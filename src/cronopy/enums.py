from __future__ import annotations

from enum import IntEnum, StrEnum


class Source(StrEnum):
    """Food source filter accepted by the search endpoint."""

    ALL = "All"


class DiaryGroup(IntEnum):
    """Diary category. Stored in the high 16 bits of a serving's ``order`` field."""

    UNCATEGORIZED = 0
    BREAKFAST = 1
    LUNCH = 2
    DINNER = 3
    SNACKS = 4

    @classmethod
    def parse(cls, name: str) -> DiaryGroup:
        return cls[name.strip().upper()]

    @classmethod
    def for_hour(cls, hour: int) -> DiaryGroup:
        """Meal group the mobile app picks for a given hour of the day."""
        if 4 <= hour < 10:
            return cls.BREAKFAST
        if 10 <= hour < 14:
            return cls.LUNCH
        if 14 <= hour < 21:
            return cls.DINNER
        return cls.SNACKS
