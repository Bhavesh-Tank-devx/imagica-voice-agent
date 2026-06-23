"""
benchmark/scenarios/schema.py — scenario + gold-label data model.

A Scenario is a reproducible test case: a persona with a goal, the facts they
know (used both to script the simulated caller AND to assert against), and the
GOLD outcome the agent should reach. Scorers compare the actual conversation to
`gold`. Keep gold grounded in real ground truth (e.g. kaya_branches.CITY_BRANCHES).
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Conversation-situation taxonomy — every suite should cover these.
CATEGORIES = [
    "happy_path",       # straightforward success
    "objection",        # price/time pushback before (maybe) converting
    "wrong_number",     # not the right person / not interested
    "ambiguous",        # vague request needing clarification
    "asr_stress",       # must capture spelled email / digits / dates correctly
    "interruption",     # user barges in
    "silence",          # user goes quiet / minimal responses
    "off_topic",        # out-of-scope request
]


@dataclass
class Gold:
    """Expected outcome for scoring. Fields left None are not asserted."""
    should_complete: bool                 # should the agent achieve the task goal?
    expected_disposition: str | None = None
    expected_tool: str | None = None      # the decisive tool (e.g. book_appointment)
    expected_branch: str | None = None    # kaya: the correct branch for the persona
    expected_slots: dict = field(default_factory=dict)  # canonical slot values (email, pincode, date, time…)
    expected_intent_per_turn: list[str] = field(default_factory=list)  # optional gold intent labels


@dataclass
class Persona:
    """Drives the simulated caller (sim_user) and seeds asr_stress facts."""
    description: str                      # natural-language persona for the LLM-as-user
    goal: str                             # what the caller is trying to do
    language: str = "english"             # english | hinglish | hindi
    facts: dict = field(default_factory=dict)  # name/email/city/pincode/date/time/concern the caller knows
    style: str = ""                       # e.g. "terse, interrupts", "polite, spells slowly"


@dataclass
class Scenario:
    id: str
    task: str                             # "kaya" | "generic"
    category: str
    persona: Persona
    gold: Gold
    max_turns: int = 14
    notes: str = ""

    def cart(self) -> dict:
        """Minimal cart dict the stacks/tools expect (mirrors production cart)."""
        f = self.persona.facts
        return {
            "cart_id": self.id,
            "customer_name": f.get("name", "Customer"),
            "customer_phone": f.get("phone", "+910000000000"),
            "city": f.get("city", ""),
            "call_type": "OUTBOUND",
            "agent_type": "kaya" if self.task == "kaya" else "imagica",
            "attempt_number": 1,
        }
