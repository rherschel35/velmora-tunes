"""Discord client and Lavalink connection."""

from __future__ import annotations

import logging

import discord
import wavelink
from discord.ext import commands

from bot.audio import CAR_TIP
from bot.config import Settings
from bot.cogs.music import Music
from bot.ui import NowPlayingView

log = logging.getLogger(__name__)


class VelmoraBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.settings = settings

    async def setup_hook(self) -> None:
        await self.add_cog(Music(self))
        self.add_view(NowPlayingView())
        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %s slash commands to guild %s", len(synced), self.settings.guild_id)
        self.loop.create_task(self._connect_lavalink())

    async def _connect_lavalink(self) -> None:
        """Keep trying Lavalink without blocking the Discord login."""

        while not self.is_closed():
            node = wavelink.Node(
                identifier="velmora",
                uri=self.settings.lavalink_uri,
                password=self.settings.lavalink_password,
                retries=None,
                inactive_player_timeout=None,
                inactive_channel_tokens=None,
            )
            log.info("Connecting to Lavalink at %s", self.settings.lavalink_host)
            await wavelink.Pool.connect(nodes=[node], client=self, cache_capacity=100)
            if await self._wait_until_node_ready():
                log.info("Lavalink node is ready")
                return
            log.error("Lavalink did not become ready. Retrying in 15 seconds.")
            await discord.utils.sleep_until(discord.utils.utcnow())  # placeholder replaced below

    async def _wait_until_node_ready(self) -> bool:
        for _ in range(50):
            if any(node.status is wavelink.NodeStatus.CONNECTED for node in wavelink.Pool.nodes.values()):
                return True
            await __import__("asyncio").sleep(0.1)
        return False

    async def on_ready(self) -> None:
        user = self.user
        log.info("Logged in as %s (%s)", getattr(user, "name", "bot"), getattr(user, "id", "unknown"))

    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        log.info("Lavalink session ready on %s (resumed=%s)", payload.node.identifier, payload.resumed)


# Imported for the car-tip constant so a missing footer string fails at import in reviews.
_ = CAR_TIP
