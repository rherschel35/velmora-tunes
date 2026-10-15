"""Slash commands for Velmora House Tunes."""

from __future__ import annotations

import asyncio
import enum
import logging
from typing import cast

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from bot.audio import CAR_TIP, DEFAULT_VOLUME, MAX_VOLUME, MIN_VOLUME, clamp_volume, needs_bitrate_tip
from bot.embeds import audio_tip, bitrate_tip, notice, now_playing, queue_page
from bot.houses import House, house_from_member
from bot.player import HousePlayer
from bot.safety import is_http_url, url_is_allowed
from bot.ui import fresh_queue_view, playing_view

log = logging.getLogger(__name__)

MAX_PLAYLIST = 100
_SEARCH_TIMEOUT = 20.0
_AUTOCOMPLETE_TIMEOUT = 2.5


class LoopChoice(enum.Enum):
    off = "off"
    track = "track"
    queue = "queue"


class SongSource(enum.Enum):
    youtube = "youtube"
    soundcloud = "soundcloud"


_LOOP_MODES = {
    LoopChoice.off: wavelink.QueueMode.normal,
    LoopChoice.track: wavelink.QueueMode.loop,
    LoopChoice.queue: wavelink.QueueMode.loop_all,
}


def _voice_channel(member: discord.abc.User) -> discord.VoiceChannel | discord.StageChannel | None:
    voice = getattr(member, "voice", None)
    channel = getattr(voice, "channel", None)
    if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        return channel
    return None


def _node_ready() -> bool:
    return any(node.status is wavelink.NodeStatus.CONNECTED for node in wavelink.Pool.nodes.values())


def _stamp(track: wavelink.Playable, member: discord.abc.User, house: House) -> None:
    track.extras = {
        "requester_id": str(member.id),
        "requester_name": member.display_name[:80],
        "house_key": house.key,
    }


def _will_continue(player: HousePlayer) -> bool:
    mode = player.queue.mode
    if mode is wavelink.QueueMode.loop and player.queue.loaded is not None:
        return True
    history = player.queue.history
    if mode is wavelink.QueueMode.loop_all and (not player.queue.is_empty or (history is not None and not history.is_empty)):
        return True
    return not player.queue.is_empty


