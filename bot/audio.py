"""Playback levels and the car-listening tips.

Volume stays at or below 100 so Lavalink never boosts past unity. Boosting
above 100 clips on phone Bluetooth. No timescale, equalizer, or other filters
are applied; those live only as unused Lavalink capabilities.
"""

from __future__ import annotations

DEFAULT_VOLUME = 70
MIN_VOLUME = 0
MAX_VOLUME = 100
BITRATE_TIP_BPS = 96_000
IDLE_SECONDS = 180
ALONE_SECONDS = 180
CAR_TIP = "In the car? Mute your mic for full stereo sound."


def clamp_volume(value: int) -> int:
    """Return a player volume inside the safe range."""

    return max(MIN_VOLUME, min(MAX_VOLUME, int(value)))


def needs_bitrate_tip(bitrate: int | None) -> bool:
    """True when a Discord voice channel is below 96 kbps."""

    return bitrate is not None and bitrate < BITRATE_TIP_BPS


def format_kbps(bitrate: int) -> int:
    return max(bitrate, 0) // 1000
