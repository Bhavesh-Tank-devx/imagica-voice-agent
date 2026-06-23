"""
benchmark/scenarios/generic_english.py — a generic English booking/support task
covering the major call situations. Provider-neutral so it doubles as a
reusable benchmark for future projects (not tied to Kaya/Imagicaa).

The "agent" here is a generic appointment/support assistant for a fictional
service ("Acme Care") with the SAME tool set as Kaya (get_closest_branches,
book_appointment, schedule_callback, transfer_to_human, mark_not_interested,
end_call), so the shared execute_tool path and scorers apply unchanged.
Gold branches reuse kaya_branches city data for ground-truth assertions.
"""
from __future__ import annotations

from .schema import Gold, Persona, Scenario

SCENARIOS: list[Scenario] = [
    Scenario(
        id="gen_happy_path",
        task="generic",
        category="happy_path",
        persona=Persona(
            description="A customer in Chennai who wants to book a consultation and knows exactly what they want.",
            goal="Book at the Adyar branch for Monday afternoon.",
            language="english",
            facts={
                "name": "David Thomas", "first_name": "David", "last_name": "Thomas",
                "email": "david.thomas@gmail.com", "phone": "+919440001122",
                "city": "Chennai", "pincode": "600020",
                "branch": "Adyar", "date": "2026-06-15", "time": "14:00",
                "concern": "general consultation",
            },
            style="clear, direct",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="Adyar",
            expected_slots={"email": "david.thomas@gmail.com", "branch_name": "Adyar"},
        ),
    ),
    Scenario(
        id="gen_objection_time",
        task="generic",
        category="objection",
        persona=Persona(
            description="A customer who initially says they're too busy and pushes back before agreeing to a callback.",
            goal="Decline now, accept a callback tomorrow evening.",
            language="english",
            facts={"name": "Sara Lee", "phone": "+919440003344", "city": "Pune"},
            style="reluctant, time-pressured",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CALLBACK_SCHEDULED",
            expected_tool="schedule_callback",
        ),
    ),
    Scenario(
        id="gen_wrong_number",
        task="generic",
        category="wrong_number",
        persona=Persona(
            description="Someone who never signed up and asks to be removed from the list.",
            goal="Get off the call, confirm not interested.",
            language="english",
            facts={"name": "Unknown"},
            style="firm, polite refusal",
        ),
        gold=Gold(
            should_complete=False,
            expected_disposition="NOT_INTERESTED",
            expected_tool="mark_not_interested",
        ),
    ),
    Scenario(
        id="gen_ambiguous",
        task="generic",
        category="ambiguous",
        persona=Persona(
            description="A customer who says 'I need help with something on my account' without specifics.",
            goal="Reveal, after clarifying questions, that they want to reschedule and need a human.",
            language="english",
            facts={"name": "Imran Q", "phone": "+919440005566", "city": "Delhi"},
            style="vague, needs prompting",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="TRANSFERRED_TO_HUMAN",
            expected_tool="transfer_to_human",
        ),
    ),
    Scenario(
        id="gen_asr_stress_digits",
        task="generic",
        category="asr_stress",
        persona=Persona(
            description="A customer reading out a long alphanumeric reference and a pincode that ASR easily corrupts.",
            goal="Book at Whitefield, ensuring pincode and email are captured exactly.",
            language="english",
            facts={
                "name": "Karthik R", "first_name": "Karthik", "last_name": "Rao",
                "email": "karthik.r2026@gmail.com", "phone": "+919440007788",
                "city": "Bengaluru", "pincode": "560066",
                "branch": "Whitefield", "date": "2026-06-19", "time": "17:30",
                "concern": "consultation",
            },
            style="reads digits individually, spells email",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="Whitefield",
            expected_slots={"email": "karthik.r2026@gmail.com", "pincode": "560066",
                            "branch_name": "Whitefield"},
        ),
    ),
    Scenario(
        id="gen_interruption",
        task="generic",
        category="interruption",
        persona=Persona(
            description="A customer who repeatedly cuts the agent off to speed things up.",
            goal="Book at T. Nagar fast, interrupting greetings and confirmations.",
            language="english",
            facts={
                "name": "Megha Iyer", "first_name": "Megha", "last_name": "Iyer",
                "email": "megha.iyer@gmail.com", "phone": "+919440009900",
                "city": "Chennai", "pincode": "600017",
                "branch": "T. Nagar", "date": "2026-06-20", "time": "11:30",
            },
            style="impatient, talks over the agent",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="T. Nagar",
        ),
    ),
    Scenario(
        id="gen_silence",
        task="generic",
        category="silence",
        persona=Persona(
            description="A customer who gives one-word answers and frequently goes quiet.",
            goal="Provide minimal info; outcome uncertain.",
            language="english",
            facts={"name": "Quiet Caller", "phone": "+919440001212", "city": "Kolkata"},
            style="monosyllabic, long pauses",
        ),
        gold=Gold(should_complete=False, expected_disposition="CALL_COMPLETED_NO_OUTCOME"),
        notes="Silence-Rate test (T3).",
    ),
    Scenario(
        id="gen_off_topic",
        task="generic",
        category="off_topic",
        persona=Persona(
            description="A customer who asks unrelated questions (weather, sports) to test rail-keeping.",
            goal="Distract the agent; see if it redirects without hallucinating.",
            language="english",
            facts={"name": "Off Topic", "phone": "+919440003434"},
            style="chatty, off-topic",
        ),
        gold=Gold(should_complete=False, expected_disposition="CALL_COMPLETED_NO_OUTCOME"),
        notes="Scope-adherence / hallucination test.",
    ),
]
