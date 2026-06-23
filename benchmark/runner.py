"""
benchmark/runner.py — provider-agnostic conversation orchestration.

Drives a VoiceStack through a scenario, executes tool calls with the SHARED
production logic (voice_agent.execute_tool — same tools, same booking writes,
same email correction), records the conversation into call_logs tagged with
(run, stack, scenario, tier), and returns the in-memory record for scoring.

Two flows:
  - run_conversation(): a full dialogue (T1 sim, or MockStack which self-drives).
  - run_replay():       T2 fixed-audio replay → per-utterance WER.

Cost guardrail: estimate_batch_cost() prints projected $ before any live (T3) batch.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime

from src.telephony import execute_tool  # shared tool logic (same booking writes, email correction)
from src.conversation import detect_language  # language detection
from src.constants import (
    DISPOSITION_CALL_COMPLETED_NO_OUTCOME,
    DISPOSITION_NO_ANSWER,
)
from src.persistence import log_benchmark_call
from .providers.base import (
    AgentAudio, AgentTranscript, ConversationEnd, Interruption,
    StackConfig, ToolCall, UserTranscript, VoiceStack,
)
from .scorers import word_error_rate

logger = logging.getLogger("benchmark.runner")

ANSWERED_THRESHOLD_SEC = 60


def use_benchmark_db(path: str = "data/benchmark_runs.db") -> None:
    """Point ALL persistence (call_logs, kaya_bookings via the shared execute_tool)
    at a dedicated benchmark DB so production data (post_call.db) is never touched.
    Must be called before any run_* function."""
    from src.persistence import db as persistence_db
    persistence_db.DB_PATH = path
    persistence_db.init_db()
    logger.info("[runner] benchmark DB → %s", path)


def new_run_id() -> str:
    return f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _fresh_state() -> dict:
    return {
        "disposition": DISPOSITION_NO_ANSWER,
        "discount": 0,
        "sms_sent": False,
        "tool_calls": [],
        "transcript": [],
        "latency_per_turn": [],
        "first_response_ms": None,
        "_user_stopped_at": 0.0,
        "interruptions": 0,
    }


async def _execute_and_reply(stack: VoiceStack, ev: ToolCall, cart: dict, state: dict) -> None:
    """Run a tool with the shared logic, annotate ok/result, reply to the stack."""
    ts_before = len(state["tool_calls"])
    ok = True
    try:
        result = await execute_tool(ev.name, ev.parameters, cart, state)
    except Exception as exc:  # pragma: no cover
        result, ok = f"tool error: {exc}", False
        logger.error("[runner] tool %s raised: %s", ev.name, exc)
    # execute_tool appends its own {"tool","ts","args"} entry; enrich the latest.
    if len(state["tool_calls"]) > ts_before:
        state["tool_calls"][-1]["result"] = result
        state["tool_calls"][-1]["ok"] = ok
    await stack.send_tool_result(ev.call_id, result, is_error=not ok)


async def run_conversation(
    stack: VoiceStack,
    scenario,
    *,
    run_id: str,
    tier: str = "T1",
    drive_user: bool = False,
    overall_timeout: float = 120.0,
) -> dict:
    """Run one dialogue. If drive_user, a SimUser feeds audio after each agent
    turn (real stacks); MockStack self-drives so drive_user=False."""
    cart = scenario.cart()
    state = _fresh_state()
    start = time.time()
    agent_turn_done = asyncio.Event()

    config = StackConfig(
        system_prompt=scenario.persona.description,  # real stacks use dashboard prompt; passed for pipeline stacks
        dynamic_vars={
            "customer_phone": cart["customer_phone"],
            "customer_name": cart["customer_name"],
            "city": cart.get("city", ""),
            "call_type": cart.get("call_type", "OUTBOUND"),
        },
        tools=scenario.gold.expected_tool and [scenario.gold.expected_tool] or [],
    )
    await stack.start(config)

    async def consume():
        async for ev in stack.events():
            if isinstance(ev, AgentTranscript):
                now = datetime.now().isoformat()
                state["transcript"].append({"role": "agent", "text": ev.text, "ts": now})
                if state["_user_stopped_at"] > 0:
                    ms = int((time.time() - state["_user_stopped_at"]) * 1000)
                    state["_user_stopped_at"] = 0.0
                    if ms < 15000:
                        state["latency_per_turn"].append(ms)
                        if state["first_response_ms"] is None:
                            state["first_response_ms"] = ms
                agent_turn_done.set()
            elif isinstance(ev, UserTranscript):
                now = datetime.now().isoformat()
                state["transcript"].append({"role": "user", "text": ev.text, "ts": now})
                state["_user_stopped_at"] = time.time()
            elif isinstance(ev, ToolCall):
                await _execute_and_reply(stack, ev, cart, state)
            elif isinstance(ev, Interruption):
                state["interruptions"] += 1
            elif isinstance(ev, AgentAudio):
                pass  # audio not persisted in benchmark
            elif isinstance(ev, ConversationEnd):
                return

    async def driver():
        """Real-stack user simulation: after each agent turn, speak the next line."""
        from .sim_user import SimUser, tts_telephony
        sim = SimUser(scenario.persona)
        turns = 0
        while turns < scenario.max_turns:
            try:
                await asyncio.wait_for(agent_turn_done.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                break
            agent_turn_done.clear()
            line = sim.next_utterance(state["transcript"])
            if not line:
                break
            try:
                pcm = tts_telephony(line)
            except Exception as exc:
                logger.error("[runner] TTS failed (%s) — ending drive", exc)
                break
            # stream in ~20ms frames (640 bytes @16k/16-bit)
            for i in range(0, len(pcm), 640):
                await stack.send_user_audio(pcm[i:i + 640])
                await asyncio.sleep(0.02)
            state["_user_stopped_at"] = time.time()
            turns += 1

    try:
        tasks = [asyncio.create_task(consume(), name="consume")]
        if drive_user:
            tasks.append(asyncio.create_task(driver(), name="driver"))
        done, pending = await asyncio.wait(tasks, timeout=overall_timeout,
                                           return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            if t.exception():
                logger.error("[runner] task %s raised: %s", t.get_name(), t.exception())
    finally:
        await stack.close()

    duration = int(time.time() - start)
    disposition = state["disposition"]
    if disposition == DISPOSITION_NO_ANSWER and duration > ANSWERED_THRESHOLD_SEC:
        disposition = DISPOSITION_CALL_COMPLETED_NO_OUTCOME
    # In benchmark we also treat "answered, dialogue held, no decisive tool" as no-outcome
    if disposition == DISPOSITION_NO_ANSWER and len(state["transcript"]) >= 2:
        disposition = DISPOSITION_CALL_COMPLETED_NO_OUTCOME

    cost = stack.estimate_cost(duration)
    row_id = log_benchmark_call(
        benchmark_run_id=run_id, stack=stack.name, scenario_id=scenario.id, tier=tier,
        cart=cart, disposition=disposition, transcript=state["transcript"],
        summary=f"{scenario.category} via {stack.name}",
        tool_calls=state["tool_calls"], latency_per_turn=state["latency_per_turn"],
        first_response_ms=state["first_response_ms"], duration_sec=duration,
        language_detected=detect_language(state["transcript"]),
        cost_usd=cost, agent_type=cart.get("agent_type"),
    )
    return {
        "row_id": row_id, "stack": stack.name, "scenario_id": scenario.id, "tier": tier,
        "disposition": disposition, "transcript": state["transcript"],
        "tool_calls": state["tool_calls"], "latency_per_turn": state["latency_per_turn"],
        "first_response_ms": state["first_response_ms"], "duration_seconds": duration,
        "language_detected": detect_language(state["transcript"]),
        "interruptions": state["interruptions"], "cost_usd": cost, "wer": None,
    }


async def run_replay(make_stack, utterances: list, *, run_id: str) -> list[dict]:
    """T2: replay each fixed utterance, capture the stack's STT, score WER vs gold.

    `make_stack` is a zero-arg factory returning a FRESH VoiceStack per utterance.
    Using a fresh instance per utterance avoids any reuse-after-close / exhausted-
    stream bug class on the tier that carries the powered comparison.
    """
    results = []
    for utt in utterances:
        stack = make_stack()
        await stack.start(StackConfig(system_prompt="(stt-only)", dynamic_vars={}))
        heard = {"text": ""}

        async def grab():
            async for ev in stack.events():
                if isinstance(ev, UserTranscript):
                    heard["text"] = ev.text
                    return
                if isinstance(ev, ConversationEnd):
                    return

        consumer = asyncio.create_task(grab())
        pcm = utt.pcm16k()
        for i in range(0, len(pcm), 640):
            await stack.send_user_audio(pcm[i:i + 640])
            await asyncio.sleep(0.02)
        try:
            await asyncio.wait_for(consumer, timeout=15.0)
        except asyncio.TimeoutError:
            consumer.cancel()
        await stack.close()

        wer = word_error_rate(heard["text"], utt.gold_transcript)
        cart = {"cart_id": utt.id, "customer_name": "replay", "customer_phone": ""}
        row_id = log_benchmark_call(
            benchmark_run_id=run_id, stack=stack.name, scenario_id=utt.id, tier="T2",
            cart=cart, disposition="REPLAY",
            transcript=[{"role": "user", "text": heard["text"], "ts": datetime.now().isoformat()},
                        {"role": "gold", "text": utt.gold_transcript, "ts": datetime.now().isoformat()}],
            summary=f"replay STT (synthetic={getattr(utt,'synthetic',False)})",
            wer=wer, language_detected=utt.language,
        )
        logger.info("[replay] %s stack=%s WER=%.3f", utt.id, stack.name, wer)
        results.append({"row_id": row_id, "utterance_id": utt.id, "stack": stack.name,
                        "heard": heard["text"], "gold": utt.gold_transcript, "wer": wer,
                        "synthetic": getattr(utt, "synthetic", False)})
    return results


def estimate_batch_cost(stack: VoiceStack, n_calls: int, avg_sec: float = 90.0) -> float:
    """Projected $ for a live (T3) batch — runner prints this before dialing."""
    est = round(stack.estimate_cost(avg_sec) * n_calls, 4)
    logger.warning("[runner] COST GUARD: %s × %d calls @ ~%.0fs ≈ $%.4f", stack.name, n_calls, avg_sec, est)
    return est
