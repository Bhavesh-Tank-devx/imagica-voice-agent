"""Scenario registry."""
from __future__ import annotations

from . import generic_english, kaya_hinglish
from .schema import CATEGORIES, Gold, Persona, Scenario

ALL_SCENARIOS: list[Scenario] = [*kaya_hinglish.SCENARIOS, *generic_english.SCENARIOS]
BY_ID: dict[str, Scenario] = {s.id: s for s in ALL_SCENARIOS}


def get(scenario_id: str) -> Scenario:
    return BY_ID[scenario_id]


def by_task(task: str) -> list[Scenario]:
    return [s for s in ALL_SCENARIOS if s.task == task]
