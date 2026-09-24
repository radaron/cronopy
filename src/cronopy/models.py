"""Data types returned by :class:`cronopy.client.CronometerClient`."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Source(StrEnum):
    """Food source filter accepted by the search endpoint."""

    ALL = "All"


@dataclass(frozen=True)
class CalorieSummary:
    """Energy balance for one day, in kcal.

    ``burned`` is BMR + baseline activity + exercise. ``target`` is the
    custom energy target if one is set, otherwise ``burned`` plus the
    (negative for weight loss) ``weight_goal_adjustment``. ``remaining`` is
    ``target - consumed`` (negative when over). ``tef`` (thermic effect of
    food) is reported separately and not included, matching the web UI.
    """

    day: dt.date
    consumed: float
    bmr: float
    activity: float
    exercise: float
    tef: float
    weight_goal_adjustment: float = 0.0
    custom_target: float | None = None

    @property
    def burned(self) -> float:
        return self.bmr + self.activity + self.exercise

    @property
    def target(self) -> float:
        if self.custom_target is not None:
            return self.custom_target + self.exercise
        return self.burned + self.weight_goal_adjustment

    @property
    def remaining(self) -> float:
        return self.target - self.consumed

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "consumed": self.consumed,
            "bmr": self.bmr,
            "activity": self.activity,
            "exercise": self.exercise,
            "tef": self.tef,
            "burned": self.burned,
            "weight_goal_adjustment": self.weight_goal_adjustment,
            "custom_target": self.custom_target,
            "target": self.target,
            "remaining": self.remaining,
        }
