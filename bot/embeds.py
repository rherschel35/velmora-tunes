"""House-colored embeds for the hall."""

from __future__ import annotations

import discord
import wavelink

from bot.audio import CAR_TIP, format_kbps
from bot.houses import House, NEUTRAL, house_by_key

QUEUE_PAGE_SIZE = 10


def notice(text: str, house: House = NEUTRAL) -> discord.Embed:
    return discord.Embed(description=text, color=house.color)


def format_duration(milliseconds: int) -> str:
    if milliseconds <= 0:
        return "0:00"
    seconds = milliseconds // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def track_extra(track: wavelink.Playable, key: str, default: str = "") -> str:
    try:
        value = dict(track.extras).get(key, default)
    except (TypeError, ValueError):
        return default
    if value is None:
        return default
    return str(value)


def track_house(track: wavelink.Playable | None) -> House:
    if track is None:
        return NEUTRAL
    return house_by_key(track_extra(track, "house_key") or None)


def requester_line(track: wavelink.Playable) -> str:
    requester_id = track_extra(track, "requester_id")
    name = track_extra(track, "requester_name", "someone in the hall")
    if requester_id.isdigit():
        return f"<@{requester_id}> ({name})"
    return name


def loop_label(mode: wavelink.QueueMode) -> str:
    if mode is wavelink.QueueMode.loop:
        return "This song"
    if mode is wavelink.QueueMode.loop_all:
        return "The queue"
    return "Off"


def _artwork(embed: discord.Embed, track: wavelink.Playable) -> None:
    artwork = track.artwork
    if isinstance(artwork, str) and artwork.startswith(("http://", "https://")):
        embed.set_thumbnail(url=artwork)


def now_playing(player: wavelink.Player) -> discord.Embed:
    track = player.current
    house = track_house(track)
    if track is None:
        embed = discord.Embed(
            title="The hall is quiet",
            description="Nothing is on the gramophone.",
            color=house.color,
        )
        embed.set_footer(text=CAR_TIP)
        return embed

    title = track.title or "Untitled"
    author = track.author or "Unknown"
    description = f"{house.line}\n\n**[{title}]({track.uri})**" if track.uri else f"{house.line}\n\n**{title}**"
    embed = discord.Embed(title="Now playing", description=description, color=house.color)
    embed.add_field(name="Artist", value=author, inline=True)
    if track.is_stream:
        length = "Live"
    else:
        length = f"{format_duration(player.position)} / {format_duration(track.length)}"
    embed.add_field(name="Time", value=length, inline=True)
    embed.add_field(name="Volume", value=str(player.volume), inline=True)
    embed.add_field(name="Requested by", value=requester_line(track), inline=True)
    embed.add_field(name="House", value=house.name, inline=True)
    embed.add_field(name="Loop", value=loop_label(player.queue.mode), inline=True)
    waiting = player.queue.count
    nxt = "The queue is clear."
    if waiting:
        upcoming = player.queue[0]
        nxt = f"{waiting} waiting. Next: {upcoming.title}"
    embed.add_field(name="Queue", value=nxt[:1024], inline=False)
    _artwork(embed, track)
    embed.set_footer(text=CAR_TIP)
    return embed


def queue_page(player: wavelink.Player, page: int) -> tuple[discord.Embed, int, int]:
    house = track_house(player.current)
    tracks = list(player.queue)
    pages = max(1, (len(tracks) + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * QUEUE_PAGE_SIZE
    chunk = tracks[start : start + QUEUE_PAGE_SIZE]

    current = player.current
    if current is None:
        now = "Nothing is playing."
    else:
        now = f"**{current.title}** — {current.author}"

    if chunk:
        lines = []
        for offset, track in enumerate(chunk, start=start + 1):
            who = track_extra(track, "requester_name", "the hall")
            lines.append(f"`{offset}.` **{track.title}** — {track.author} · {who}")
        body = "\n".join(lines)
    else:
        body = "No songs are waiting."

    embed = discord.Embed(
        title="The queue",
        description=f"Now: {now}\n\n{body}",
        color=house.color,
    )
    embed.set_footer(text=f"Page {page + 1} of {pages} · {CAR_TIP}")
    return embed, page, pages


def bitrate_tip(channel_name: str, bitrate: int, house: House) -> discord.Embed:
    kbps = format_kbps(bitrate)
    embed = discord.Embed(
        title="The chamber is a little narrow",
        description=(
            f"**{channel_name}** is set to **{kbps} kbps**, under the 96 kbps this music needs. "
            "Discord squeezes the sound before it reaches a phone. A staff member can raise it: "
            "edit the voice channel, open **Bitrate**, and set it to **96 kbps or higher**. "
            "How high it can go depends on the server's boost level."
        ),
        color=house.color,
    )
    embed.set_footer(text=CAR_TIP)
    return embed


def audio_tip(house: House) -> discord.Embed:
    embed = discord.Embed(
        title="Listening from the road",
        description=(
            f"**{CAR_TIP}**\n\n"
            "Discord keeps the mix in stereo only while your microphone is closed. "
            "An open mic folds the hall into mono, and phone Bluetooth makes that sound thin.\n\n"
            "Velmora House Tunes also plays at volume **70** and will not go past **100**, "
            "so the signal is not boosted into clipping. No equalizer or other filters are applied.\n\n"
            "If a voice channel is under **96 kbps**, the bot asks staff once, when it joins, "
            "to raise that channel's bitrate."
        ),
        color=house.color,
    )
    return embed
