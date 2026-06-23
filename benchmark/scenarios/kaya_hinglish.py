"""
benchmark/scenarios/kaya_hinglish.py — Kaya Clinic appointment-booking scenarios
in Hinglish. Gold branches are grounded in kaya_branches.CITY_BRANCHES so the
scorer can assert the agent booked the CORRECT branch for the persona's city.
"""
from __future__ import annotations

from .schema import Gold, Persona, Scenario

# Convention: gold expected_branch values are exact strings from CITY_BRANCHES.

SCENARIOS: list[Scenario] = [
    Scenario(
        id="kaya_happy_path",
        task="kaya",
        category="happy_path",
        persona=Persona(
            description="Neha, a working professional in Bengaluru who filled the Kaya form for acne-scar consultation and is keen to book.",
            goal="Book an appointment at the Koramangala branch for this Saturday morning.",
            language="hinglish",
            facts={
                "name": "Neha Sharma", "first_name": "Neha", "last_name": "Sharma",
                "email": "neha.sharma@gmail.com", "phone": "+919900112233",
                "city": "Bengaluru", "pincode": "560034",
                "branch": "Koramangala", "date": "2026-06-13", "time": "11:00",
                "concern": "acne scars",
            },
            style="cooperative, mixes Hindi and English naturally",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="Koramangala",
            expected_slots={"email": "neha.sharma@gmail.com", "branch_name": "Koramangala",
                            "appointment_date": "2026-06-13", "appointment_time": "11:00"},
        ),
    ),
    Scenario(
        id="kaya_objection_price",
        task="kaya",
        category="objection",
        persona=Persona(
            description="Rohit in Mumbai, interested in laser hair reduction but worried about consultation cost.",
            goal="Get reassurance on pricing, then book at the Bandra branch if satisfied.",
            language="hinglish",
            facts={
                "name": "Rohit Mehra", "first_name": "Rohit", "last_name": "Mehra",
                "email": "rohit.mehra@yahoo.com", "phone": "+919812345678",
                "city": "Mumbai", "pincode": "400050",
                "branch": "Bandra", "date": "2026-06-14", "time": "16:00",
                "concern": "laser hair reduction",
            },
            style="skeptical first, raises price objection, then warms up",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="Bandra",
            expected_slots={"branch_name": "Bandra"},
        ),
    ),
    Scenario(
        id="kaya_wrong_number",
        task="kaya",
        category="wrong_number",
        persona=Persona(
            description="A person who never filled any Kaya form and is annoyed by the call.",
            goal="End the call quickly; not interested.",
            language="hinglish",
            facts={"name": "Unknown", "city": ""},
            style="curt, says galat number / not interested",
        ),
        gold=Gold(
            should_complete=False,
            expected_disposition="NOT_INTERESTED",
            expected_tool="mark_not_interested",
        ),
    ),
    Scenario(
        id="kaya_ambiguous_city",
        task="kaya",
        category="ambiguous",
        persona=Persona(
            description="Anjali who knows she wants skin consultation but is vague about location at first ('somewhere near my office').",
            goal="Eventually reveal she is in Surat and book at Vesu.",
            language="hinglish",
            facts={
                "name": "Anjali Patel", "first_name": "Anjali", "last_name": "Patel",
                "email": "anjali.patel@gmail.com", "phone": "+919898989898",
                "city": "Surat", "pincode": "395007",
                "branch": "Vesu", "date": "2026-06-15", "time": "10:30",
                "concern": "pigmentation",
            },
            style="vague initially, needs the agent to ask clarifying questions",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="Vesu",
            expected_slots={"branch_name": "Vesu"},
        ),
    ),
    Scenario(
        id="kaya_asr_stress_email",
        task="kaya",
        category="asr_stress",
        persona=Persona(
            description="Vikram with a tricky email/surname that ASR commonly mangles; he spells it out.",
            goal="Book at Indiranagar, ensuring his email is captured correctly despite spelling.",
            language="hinglish",
            facts={
                "name": "Vikram Krishnamurthy", "first_name": "Vikram", "last_name": "Krishnamurthy",
                "email": "vikram.k99@gmail.com", "phone": "+919700001111",
                "city": "Bengaluru", "pincode": "560038",
                "branch": "Indiranagar", "date": "2026-06-16", "time": "18:00",
                "concern": "hair fall",
            },
            style="spells email letter-by-letter, reads digits one at a time",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="Indiranagar",
            expected_slots={"email": "vikram.k99@gmail.com", "branch_name": "Indiranagar"},
        ),
        notes="The discriminating test: ASR + spell-back must yield the exact email.",
    ),
    Scenario(
        id="kaya_interruption",
        task="kaya",
        category="interruption",
        persona=Persona(
            description="Busy Priyanka in Delhi who interrupts the agent's long sentences to hurry things along.",
            goal="Book at Green Park quickly, barging in to skip pleasantries.",
            language="hinglish",
            facts={
                "name": "Priyanka Singh", "first_name": "Priyanka", "last_name": "Singh",
                "email": "priyanka.singh@outlook.com", "phone": "+919600002222",
                "city": "Delhi", "pincode": "110016",
                "branch": "Green Park", "date": "2026-06-17", "time": "12:00",
                "concern": "skin consultation",
            },
            style="impatient, interrupts mid-sentence, short replies",
        ),
        gold=Gold(
            should_complete=True,
            expected_disposition="CONVERTED",
            expected_tool="book_appointment",
            expected_branch="Green Park",
        ),
    ),
    Scenario(
        id="kaya_silence",
        task="kaya",
        category="silence",
        persona=Persona(
            description="A hesitant caller who gives very short, delayed answers and sometimes says nothing.",
            goal="Slowly provide details; may or may not book.",
            language="hinglish",
            facts={
                "name": "Sunita Rao", "first_name": "Sunita", "last_name": "Rao",
                "city": "Hyderabad", "pincode": "500034", "branch": "Banjara Hills",
                "concern": "consultation", "email": "sunita.rao@gmail.com",
                "date": "2026-06-18", "time": "15:00", "phone": "+919500003333",
            },
            style="minimal words, long pauses, 'haan'/'hmm' answers",
        ),
        gold=Gold(
            should_complete=False,  # ceiling: completion uncertain; silence handling is the point
            expected_disposition="CALL_COMPLETED_NO_OUTCOME",
        ),
        notes="Primarily a Silence-Rate test (T3). Completion not the focus.",
    ),
    Scenario(
        id="kaya_off_topic",
        task="kaya",
        category="off_topic",
        persona=Persona(
            description="Caller who veers into unrelated medical questions (dentistry, weight-loss surgery) Kaya doesn't offer.",
            goal="Ask out-of-scope questions; see if agent stays on-rails and redirects.",
            language="hinglish",
            facts={"name": "Manish Gupta", "city": "Pune", "pincode": "411001"},
            style="curious, keeps going off-topic",
        ),
        gold=Gold(
            should_complete=False,
            expected_disposition="CALL_COMPLETED_NO_OUTCOME",
        ),
        notes="Tests scope-adherence / error rate (off-topic hallucination).",
    ),
]
