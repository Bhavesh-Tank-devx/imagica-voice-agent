"""Pincode / city -> Kaya Clinic branch lookup.

Used by the ``get_closest_branches`` tool in the Twilio<->ElevenLabs bridge.
"""

# City -> ordered list of branch areas (matches the system prompt knowledge base).
CITY_BRANCHES: dict[str, list[str]] = {
    "Mumbai": ["Peddar Rd", "Bandra", "Andheri", "Borivali", "Matunga", "Powai", "Goregaon", "Mulund", "Malad", "Chembur"],
    "Bengaluru": ["Indiranagar", "Koramangala", "HSR Layout", "JP Nagar", "Jayanagar", "Whitefield", "Electronic City", "HRBR Layout", "New BEL Rd", "Sadashivanagar"],
    "Delhi": ["Karol Bagh", "Green Park", "Vasant Kunj", "Khan Market", "Lajpat Nagar", "GK2", "Civil Lines", "South Extension", "Punjabi Bagh"],
    "Hyderabad": ["Banjara Hills", "Himayat Nagar", "Kukatpally", "Gachibowli", "Secunderabad"],
    "Chennai": ["Alwarpet", "Anna Nagar", "T. Nagar", "Nungambakkam", "Velachery", "Adyar", "Kilpauk", "Ashok Nagar"],
    "Pune": ["Aundh", "Koregaon Park", "Law College Rd", "Camp", "Viman Nagar", "Pimple Saudagar"],
    "Kolkata": ["Kankurgachi", "Lake Gardens", "Salt Lake", "Loudon Street", "Deshapriya Park", "Alipore"],
    "Ahmedabad": ["CG Road", "Vastrapur", "Satellite Cross Road"],
    "Surat": ["Vesu", "Ghod Dod Road"],
    "Coimbatore": ["DB Road", "Skanda Square"],
    "Vadodara": ["Jetalpur"],
    "Chandigarh": ["Sector 8C"],
    "Nagpur": ["Abhyankar Nagar"],
    "Siliguri": ["Chayan Para"],
    "Guwahati": ["GS Road"],
    "Ludhiana": ["Sun View Plaza"],
    # Cities where the exact branch address must be confirmed on call.
    "Jaipur": [],
    "Lucknow": [],
    "Kochi": [],
    "Indore": [],
    "Visakhapatnam": [],
    "Navi Mumbai": [],
    "Thane": [],
    "Noida": [],
    "Gurugram": [],
    "Faridabad": [],
    "Jalandhar": [],
}

# (prefix, city) — longer prefixes listed first for unambiguous matching.
_PREFIX_MAP: list[tuple[str, str]] = [
    ("122", "Gurugram"),
    ("121", "Faridabad"),
    ("201", "Noida"),
    ("144", "Jalandhar"),
    ("141", "Ludhiana"),
    ("302", "Jaipur"),
    ("303", "Jaipur"),
    ("226", "Lucknow"),
    ("682", "Kochi"),
    ("530", "Visakhapatnam"),
    ("531", "Visakhapatnam"),
    ("390", "Vadodara"),
    ("395", "Surat"),
    ("11", "Delhi"),
    ("12", "Delhi"),
    ("40", "Mumbai"),
    ("41", "Pune"),
    ("50", "Hyderabad"),
    ("56", "Bengaluru"),
    ("60", "Chennai"),
    ("61", "Chennai"),
    ("64", "Coimbatore"),
    ("70", "Kolkata"),
    ("71", "Kolkata"),
    ("38", "Ahmedabad"),
    ("39", "Surat"),
    ("43", "Nagpur"),
    ("44", "Nagpur"),
    ("45", "Indore"),
    ("16", "Chandigarh"),
    ("73", "Siliguri"),
    ("78", "Guwahati"),
]


def _city_from_pincode(pincode: str) -> str | None:
    """Resolve a city from a pincode prefix, or None if no prefix matches."""
    pincode = pincode.strip()
    for prefix, city in _PREFIX_MAP:
        if pincode.startswith(prefix):
            return city
    return None


def _normalize_city(name: str) -> str | None:
    """Match a spoken city name to a known city (exact, then substring)."""
    name_lower = name.strip().lower()
    for city in CITY_BRANCHES:
        if city.lower() == name_lower:
            return city
    for city in CITY_BRANCHES:
        if name_lower in city.lower() or city.lower() in name_lower:
            return city
    return None


def _branch_listing(branches: list[str]) -> str:
    """Render up to three branch names as a natural-language list."""
    if len(branches) == 1:
        return branches[0]
    if len(branches) == 2:
        return f"{branches[0]} and {branches[1]}"
    return f"{branches[0]}, {branches[1]}, and {branches[2]}"


def get_closest_branches(pincode: str = "", city: str = "") -> dict:
    """Return branch information for a given pincode or city name.

    Returns:
        A dict with keys ``city`` (resolved name or None), ``branches`` (list),
        ``confirm_address`` (bool — agent must confirm address on call), and
        ``message`` (agent-ready response).
    """
    resolved: str | None = None
    if pincode:
        resolved = _city_from_pincode(pincode)
    if not resolved and city:
        resolved = _normalize_city(city)

    if not resolved:
        return {
            "city": None,
            "branches": [],
            "confirm_address": False,
            "message": "I could not find a Kaya Clinic in that area. Could you please share your city name?",
        }

    branches = CITY_BRANCHES.get(resolved, [])

    if not branches:
        return {
            "city": resolved,
            "branches": [],
            "confirm_address": True,
            "message": (
                f"We do have a Kaya Clinic in {resolved}. "
                "Could you give me a moment — I'll confirm the exact address for you."
            ),
        }

    if len(branches) <= 3:
        return {
            "city": resolved,
            "branches": branches,
            "confirm_address": False,
            "message": (
                f"In {resolved}, we have branches at {_branch_listing(branches)}. "
                "Which one is most convenient for you?"
            ),
        }

    if len(branches) <= 8:
        return {
            "city": resolved,
            "branches": branches,
            "confirm_address": False,
            "message": (
                f"We have {len(branches)} branches in {resolved}. "
                "Which part of the city are you in? I can suggest the two or three nearest ones."
            ),
        }

    # 9+ branches — ask for area first.
    return {
        "city": resolved,
        "branches": branches,
        "confirm_address": False,
        "message": (
            f"We have {len(branches)} branches across {resolved}. "
            "Which area of the city are you in? I'll suggest the two closest to you."
        ),
    }