def _next_loop(mode: wavelink.QueueMode) -> wavelink.QueueMode:
    if mode is wavelink.QueueMode.normal:
        return wavelink.QueueMode.loop
    if mode is wavelink.QueueMode.loop:
        return wavelink.QueueMode.loop_all
    return wavelink.QueueMode.normal


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tipped_channels: set[int] = set()

    async def _ephemeral(self, interaction: discord.Interaction, text: str, house: House) -> None:
        embed = notice(text, house)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _public(self, interaction: discord.Interaction, embed: discord.Embed) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

    def _in_bot_channel(self, member: discord.abc.User, player: HousePlayer) -> bool:
        channel = _voice_channel(member)
        return channel is not None and player.channel is not None and channel.id == player.channel.id

    async def _control_player(self, interaction: discord.Interaction) -> HousePlayer | None:
        house = house_from_member(interaction.user)
        guild = interaction.guild
        player = guild.voice_client if guild is not None else None
        if not isinstance(player, HousePlayer) or not player.connected or player.closing:
            await self._ephemeral(interaction, "The gramophone is not in a voice channel.", house)
            return None
        if not self._in_bot_channel(interaction.user, player):
            await self._ephemeral(
                interaction,
                "Step into the same chamber as the gramophone before you change the music.",
                house,
            )
            return None
        return player

    async def _announce(self, channel: discord.abc.Messageable, reason: str) -> None:
        if reason == "alone":
            text = "No one remains in the chamber. After three quiet minutes the gramophone hushes and leaves."
        else:
            text = "Three quiet minutes have passed with no song. Velmora House Tunes steps out of the hall."
        await channel.send(embed=notice(text))

    async def _refresh_stored(self, player: HousePlayer) -> None:
        message = player.notice
        if message is None:
            return
        view = playing_view(player) if player.current is not None else None
        try:
            await message.edit(embed=now_playing(player), view=view)
        except discord.HTTPException:
            player.notice = None

    async def _show_card(self, player: HousePlayer) -> None:
        channel = player.text_channel
        embed = now_playing(player)
        view = playing_view(player) if player.current is not None else None
        if player.notice is not None:
            try:
                await player.notice.edit(embed=embed, view=view)
                return
            except discord.HTTPException:
                player.notice = None
        if channel is None:
            return
        try:
            player.notice = await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            log.warning("Could not post the now-playing card")

    async def _ack_card(self, interaction: discord.Interaction, player: HousePlayer) -> None:
        embed = now_playing(player)
        view = playing_view(player) if player.current is not None else None
        if interaction.response.is_done():
            if interaction.message is not None:
                await interaction.message.edit(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
        if interaction.message is not None:
            player.notice = interaction.message

    async def maybe_bitrate_tip(
        self,
        voice: discord.abc.GuildChannel,
        text: discord.abc.Messageable | None,
        house: House,
    ) -> None:
        if text is None or voice.id in self._tipped_channels:
            return
        bitrate = getattr(voice, "bitrate", None)
        if not needs_bitrate_tip(bitrate):
            return
        embed = bitrate_tip(getattr(voice, "name", "This channel"), int(bitrate), house)
        try:
            await text.send(embed=embed)
        except discord.HTTPException:
            log.warning("Could not post the bitrate tip")
            return
        self._tipped_channels.add(voice.id)

    async def _connect(self, interaction: discord.Interaction, house: House) -> HousePlayer | None:
        voice = _voice_channel(interaction.user)
        if voice is None:
            await self._ephemeral(interaction, "Join a voice channel, then ask the hall for a song.", house)
            return None
        if not _node_ready():
            await self._ephemeral(
                interaction,
                "The wireless has not answered yet. Lavalink is still waking.",
                house,
            )
            return None

        guild = interaction.guild
        assert guild is not None
        existing = guild.voice_client
        created = False
        if isinstance(existing, HousePlayer) and existing.connected and not existing.closing:
            if existing.channel is not None and existing.channel.id != voice.id:
                name = getattr(existing.channel, "name", "another chamber")
                await self._ephemeral(
                    interaction,
                    f"The gramophone is already in **{name}**. Join it, or ask someone there to use /leave.",
                    house,
                )
                return None
            player = existing
        else:
            try:
                player = await voice.connect(cls=HousePlayer, self_deaf=True)
            except discord.ClientException:
                await self._ephemeral(interaction, "I could not step into that voice channel.", house)
                return None
            except wavelink.ChannelTimeoutException:
                await self._ephemeral(interaction, "The chamber door did not open in time. Try again.", house)
                return None
            created = True
            player.autoplay = wavelink.AutoPlayMode.partial

        player = cast(HousePlayer, player)
        if isinstance(interaction.channel, discord.abc.Messageable):
            player.text_channel = interaction.channel
        player.announce = self._announce
        player.sync_alone()
        if created:
            await self.maybe_bitrate_tip(voice, player.text_channel, house)
        return player

    async def _search(self, query: str, source: SongSource) -> wavelink.Search:
        cleaned = query.strip()
        if is_http_url(cleaned):
            if not await url_is_allowed(cleaned):
                raise ValueError("blocked-url")
            return await wavelink.Playable.search(cleaned)
        track_source = (
            wavelink.TrackSource.SoundCloud if source is SongSource.soundcloud else wavelink.TrackSource.YouTube
        )
        return await wavelink.Playable.search(cleaned, source=track_source)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = payload.player
        if not isinstance(player, HousePlayer) or player.closing:
            return
        player.cancel_idle()
        await self._show_card(player)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player
        if not isinstance(player, HousePlayer) or player.closing:
            return
        if payload.reason == "replaced":
            return
        if _will_continue(player):
            player.cancel_idle()
            return
        player.arm_idle()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        player = member.guild.voice_client
        if not isinstance(player, HousePlayer) or player.closing:
            return
        current_id = player.channel.id if player.channel is not None else None
        before_id = before.channel.id if before.channel is not None else None
        after_id = after.channel.id if after.channel is not None else None
        if current_id is None or current_id not in {before_id, after_id}:
            return
        player.sync_alone()

    @app_commands.command(name="play", description="Request a song by name or by a YouTube, SoundCloud, or direct link.")
    @app_commands.describe(
        query="Song name or link",
        source="Where a name search looks. Links choose their own source.",
    )
    @app_commands.guild_only()
    async def play(
        self,
        interaction: discord.Interaction,
        query: str,
        source: SongSource = SongSource.youtube,
    ) -> None:
        house = house_from_member(interaction.user)
        await interaction.response.defer()
        player = await self._connect(interaction, house)
        if player is None:
            return

        try:
            result = await asyncio.wait_for(self._search(query, source), timeout=_SEARCH_TIMEOUT)
        except asyncio.TimeoutError:
            player.arm_idle()
            await interaction.followup.send(
                embed=notice("The archives took too long to answer. Try the request again.", house)
            )
            return
        except ValueError:
            player.arm_idle()
            await interaction.followup.send(
                embed=notice("That link points somewhere this hall will not follow.", house)
            )
            return
        except wavelink.LavalinkLoadException:
            log.warning("Lavalink could not load a request")
            player.arm_idle()
            await interaction.followup.send(
                embed=notice("The wireless could not read that request.", house)
            )
            return

        if not result:
            player.arm_idle()
            await interaction.followup.send(
                embed=notice("The archives have no song under that name.", house)
            )
            return

        playlist_name: str | None = None
        truncated = False
        if isinstance(result, wavelink.Playlist):
            tracks = list(result.tracks)[:MAX_PLAYLIST]
            truncated = len(result.tracks) > MAX_PLAYLIST
            playlist_name = result.name
        else:
            tracks = [result[0]]

        if not tracks:
            player.arm_idle()
            await interaction.followup.send(embed=notice("The archives sent back an empty list.", house))
            return

        for track in tracks:
            _stamp(track, interaction.user, house)
        added = await player.queue.put_wait(tracks)

        started = False
        if player.current is None:
            try:
                await player.play(player.queue.get())
                started = True
            except wavelink.QueueEmpty:
                started = False

        if playlist_name:
            extra = " The first 100 were taken up." if truncated else ""
            text = f"The playlist **{playlist_name}** lays {added} songs on the stand.{extra}"
        elif started:
            text = f"{house.line} The hall takes up **{tracks[0].title}**."
        else:
            text = f"Added **{tracks[0].title}** to the queue."
        await interaction.followup.send(embed=notice(text, house))

    @play.autocomplete("query")
    async def play_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        cleaned = current.strip()
        if len(cleaned) < 2 or is_http_url(cleaned) or not _node_ready():
            return []
        chosen = getattr(interaction.namespace, "source", None)
        if chosen is SongSource.soundcloud or chosen == SongSource.soundcloud.value:
            source = SongSource.soundcloud
        else:
            source = SongSource.youtube
        try:
            result = await asyncio.wait_for(self._search(cleaned, source), timeout=_AUTOCOMPLETE_TIMEOUT)
        except (asyncio.TimeoutError, wavelink.LavalinkLoadException, wavelink.LavalinkException, ValueError):
            return []
        if isinstance(result, wavelink.Playlist):
            found = list(result.tracks)
        else:
            found = list(result)

        choices: list[app_commands.Choice[str]] = []
        seen_values: set[str] = set()
        seen_names: set[str] = set()
        for track in found:
            value = track.uri or track.title
            if not value or len(value) > 100 or value in seen_values:
                continue
            base = f"{track.title} — {track.author}".replace("\n", " ")
            name = base[:100] if base else value[:100]
            suffix = 2
            while name in seen_names:
                tail = f" ({suffix})"
                name = f"{base[: 100 - len(tail)]}{tail}"
                suffix += 1
            seen_names.add(name)
            seen_values.add(value)
            choices.append(app_commands.Choice(name=name, value=value))
            if len(choices) == 25:
                break
        return choices

    @app_commands.command(name="skip", description="Skip the current song.")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        if player.current is None and player.queue.is_empty:
            await self._ephemeral(interaction, "Nothing is playing.", house)
            return
        await player.skip(force=True)
        await self._public(interaction, notice("The song steps aside.", house))

    @app_commands.command(name="pause", description="Pause the gramophone.")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        if player.current is None:
            await self._ephemeral(interaction, "Nothing is playing.", house)
            return
        if player.paused:
            await self._ephemeral(interaction, "The song is already resting.", house)
            return
        await player.pause(True)
        await self._refresh_stored(player)
        await self._public(interaction, notice("The gramophone rests its hand.", house))

    @app_commands.command(name="resume", description="Resume the paused song.")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        if player.current is None or not player.paused:
            await self._ephemeral(interaction, "There is no paused song to resume.", house)
            return
        await player.pause(False)
        player.cancel_idle()
        await self._refresh_stored(player)
        await self._public(interaction, notice("The song goes on.", house))

    @app_commands.command(name="stop", description="Stop playback and clear the queue.")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        await self._stop_player(player)
        await self._refresh_stored(player)
        await self._public(interaction, notice("The queue is cleared and the hall goes quiet.", house))

    async def _stop_player(self, player: HousePlayer) -> None:
        player.queue.mode = wavelink.QueueMode.normal
        player.queue.clear()
        if player.current is not None:
            await player.skip(force=True)
        player.arm_idle()

    @app_commands.command(name="queue", description="Show the songs waiting in the hall.")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        player = guild.voice_client if guild is not None else None
        if not isinstance(player, HousePlayer) or player.closing:
            await interaction.response.send_message(embed=notice("The queue is an empty music stand."))
            return
        embed, page, pages = queue_page(player, 0)
        view = fresh_queue_view(guild.id, page, pages) if pages > 1 else None
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="nowplaying", description="Show the song that is playing.")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        player = guild.voice_client if guild is not None else None
        if not isinstance(player, HousePlayer) or player.closing or player.current is None:
            await interaction.response.send_message(embed=notice(f"The hall is quiet.\n\n{CAR_TIP}"))
            return
        await interaction.response.send_message(embed=now_playing(player), view=playing_view(player))
        try:
            player.notice = await interaction.original_response()
        except discord.HTTPException:
            pass

    @app_commands.command(name="volume", description="Set the volume from 0 to 100. The default is 70.")
    @app_commands.describe(level=f"Volume from {MIN_VOLUME} to {MAX_VOLUME}. Values above 100 are rejected.")
    @app_commands.guild_only()
    async def volume(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, MIN_VOLUME, MAX_VOLUME],
    ) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        safe = clamp_volume(level)
        await player.set_volume(safe)
        await self._refresh_stored(player)
        await self._public(
            interaction,
            notice(
                f"The hall is set to **{safe}**. It will not rise past {MAX_VOLUME}, "
                f"and a fresh visit starts at {DEFAULT_VOLUME}.",
                house,
            ),
        )

    @app_commands.command(name="shuffle", description="Shuffle the waiting songs.")
    @app_commands.guild_only()
    async def shuffle(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        if player.queue.count < 2:
            await self._ephemeral(interaction, "There are not enough waiting songs to shuffle.", house)
            return
        player.queue.shuffle()
        await self._refresh_stored(player)
        await self._public(interaction, notice("The queue is shuffled like a deck of house cards.", house))

    @app_commands.command(name="loop", description="Loop nothing, the current song, or the whole queue.")
    @app_commands.describe(mode="What should repeat")
    @app_commands.guild_only()
    async def loop(self, interaction: discord.Interaction, mode: LoopChoice) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        player.queue.mode = _LOOP_MODES[mode]
        await self._refresh_stored(player)
        labels = {
            LoopChoice.off: "The loop is off.",
            LoopChoice.track: "This song will play again when it ends.",
            LoopChoice.queue: "The whole queue will circle back.",
        }
        await self._public(interaction, notice(labels[mode], house))

    @app_commands.command(name="remove", description="Remove a waiting song by its queue position.")
    @app_commands.describe(position="Position in /queue. 1 is the next song, not the one playing.")
    @app_commands.guild_only()
    async def remove(
        self,
        interaction: discord.Interaction,
        position: app_commands.Range[int, 1, 500],
    ) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        index = position - 1
        if index >= player.queue.count:
            await self._ephemeral(
                interaction,
                "That position is empty. Position 1 is the next song, not the one already playing.",
                house,
            )
            return
        try:
            title = player.queue[index].title
            player.queue.delete(index)
        except (IndexError, wavelink.QueueEmpty):
            await self._ephemeral(interaction, "That song had already left the queue.", house)
            return
        await self._refresh_stored(player)
        await self._public(interaction, notice(f"Removed **{title}** from the queue.", house))

    @app_commands.command(name="leave", description="Disconnect and let the hall go quiet.")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        guild = interaction.guild
        player = guild.voice_client if guild is not None else None
        if not isinstance(player, HousePlayer) or not player.connected or player.closing:
            await self._ephemeral(interaction, "The gramophone is already gone.", house)
            return
        if player.listener_count() > 0 and not self._in_bot_channel(interaction.user, player):
            await self._ephemeral(interaction, "Join the chamber before you dismiss the gramophone.", house)
            return
        await player.disconnect()
        await self._public(interaction, notice("The gramophone bows and leaves the chamber.", house))

    @app_commands.command(name="audiotip", description="Why the music sounds thin in the car, and how to fix it.")
    @app_commands.guild_only()
    async def audiotip(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        await interaction.response.send_message(embed=audio_tip(house))

    async def handle_toggle(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        if player.current is None:
            await self._ephemeral(interaction, "Nothing is playing.", house)
            return
        await player.pause(not player.paused)
        if not player.paused:
            player.cancel_idle()
        await self._ack_card(interaction, player)

    async def handle_skip(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        if player.current is None and player.queue.is_empty:
            await self._ephemeral(interaction, "Nothing is playing.", house)
            return
        await player.skip(force=True)
        await self._ack_card(interaction, player)

    async def handle_stop(self, interaction: discord.Interaction) -> None:
        player = await self._control_player(interaction)
        if player is None:
            return
        await self._stop_player(player)
        embed = notice("The queue is cleared and the hall goes quiet.", house_from_member(interaction.user))
        if interaction.response.is_done():
            if interaction.message is not None:
                await interaction.message.edit(embed=embed, view=None)
        else:
            await interaction.response.edit_message(embed=embed, view=None)
        if interaction.message is not None:
            player.notice = interaction.message

    async def handle_loop_cycle(self, interaction: discord.Interaction) -> None:
        player = await self._control_player(interaction)
        if player is None:
            return
        player.queue.mode = _next_loop(player.queue.mode)
        await self._ack_card(interaction, player)

    async def handle_shuffle(self, interaction: discord.Interaction) -> None:
        house = house_from_member(interaction.user)
        player = await self._control_player(interaction)
        if player is None:
            return
        if player.queue.count < 2:
            await self._ephemeral(interaction, "There are not enough waiting songs to shuffle.", house)
            return
        player.queue.shuffle()
        await self._ack_card(interaction, player)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        log.exception("Slash command failed: %s", type(error).__name__)
        house = house_from_member(interaction.user)
        await self._ephemeral(interaction, "The enchantment snagged. Try the request once more.", house)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
