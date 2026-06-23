"""Dual logging (console + rotating file) and per-call summary files.

``setup_logging`` attaches a rotating file handler to the root logger;
``write_call_summary`` renders a human-readable per-call report under
``logs/calls/``.
"""
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path("logs")
CALLS_DIR = LOGS_DIR / "calls"

_FMT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"
_MAX_LOG_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def setup_logging(service: str, level: int = logging.INFO) -> None:
    """Attach a rotating file handler (and console handler) to the root logger.

    Safe to call multiple times: a file handler for the same path is only added
    once, so uvicorn worker restarts do not create duplicate handlers. Existing
    handlers are preserved (notably uvicorn's own console handler).

    Args:
        service: Log file base name, written to ``logs/<service>.log``.
        level: Root logger level.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log_path = str((LOGS_DIR / f"{service}.log").resolve())
    formatter = logging.Formatter(_FMT, datefmt=_DATE)
    root = logging.getLogger()
    root.setLevel(level)

    for handler in root.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            if getattr(handler, "baseFilename", None) == log_path:
                return  # already set up for this path

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Ensure a console handler exists (agent worker has no uvicorn handler).
    has_console = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in root.handlers
    )
    if not has_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    logging.getLogger("imagica").info("Logging -> console + %s", log_path)


def _format_timestamp(raw: str, fmt: str = "%H:%M:%S") -> str:
    """Best-effort reformat of an ISO timestamp; returns the input on failure."""
    try:
        return datetime.fromisoformat(raw).strftime(fmt)
    except ValueError:
        return raw


def _summary_header(cart: dict, disposition: str, duration_sec: int, discount: int,
                    called_at: str) -> list[str]:
    """Build the header block of a call summary."""
    sep = "=" * 56
    return [
        sep,
        "  CALL SUMMARY",
        sep,
        f"  Cart ID    : {cart.get('cart_id', 'unknown')}",
        f"  Customer   : {cart.get('customer_name')} ({cart.get('customer_phone')})",
        f"  Attempt    : {cart.get('attempt_number', 1)} / 3",
        f"  Disposition: {disposition}",
        f"  Duration   : {duration_sec}s",
        f"  Discount   : {discount}%" if discount else "  Discount   : none",
        f"  Called At  : {called_at}",
        f"  Ended At   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]


def _latency_block(latency_per_turn: list[int], first_response_ms: int | None) -> list[str]:
    """Build the latency section of a call summary."""
    thin = "-" * 56
    lines = [thin, "  LATENCY", thin]
    if not latency_per_turn:
        lines.append("  No latency data (transcription may not be enabled)")
        return lines + [""]

    n = len(latency_per_turn)
    avg = round(sum(latency_per_turn) / n)
    under_1s = sum(1 for t in latency_per_turn if t < 1000)
    lines += [
        f"  First Response : {first_response_ms} ms" if first_response_ms
        else "  First Response : n/a",
        f"  Avg Per Turn   : {avg} ms",
        f"  Min / Max      : {min(latency_per_turn)} ms / {max(latency_per_turn)} ms",
        f"  Turns Measured : {n}",
        f"  Under 1 second : {under_1s}/{n} ({round(under_1s / n * 100)}%)",
        f"  Per-Turn (ms)  : {latency_per_turn}",
        "",
    ]
    return lines


def _transcript_block(transcript: list[dict]) -> list[str]:
    """Build the transcript section of a call summary."""
    thin = "-" * 56
    lines = [thin, "  TRANSCRIPT", thin]
    if not transcript:
        return lines + ["  No transcript captured", ""]
    for entry in transcript:
        role = "PRIYA   " if entry.get("role") == "agent" else "CUSTOMER"
        ts = _format_timestamp(entry.get("ts", ""))
        lines.append(f"  [{ts}] {role}: {entry.get('text', '')}")
    return lines + [""]


def _tools_block(tool_calls: list[dict]) -> list[str]:
    """Build the tools-called section of a call summary."""
    thin = "-" * 56
    lines = [thin, "  TOOLS CALLED", thin]
    if not tool_calls:
        return lines + ["  No tools called"]
    for call in tool_calls:
        ts = _format_timestamp(call.get("ts", ""))
        args = call.get("args", {})
        args_str = "  ".join(f"{k}={v}" for k, v in args.items()) if args else ""
        lines.append(f"  [{ts}] {call.get('tool', '?')}  {args_str}")
    return lines


def write_call_summary(
    cart: dict,
    disposition: str,
    duration_sec: int,
    discount: int,
    transcript: list[dict],
    tool_calls: list[dict],
    latency_per_turn: list[int],
    first_response_ms: int | None,
    called_at: str,
) -> str:
    """Write a human-readable per-call report and return its path.

    Args:
        cart: Cart dict used during the call.
        disposition: Final disposition code.
        duration_sec: Total call duration.
        discount: Discount percent applied (0 if none).
        transcript: List of ``{"role", "text", "ts"}`` dicts.
        tool_calls: List of ``{"tool", "ts", "args"}`` dicts.
        latency_per_turn: Per-turn end-to-end latencies in ms.
        first_response_ms: Latency to the first agent response in ms.
        called_at: ISO timestamp when the call started.

    Returns:
        Path to the written log file.
    """
    CALLS_DIR.mkdir(parents=True, exist_ok=True)

    cart_id = cart.get("cart_id", "unknown")
    attempt = cart.get("attempt_number", 1)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filepath = CALLS_DIR / f"{cart_id}-attempt-{attempt}-{timestamp}.log"

    lines: list[str] = []
    lines += _summary_header(cart, disposition, duration_sec, discount, called_at)
    lines += _latency_block(latency_per_turn, first_response_ms)
    lines += _transcript_block(transcript)
    lines += _tools_block(tool_calls)
    lines += ["", "=" * 56, ""]

    filepath.write_text("\n".join(lines), encoding="utf-8")
    logging.getLogger("imagica-agent").info("[CALL LOG] Written -> %s", filepath)
    return str(filepath)
