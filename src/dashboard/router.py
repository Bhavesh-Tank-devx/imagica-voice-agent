"""Dashboard HTML pages, Kaya admin APIs, and observability endpoints."""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from src.persistence import (
    get_call_detail,
    get_call_logs,
    get_kaya_appointments,
    get_kaya_transcript_detail,
    get_kaya_transcripts,
    get_metrics,
)

logger = logging.getLogger("imagica-webhook")

router = APIRouter()

# HTML pages live in web/ at the project root (two levels above src/dashboard/).
_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def _page(filename: str) -> FileResponse:
    """Serve a static HTML page from the web/ directory."""
    return FileResponse(_WEB_DIR / filename)


@router.get("/")
async def dashboard() -> FileResponse:
    """Serve the main dev dashboard."""
    return _page("dashboard.html")


@router.get("/kaya")
async def kaya_demo() -> FileResponse:
    """Serve the Kaya demo page."""
    return _page("kaya_demo.html")


@router.get("/kaya/appointments")
async def kaya_appointments_page() -> FileResponse:
    """Serve the Kaya appointments page."""
    return _page("kaya_appointments.html")


@router.get("/kaya/transcripts")
async def kaya_transcripts_page() -> FileResponse:
    """Serve the Kaya transcripts page."""
    return _page("kaya_transcripts.html")


@router.get("/api/kaya/appointments")
async def api_kaya_appointments() -> JSONResponse:
    """Return all Kaya appointments as JSON."""
    return JSONResponse({"appointments": get_kaya_appointments()})


@router.get("/api/kaya/transcripts")
async def api_kaya_transcripts() -> JSONResponse:
    """Return Kaya call summaries as JSON."""
    return JSONResponse({"calls": get_kaya_transcripts()})


@router.get("/api/kaya/transcripts/{call_id}")
async def api_kaya_transcript_detail(call_id: int) -> JSONResponse:
    """Return a single Kaya call with its transcript."""
    detail = get_kaya_transcript_detail(call_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Call not found")
    return JSONResponse(detail)


@router.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/metrics")
async def metrics() -> dict:
    """Aggregated PRD evaluation metrics."""
    return get_metrics()


def _summarise_call(row: dict) -> dict:
    """Project a call_logs row to the dashboard's call-list shape."""
    transcript = row.get("transcript") or []
    turns = row.get("latency_per_turn") or []
    if isinstance(transcript, str):
        transcript = _safe_json(transcript)
    if isinstance(turns, str):
        turns = _safe_json(turns)
    return {
        "id": row["id"],
        "cart_id": row["cart_id"],
        "customer": row["customer_name"],
        "phone": row["customer_phone"],
        "disposition": row["disposition"],
        "attempt": row["attempt_number"],
        "called_at": row["called_at"],
        "first_response_ms": row.get("first_response_ms"),
        "latency_avg_ms": round(sum(turns) / len(turns)) if turns else None,
        "latency_per_turn_ms": turns,
        "transcript_turns": len(transcript),
        "transcript": transcript,
    }


def _safe_json(raw: str) -> list:
    """Decode a JSON array, returning ``[]`` on error."""
    try:
        return json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


@router.get("/calls")
async def list_calls(limit: int = Query(default=20)) -> dict:
    """List recent calls with parsed transcript and latency summaries."""
    rows = get_call_logs()
    return {"calls": [_summarise_call(r) for r in rows[:limit]]}


@router.get("/calls/{call_id}")
async def call_detail(call_id: int) -> dict:
    """Return a single call's full detail."""
    detail = get_call_detail(call_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Call not found")
    return detail
