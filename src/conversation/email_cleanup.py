"""Speech-to-text email repair for the Kaya booking flow.

Spoken email addresses arrive with ASR artefacts ("at gmail" run together,
letters misheard). These helpers normalise the obvious cases and, when the
local-part is clearly the customer's name, snap it to the name-derived spelling.
"""
import logging
import re

logger = logging.getLogger("imagica-voice-agent")

# Applied in order after stripping all spaces (so patterns never need \s).
_DOMAIN_SUBS: list[tuple[str, str]] = [
    # "at<provider>" run together -> @<provider>
    (r"at(gmail|yahoo|hotmail|outlook|icloud|rediff|live)", r"@\1"),
    # "dot<ext>" run together -> .<ext>
    (r"dot(com|in|co|net|org|io)", r".\1"),
]

# Max edit distance for snapping a local-part to a name-derived candidate.
_NAME_MATCH_MAX_DISTANCE = 2


def normalize_email(raw: str) -> str:
    """Fix common transcription errors in a spoken email address.

    Returns the original string unchanged if the result is too broken to be a
    valid address (the agent will re-ask).
    """
    cleaned = raw.lower().strip().replace(" ", "")
    for pattern, replacement in _DOMAIN_SUBS:
        cleaned = re.sub(pattern, replacement, cleaned)
    if cleaned.count("@") != 1 or "." not in cleaned.split("@")[-1]:
        return raw
    return cleaned


def levenshtein(a: str, b: str) -> int:
    """Return the Wagner-Fischer edit distance between two strings."""
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = (
                dp[j],
                prev if a[i - 1] == b[j - 1] else 1 + min(prev, dp[j], dp[j - 1]),
            )
    return dp[n]


def _name_candidates(first_name: str, last_name: str) -> list[str]:
    """Build plausible name-derived local-parts (e.g. firstlast, first.last)."""
    candidates = [
        (first_name + last_name).lower(),
        (first_name + "." + last_name).lower(),
        (first_name + "_" + last_name).lower(),
    ]
    if first_name:
        candidates.append((first_name[0] + last_name).lower())
    return [c for c in candidates if c]


def fuzzy_correct_email(email: str, first_name: str, last_name: str) -> str:
    """Snap an email's local-part to the customer's name when very close.

    If the local-part is within ``_NAME_MATCH_MAX_DISTANCE`` edits of a
    name-derived candidate, replace it. Catches ASR substitutions like
    'e'->'t' (bhaveshreank -> bhaveshtank).
    """
    if "@" not in email:
        return email
    username, domain = email.split("@", 1)

    best_dist, best_candidate = len(username) + 1, None
    for candidate in _name_candidates(first_name, last_name):
        dist = levenshtein(username.lower(), candidate)
        if dist < best_dist:
            best_dist, best_candidate = dist, candidate

    if best_dist <= _NAME_MATCH_MAX_DISTANCE and best_candidate:
        corrected = f"{best_candidate}@{domain}"
        if corrected != email:
            logger.info(
                "[EMAIL] Fuzzy-corrected '%s' -> '%s' (edit_dist=%s)",
                email, corrected, best_dist,
            )
        return corrected

    return email
