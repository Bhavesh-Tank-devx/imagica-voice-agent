"""
benchmark/sim_user.py — the simulated caller (Tier 1).

An LLM role-plays the scenario persona, pursuing the goal and using the persona's
known facts (name, email, city, date…). Its spoken lines are turned into audio
and pushed through the µ-law 8 kHz codec so the stack's STT is exercised on
telephony-grade audio — the cheap-but-valid way to surface ASR weakness without
making real phone calls.

Reuses the production codec (voice_agent.pcm16k_to_mulaw / mulaw_to_pcm16k) so
the degradation matches what a real Twilio call would impose.
"""
from __future__ import annotations

import logging
import os

from src.telephony import mulaw_to_pcm16k, pcm16k_to_mulaw  # reuse production codec

logger = logging.getLogger("benchmark.sim_user")

USER_MODEL = os.getenv("BENCH_SIM_USER_MODEL", "claude-haiku-4-5-20251001")


def _system_prompt(persona) -> str:
    facts = "\n".join(f"  - {k}: {v}" for k, v in persona.facts.items())
    return (
        "You are role-playing a CUSTOMER on a phone call with an AI voice agent. "
        "Stay fully in character. Speak naturally and BRIEFLY, like a real phone call — "
        "one short turn at a time, never narrate.\n\n"
        f"PERSONA: {persona.description}\n"
        f"YOUR GOAL: {persona.goal}\n"
        f"SPEAKING STYLE: {persona.style or 'natural'}\n"
        f"LANGUAGE: {persona.language} "
        "(if hinglish, mix Hindi and English the way urban Indians do).\n"
        f"FACTS YOU KNOW (reveal only when asked, and when spelling an email or "
        f"reading digits, do it slowly, letter/number at a time):\n{facts}\n\n"
        "Rules:\n"
        "- Output ONLY your spoken words, nothing else.\n"
        "- When your goal is achieved, or you want to hang up, output exactly: [HANGUP]\n"
        "- Do not invent facts not listed above; if asked something you don't know, say so."
    )


class SimUser:
    """Generates the next caller utterance from conversation history.

    Primary backend: Anthropic Messages API (cheap Haiku). If the SDK or key is
    unavailable, falls back to a deterministic fact-walker so offline pipeline
    tests still run (clearly lower fidelity)."""

    def __init__(self, persona, model: str = USER_MODEL):
        self.persona = persona
        self.model = model
        self._client = None
        self._fallback_idx = 0
        try:
            import anthropic  # lazy
            if os.getenv("ANTHROPIC_API_KEY"):
                self._client = anthropic.Anthropic()
        except Exception as exc:  # pragma: no cover
            logger.warning("[sim_user] Anthropic unavailable (%s) — deterministic fallback", exc)

    def next_utterance(self, history: list[dict]) -> str | None:
        """history: [{"role":"agent"|"user","text":...}]. Returns next line or None to hang up."""
        if self._client is not None:
            return self._llm_turn(history)
        return self._fallback_turn(history)

    def _llm_turn(self, history: list[dict]) -> str | None:
        messages = []
        for h in history:
            # agent speech = "user" role to the sim (it's what the sim hears);
            # sim's own lines = "assistant".
            role = "user" if h["role"] == "agent" else "assistant"
            messages.append({"role": role, "content": h["text"]})
        if not messages or messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": "(the agent has dialed you; greet/respond)"})
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=120,
                system=_system_prompt(self.persona),
                messages=messages,
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        except Exception as exc:
            logger.error("[sim_user] LLM error: %s — falling back", exc)
            return self._fallback_turn(history)
        if "[HANGUP]" in text or not text:
            return None
        return text

    def _fallback_turn(self, history: list[dict]) -> str | None:
        """Deterministic fact-walker — low fidelity, for offline tests only."""
        f = self.persona.facts
        script = [
            "Yes, that's me.",
            f"I'm interested in {f.get('concern', 'a consultation')}.",
            f"I'm in {f.get('city', 'the city')}, pincode {f.get('pincode', '')}.",
            f"My name is {f.get('name', '')}.",
            f"My email is {f.get('email', '')}.",
            f"{f.get('date', '')} at {f.get('time', '')} works for me.",
            "Yes, please book it.",
        ]
        if self._fallback_idx >= len(script):
            return None
        line = script[self._fallback_idx]
        self._fallback_idx += 1
        return line


# ---------------------------------------------------------------------------
# Text → telephony-grade audio
# ---------------------------------------------------------------------------

def degrade_to_telephony(pcm16k: bytes) -> bytes:
    """Round-trip PCM 16 kHz through µ-law 8 kHz and back, imposing exactly the
    bandwidth/quantization loss a real Twilio call would. This is what makes T1
    sim audio a valid probe of each stack's STT (not clean studio audio)."""
    return mulaw_to_pcm16k(pcm16k_to_mulaw(pcm16k))


def tts_telephony(text: str, voice_id: str | None = None) -> bytes:
    """ElevenLabs TTS → PCM 16 kHz → telephony-degraded PCM. Used for T1 with
    real stacks. Returns raw PCM 16-bit 16 kHz mono bytes.

    Kept import-lazy so the harness loads without the SDK for mock/offline runs.
    """
    from elevenlabs.client import ElevenLabs  # lazy

    client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    voice_id = voice_id or os.getenv("BENCH_SIM_USER_VOICE", "EXAVITQu4vr4xnSDxMaL")
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_flash_v2_5",
        output_format="pcm_16000",
    )
    pcm = b"".join(audio) if hasattr(audio, "__iter__") else audio
    return degrade_to_telephony(pcm)
