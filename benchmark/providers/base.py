"""
benchmark/providers/base.py — the provider abstraction for the benchmark harness.

Every voice stack we benchmark (ElevenLabs Conv AI, a best-of-breed pipeline,
Sarvam Indic, …) speaks a different WebSocket / API dialect. This module
normalizes them behind ONE interface so `runner.py` is provider-agnostic and
only the per-stack adapter files hold provider-specific code.

The normalized event vocabulary mirrors what the production ElevenLabs bridge
already handles (voice_agent.py): audio out, agent transcript, user transcript,
interruption, tool call, conversation end.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


# ---------------------------------------------------------------------------
# Normalized events — every stack adapter yields these, nothing else
# ---------------------------------------------------------------------------

@dataclass
class AgentAudio:
    """A chunk of agent speech, PCM 16-bit 16 kHz mono (base64-decoded bytes)."""
    pcm16k: bytes


@dataclass
class AgentTranscript:
    """Text of what the agent said (a full agent turn or incremental)."""
    text: str
    final: bool = True


@dataclass
class UserTranscript:
    """The stack's STT of what the user said — i.e. what the stack *heard*.
    This is the key field for WER / ASR-error scoring: compare to gold."""
    text: str
    final: bool = True


@dataclass
class ToolCall:
    """Agent requested a tool. The runner executes it (shared execute_tool) and
    replies via VoiceStack.send_tool_result(call_id, result)."""
    call_id: str
    name: str
    parameters: dict


@dataclass
class Interruption:
    """User barged in; agent audio should be flushed."""
    pass


@dataclass
class ConversationEnd:
    reason: str = "unknown"


Event = AgentAudio | AgentTranscript | UserTranscript | ToolCall | Interruption | ConversationEnd


# ---------------------------------------------------------------------------
# Cost model — per-stack pricing pulled from VOICE_MODEL_STUDY.md
# ---------------------------------------------------------------------------

@dataclass
class CostModel:
    """Rough $/min estimate. Keep numbers sourced to the study; this is for
    relative comparison, not billing. Sub-components left None where a stack
    bundles them (e.g. ElevenLabs Conv AI is one blended per-minute rate)."""
    per_minute_usd: float = 0.0
    note: str = ""

    def estimate(self, duration_sec: float) -> float:
        return round(self.per_minute_usd * (duration_sec / 60.0), 6)


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

@dataclass
class StackConfig:
    """What the runner hands a stack to start a conversation."""
    system_prompt: str
    dynamic_vars: dict = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    # Audio the stack should accept from the simulated user, as PCM 16k frames.
    # The runner pushes frames via send_user_audio().


class VoiceStack(ABC):
    """One conversational voice stack under test.

    Lifecycle:
        await stack.start(config)
        # runner pushes user audio and consumes events concurrently
        async for ev in stack.events(): ...
        await stack.send_user_audio(pcm16k)        # interleaved
        await stack.send_tool_result(id, result)   # in response to ToolCall
        await stack.close()
    """

    name: str = "base"
    cost_model: CostModel = CostModel()

    @abstractmethod
    async def start(self, config: StackConfig) -> None: ...

    @abstractmethod
    async def send_user_audio(self, pcm16k: bytes) -> None: ...

    @abstractmethod
    def events(self) -> AsyncIterator[Event]: ...

    @abstractmethod
    async def send_tool_result(self, call_id: str, result: str, is_error: bool = False) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    def estimate_cost(self, duration_sec: float) -> float:
        return self.cost_model.estimate(duration_sec)


# ---------------------------------------------------------------------------
# MockStack — scripted conversation, ZERO API spend.
# Lets us verify the whole measurement pipeline (scorers, judge, report)
# end-to-end before spending a cent on real providers.
# ---------------------------------------------------------------------------

class MockStack(VoiceStack):
    """Replays a deterministic script of agent turns + tool calls.

    The script is a list of steps, each one of:
      {"agent": "text"}                          → emits AgentTranscript (+ tiny AgentAudio)
      {"tool": "name", "params": {...}}          → emits ToolCall, waits for result
      {"hear": "text"}                           → emits UserTranscript (what the stack 'heard')
      {"end": "reason"}                          → emits ConversationEnd

    `heard_text` overrides let us simulate ASR error (stack mis-hears the user)
    for WER/ASR-error testing without real audio.
    """

    name = "mock"
    cost_model = CostModel(per_minute_usd=0.0, note="mock — no real provider")

    def __init__(self, script: list[dict], turn_latency_ms: int = 600):
        self.script = script
        self.turn_latency_ms = turn_latency_ms
        self._tool_results: asyncio.Queue = asyncio.Queue()
        self._started = False

    async def start(self, config: StackConfig) -> None:
        self._started = True
        self._config = config

    async def send_user_audio(self, pcm16k: bytes) -> None:
        # Mock ignores inbound audio; the script drives the conversation.
        return

    async def send_tool_result(self, call_id: str, result: str, is_error: bool = False) -> None:
        await self._tool_results.put((call_id, result, is_error))

    async def events(self) -> AsyncIterator[Event]:
        for step in self.script:
            if "hear" in step:
                yield UserTranscript(text=step["hear"])
            elif "agent" in step:
                # simulate a little response latency for the latency slice
                await asyncio.sleep(self.turn_latency_ms / 1000.0)
                yield AgentAudio(pcm16k=b"\x00\x00" * 8)  # token silence
                yield AgentTranscript(text=step["agent"])
            elif "tool" in step:
                cid = step.get("call_id", f"mock-{step['tool']}")
                yield ToolCall(call_id=cid, name=step["tool"], parameters=step.get("params", {}))
                # wait for runner to execute and reply (bounded)
                try:
                    await asyncio.wait_for(self._tool_results.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
            elif "interrupt" in step:
                yield Interruption()
            elif "end" in step:
                yield ConversationEnd(reason=step["end"])
                return
        yield ConversationEnd(reason="script_exhausted")

    async def close(self) -> None:
        self._started = False
