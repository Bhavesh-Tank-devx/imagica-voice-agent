"""
benchmark/providers/elevenlabs_stack.py — ElevenLabs Conversational AI adapter.

Speaks the same WebSocket protocol as the production bridge (voice_agent.py),
but exposes it through the VoiceStack interface so the benchmark runner can
drive it identically to any other stack.

Difference from production: there is no Twilio. The runner pushes PCM-16k frames
(already codec-degraded to telephony grade by sim_user) straight into
`user_audio_chunk`, and consumes normalized events. Tool execution is shared
(runner calls voice_agent.execute_tool), so only protocol framing lives here.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import AsyncIterator

import websockets
from dotenv import load_dotenv

from .base import (
    AgentAudio,
    AgentTranscript,
    ConversationEnd,
    CostModel,
    Event,
    Interruption,
    StackConfig,
    ToolCall,
    UserTranscript,
    VoiceStack,
)

load_dotenv()
logger = logging.getLogger("benchmark.elevenlabs")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_WSS_URL = "wss://api.elevenlabs.io/v1/convai/conversation"


class ElevenLabsStack(VoiceStack):
    """ElevenLabs Conversational AI as a benchmark stack.

    Note: ElevenLabs manages STT+LLM+TTS+prompt server-side. The system prompt
    and tools are configured in the ElevenLabs dashboard agent; here we can only
    pass dynamic_variables and (optionally) overrides the agent allows. We pass
    the agent_id for the task being benchmarked.
    """

    name = "elevenlabs"
    # Blended rate after the Feb-2026 ~50% cut (VOICE_MODEL_STUDY.md §3.4/3.5).
    cost_model = CostModel(per_minute_usd=0.09, note="Conv AI 2.0 blended, post-Feb-2026")

    def __init__(self, agent_id: str | None = None):
        self.agent_id = agent_id or os.getenv("ELEVENLABS_KAYA_AGENT_ID") or os.getenv("ELEVENLABS_AGENT_ID", "")
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._closed = False

    async def start(self, config: StackConfig) -> None:
        if not ELEVENLABS_API_KEY or not self.agent_id:
            raise RuntimeError("ELEVENLABS_API_KEY / agent_id not configured")
        self._closed = False  # reset so a reused instance can re-open after close()
        url = f"{ELEVENLABS_WSS_URL}?agent_id={self.agent_id}"
        self._ws = await websockets.connect(url, additional_headers={"xi-api-key": ELEVENLABS_API_KEY})
        session_init = {
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {"tts": {"output_format": "pcm_16000"}},
            "dynamic_variables": config.dynamic_vars or {},
        }
        await self._ws.send(json.dumps(session_init))
        logger.info("[EL] connected agent_id=%s", self.agent_id)

    async def send_user_audio(self, pcm16k: bytes) -> None:
        if self._ws is None or self._closed:
            return
        await self._ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm16k).decode()}))

    async def events(self) -> AsyncIterator[Event]:
        assert self._ws is not None
        async for raw in self._ws:
            msg = json.loads(raw)
            mt = msg.get("type")

            if mt == "audio":
                b64 = msg.get("audio_event", {}).get("audio_base_64", "")
                if b64:
                    yield AgentAudio(pcm16k=base64.b64decode(b64))

            elif mt == "agent_response":
                text = msg.get("agent_response_event", {}).get("agent_response", "")
                if text:
                    yield AgentTranscript(text=text)

            elif mt == "user_transcript":
                text = msg.get("user_transcription_event", {}).get("user_transcript", "")
                if text:
                    yield UserTranscript(text=text)

            elif mt == "interruption":
                yield Interruption()

            elif mt == "client_tool_call":
                tc = msg.get("client_tool_call", {})
                yield ToolCall(
                    call_id=tc.get("tool_call_id", ""),
                    name=tc.get("tool_name", ""),
                    parameters=tc.get("parameters", {}),
                )

            elif mt == "conversation_end":
                reason = msg.get("conversation_end_event", {}).get("reason", "unknown")
                yield ConversationEnd(reason=reason)
                return

            elif mt == "ping":
                # keep-alive — handle inline, do not surface to runner
                await self._ws.send(json.dumps({
                    "type": "pong",
                    "event_id": msg.get("ping_event", {}).get("event_id"),
                }))

    async def send_tool_result(self, call_id: str, result: str, is_error: bool = False) -> None:
        if self._ws is None or self._closed:
            return
        await self._ws.send(json.dumps({
            "type": "client_tool_result",
            "tool_call_id": call_id,
            "result": result,
            "is_error": is_error,
        }))

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
