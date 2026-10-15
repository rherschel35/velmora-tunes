"""House colors taken from Discord role names."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class House:
    key: str
    name: str
    color: int
    line: str


NEUTRAL = House(
    key="neutral",
    name="Velmora",
    color=0x4A3F55,
    line="The east-hall wireless takes the request.",
)

HOUSES: tuple[House, ...] = (
    House(
        key="asterwilde",
        name="Asterwilde",
        color=0x3E5F8A,
        line="Silver strings answer from the blue gallery.",
    ),
    House(
        key="oakmont",
        name="Oakmont",
        color=0x6C3483,
        line="Oak and violet fire take the melody.",
    ),
    House(
        key="caldrin",
        name="Caldrin",
        color=0xC4A35A,
        line="A gold thread runs through the dark.",
    ),
    House(
        key="veyren",
        name="Veyren",
        color=0x9B2335,
        line="Red banners stir and the chorus steps forward.",
    ),
    House(
        key="thornmere",
        name="Thornmere",
        color=0x1F7A4D,
        line="Green and silver leaves keep time.",
    ),
)

_BY_KEY = {house.key: house for house in HOUSES}


def house_by_key(key: str | None) -> House:
    if not key:
        return NEUTRAL
    return _BY_KEY.get(key, NEUTRAL)


def house_from_role_names(names: list[str]) -> House:
    """Pick a house from role names. Earlier names win, so pass highest role first."""

    for name in names:
        lowered = name.lower()
        for house in HOUSES:
            if house.key in lowered:
                return house
    return NEUTRAL


def house_from_member(member: object) -> House:
    roles = getattr(member, "roles", None)
    if not roles:
        return NEUTRAL
    ordered = sorted(roles, key=lambda role: getattr(role, "position", 0), reverse=True)
    return house_from_role_names([getattr(role, "name", "") for role in ordered])
