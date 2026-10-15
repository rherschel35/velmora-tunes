"""Buttons on the now-playing card and the queue pages."""

from __future__ import annotations

import discord

from bot.embeds import now_playing, queue_page


def _loop_name(player: object) -> str:
    mode = getattr(getattr(player, "queue", None), "mode", None)
    value = getattr(mode, "name", "normal")
    if value == "loop":
        return "track"
    if value == "loop_all":
        return "queue"
    return "off"


class NowPlayingView(discord.ui.View):
    """Pause, skip, stop, loop, and shuffle. Persistent across restarts."""

    def __init__(self, *, paused: bool = False, loop_name: str = "off") -> None:
        super().__init__(timeout=None)
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "velmora:toggle":
                child.label = "Resume" if paused else "Pause"
                child.emoji = "▶️" if paused else "⏸️"
            elif child.custom_id == "velmora:loop":
                child.label = f"Loop {loop_name}"

    async def _cog(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Music")
        if cog is None:
            await interaction.response.send_message(
                "The gramophone is not awake yet.",
                ephemeral=True,
            )
        return cog

    @discord.ui.button(
        label="Pause",
        style=discord.ButtonStyle.secondary,
        custom_id="velmora:toggle",
        emoji="⏸️",
        row=0,
    )
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        cog = await self._cog(interaction)
        if cog is not None:
            await cog.handle_toggle(interaction)

    @discord.ui.button(
        label="Skip",
        style=discord.ButtonStyle.primary,
        custom_id="velmora:skip",
        emoji="⏭️",
        row=0,
    )
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        cog = await self._cog(interaction)
        if cog is not None:
            await cog.handle_skip(interaction)

    @discord.ui.button(
        label="Stop",
        style=discord.ButtonStyle.danger,
        custom_id="velmora:stop",
        emoji="⏹️",
        row=0,
    )
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        cog = await self._cog(interaction)
        if cog is not None:
            await cog.handle_stop(interaction)

    @discord.ui.button(
        label="Loop off",
        style=discord.ButtonStyle.secondary,
        custom_id="velmora:loop",
        emoji="🔁",
        row=0,
    )
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        cog = await self._cog(interaction)
        if cog is not None:
            await cog.handle_loop_cycle(interaction)

    @discord.ui.button(
        label="Shuffle",
        style=discord.ButtonStyle.secondary,
        custom_id="velmora:shuffle",
        emoji="🔀",
        row=0,
    )
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        cog = await self._cog(interaction)
        if cog is not None:
            await cog.handle_shuffle(interaction)


def playing_view(player: object) -> NowPlayingView:
    paused = bool(getattr(player, "paused", False))
    return NowPlayingView(paused=paused, loop_name=_loop_name(player))


class QueueView(discord.ui.View):
    def __init__(self, guild_id: int, page: int, *, has_prev: bool, has_next: bool) -> None:
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.page = page
        self.previous_page = discord.ui.Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            disabled=not has_prev,
            emoji="◀️",
        )
        self.next_page = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.secondary,
            disabled=not has_next,
            emoji="▶️",
        )
        self.previous_page.callback = self.on_previous
        self.next_page.callback = self.on_next
        self.add_item(self.previous_page)
        self.add_item(self.next_page)

    async def _turn(self, interaction: discord.Interaction, delta: int) -> None:
        guild = interaction.guild
        player = guild.voice_client if guild is not None else None
        if player is None or not hasattr(player, "queue"):
            await interaction.response.edit_message(
                embed=discord.Embed(description="The queue has been cleared.", color=0x4A3F55),
                view=None,
            )
            return
        embed, page, pages = queue_page(player, self.page + delta)
        view = QueueView(
            self.guild_id,
            page,
            has_prev=page > 0,
            has_next=page < pages - 1,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_previous(self, interaction: discord.Interaction) -> None:
        await self._turn(interaction, -1)

    async def on_next(self, interaction: discord.Interaction) -> None:
        await self._turn(interaction, 1)


def fresh_queue_view(guild_id: int, page: int, pages: int) -> QueueView:
    return QueueView(guild_id, page, has_prev=page > 0, has_next=page < pages - 1)


def refresh_embed(player: object) -> discord.Embed:
    return now_playing(player)  # type: ignore[arg-type]
