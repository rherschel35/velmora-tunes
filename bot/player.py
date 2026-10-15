"""Voice player with a 70 default volume and idle or alone disconnects."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
import wavelink

from bot.audio import ALONE_SECONDS, DEFAULT_VOLUME, IDLE_SECONDS

if TYPE_CHECKING:
    Announce = Callable[[discord.abc.Messageable, str], Awaitable[None]]

log = logging.getLogger(__name__)


class HousePlayer(wavelink.Player):
    """Guild player. Volume starts at 70 and no filters are enabled."""

    def __init__(
        self,
        client: discord.Client,
        channel: discord.abc.Connectable,
        *,
        nodes: list[wavelink.Node] | None = None,
    ) -> None:
        super().__init__(client, channel, nodes=nodes)
        # Wavelink starts at 100. Play requests send this value, so set it
        # before the first track or the car speakers get a hotter signal.
        self._volume = DEFAULT_VOLUME
        self.text_channel: discord.abc.Messageable | None = None
        self.notice: discord.Message | None = None
        self.announce: Announce | None = None
        self.closing = False
        self._idle_task: asyncio.Task[None] | None = None
        self._alone_task: asyncio.Task[None] | None = None

    def listener_count(self) -> int:
        channel = self.channel
        if channel is None:
            return 0
        members = getattr(channel, "members", ())
        return sum(1 for member in members if not getattr(member, "bot", False))

    def cancel_timers(self) -> None:
        self.cancel_idle()
        self.cancel_alone()

    def cancel_idle(self) -> None:
        task = self._idle_task
        if task is not None and not task.done():
            task.cancel()
        self._idle_task = None

    def cancel_alone(self) -> None:
        task = self._alone_task
        if task is not None and not task.done():
            task.cancel()
        self._alone_task = None

    def arm_idle(self) -> None:
        if self.closing or self.current is not None or not self.queue.is_empty:
            return
        if self.queue.mode is not wavelink.QueueMode.normal:
            return
        if self._idle_task is not None and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_wait())

    def sync_alone(self) -> None:
        if self.closing or not self.connected:
            self.cancel_alone()
            return
        if self.listener_count() > 0:
            self.cancel_alone()
            return
        if self._alone_task is not None and not self._alone_task.done():
            return
        self._alone_task = asyncio.create_task(self._alone_wait())

    async def _idle_wait(self) -> None:
        try:
            await asyncio.sleep(IDLE_SECONDS)
        except asyncio.CancelledError:
            return
        if self.closing or not self.connected:
            return
        if self.current is not None or not self.queue.is_empty:
            return
        if self.queue.mode is not wavelink.QueueMode.normal:
            return
        await self.shutdown("idle")

    async def _alone_wait(self) -> None:
        try:
            await asyncio.sleep(ALONE_SECONDS)
        except asyncio.CancelledError:
            return
        if self.closing or not self.connected:
            return
        if self.listener_count() > 0:
            return
        await self.shutdown("alone")

    async def shutdown(self, reason: str) -> None:
        if self.closing:
            return
        self.closing = True
        self.cancel_timers()
        channel = self.text_channel
        callback = self.announce
        try:
            await super().disconnect()
        except Exception:
            log.exception("Player disconnect failed")
        if callback is not None and channel is not None:
            try:
                await callback(channel, reason)
            except Exception:
                log.exception("Departure notice failed")

    async def disconnect(self, **kwargs: object) -> None:
        self.closing = True
        self.cancel_timers()
        await super().disconnect(**kwargs)
