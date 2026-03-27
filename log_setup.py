"""
log_setup.py — Dual logging: console + rotating file
Writes to both terminal and logs/<service>.log simultaneously.

Per-call summaries are written to logs/calls/<cart_id>-attempt-<N>.log
"""
import logging
import logging.handlers
import os
from datetime import datetime

LOGS_DIR = "logs"
CALLS_DIR = os.path.join(LOGS_DIR, "calls")
_FMT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"


def setup_logging(service: str, level: int = logging.INFO) -> None:
    """
    Add a RotatingFileHandler to the root logger.
    Safe to call multiple times — checks for existing file handler for same path
    so no duplicates even when uvicorn restarts workers.
    Does NOT remove existing handlers (preserves uvicorn's console handler).
    """
    os.makedirs(LOGS_DIR, exist_ok=True)

    log_path = os.path.abspath(os.path.join(LOGS_DIR, f"{service}.log"))
    formatter = logging.Formatter(_FMT, datefmt=_DATE)
    root = logging.getLogger()
    root.setLevel(level)

    # Check if our file handler for this path already exists
    for h in root.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            if getattr(h, "baseFilename", None) == log_path:
                return  # already set up

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Also ensure there's a console handler if none exists (for agent.py which
    # doesn't have uvicorn's console handler pre-installed)
    has_console = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in root.handlers
    )
    if not has_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    logging.getLogger("imagica").info(f"Logging → console + {log_path}")


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
    """
    Write a human-readable per-call log to logs/calls/<cart_id>-attempt-<N>.log
    Returns the path of the written file.
    """
    os.makedirs(CALLS_DIR, exist_ok=True)

    cart_id = cart.get("cart_id", "unknown")
    attempt = cart.get("attempt_number", 1)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{cart_id}-attempt-{attempt}-{timestamp}.log"
    filepath = os.path.join(CALLS_DIR, filename)

    sep = "=" * 56
    thin = "-" * 56

    lines = [
        sep,
        "  CALL SUMMARY",
        sep,
        f"  Cart ID    : {cart_id}",
        f"  Customer   : {cart.get('customer_name')} ({cart.get('customer_phone')})",
        f"  Attempt    : {attempt} / 3",
        f"  Disposition: {disposition}",
        f"  Duration   : {duration_sec}s",
        f"  Discount   : {discount}%" if discount else "  Discount   : none",
        f"  Called At  : {called_at}",
        f"  Ended At   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    # Latency section
    lines += [thin, "  LATENCY", thin]
    if latency_per_turn:
        n = len(latency_per_turn)
        avg = round(sum(latency_per_turn) / n)
        under_1s = sum(1 for t in latency_per_turn if t < 1000)
        lines += [
            f"  First Response : {first_response_ms} ms" if first_response_ms else "  First Response : n/a",
            f"  Avg Per Turn   : {avg} ms",
            f"  Min / Max      : {min(latency_per_turn)} ms / {max(latency_per_turn)} ms",
            f"  Turns Measured : {n}",
            f"  Under 1 second : {under_1s}/{n} ({round(under_1s/n*100)}%)",
            f"  Per-Turn (ms)  : {latency_per_turn}",
        ]
    else:
        lines.append("  No latency data (transcription may not be enabled)")
    lines.append("")

    # Transcript section
    lines += [thin, "  TRANSCRIPT", thin]
    if transcript:
        for entry in transcript:
            role = "PRIYA   " if entry.get("role") == "agent" else "CUSTOMER"
            ts = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts).strftime("%H:%M:%S")
            except Exception:
                pass
            lines.append(f"  [{ts}] {role}: {entry.get('text', '')}")
    else:
        lines.append("  No transcript captured")
    lines.append("")

    # Tools section
    lines += [thin, "  TOOLS CALLED", thin]
    if tool_calls:
        for tc in tool_calls:
            ts = tc.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts).strftime("%H:%M:%S")
            except Exception:
                pass
            args = tc.get("args", {})
            args_str = "  ".join(f"{k}={v}" for k, v in args.items()) if args else ""
            lines.append(f"  [{ts}] {tc.get('tool', '?')}  {args_str}")
    else:
        lines.append("  No tools called")

    lines += ["", sep, ""]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logging.getLogger("imagica-agent").info(f"[CALL LOG] Written → {filepath}")
    return filepath
