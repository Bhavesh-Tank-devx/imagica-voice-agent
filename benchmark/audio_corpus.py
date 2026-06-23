"""
benchmark/audio_corpus.py — Tier 2 fixed audio-replay corpus.

The same audio is replayed against every stack's STT, so WER / intent / ASR-error
are measured apples-to-apples on real telephony-grade audio. This tier carries
the powered comparison.

A corpus is a manifest (JSON) of utterances:
  {
    "id": "utt_001",
    "audio_path": "corpus/utt_001.wav",   # raw PCM16 mono or WAV
    "gold_transcript": "my email is neha dot sharma at gmail dot com",
    "language": "hinglish",
    "expected_slots": {"email": "neha.sharma@gmail.com"}   # optional, for ASR-error scoring
  }

PROVENANCE MATTERS: for valid WER on the Hinglish use case the audio must be
REAL accented telephony speech (recorded calls, team recordings, or a public
Indic set like IndicVoices/Svarah). `synthesize_starter_corpus()` produces a
SYNTHETIC TTS corpus — explicitly labeled — only so the pipeline can be tested
end-to-end before a real corpus is collected. The report flags corpus provenance.
"""
from __future__ import annotations

import json
import logging
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.corpus")

CORPUS_DIR = Path(__file__).parent / "corpus"


@dataclass
class Utterance:
    id: str
    audio_path: str
    gold_transcript: str
    language: str = "english"
    expected_slots: dict = field(default_factory=dict)
    synthetic: bool = False  # True if TTS-generated (NOT valid for real WER claims)

    def pcm16k(self) -> bytes:
        """Load audio as raw PCM 16-bit 16 kHz mono bytes."""
        p = Path(self.audio_path)
        if not p.is_absolute():
            p = CORPUS_DIR / p.name
        if p.suffix == ".wav":
            with wave.open(str(p), "rb") as w:
                return w.readframes(w.getnframes())
        return p.read_bytes()


def load_corpus(manifest: str | Path) -> list[Utterance]:
    data = json.loads(Path(manifest).read_text())
    return [Utterance(**u) for u in data["utterances"]]


def synthesize_starter_corpus(scenarios, out_dir: Path = CORPUS_DIR) -> Path:
    """Generate a SYNTHETIC TTS corpus from scenario facts for pipeline testing.

    Produces telephony-degraded WAVs + a manifest with synthetic=True on every
    utterance. NOT a substitute for real accented audio — WER on this corpus only
    validates the measurement code, not the stacks' real Hinglish accuracy.
    """
    from .sim_user import tts_telephony  # lazy (needs ElevenLabs)

    out_dir.mkdir(parents=True, exist_ok=True)
    utterances = []
    for sc in scenarios:
        f = sc.persona.facts
        if not f.get("email"):
            continue
        spoken = f"My email is {f['email']}. I am in {f.get('city','')}, pincode {f.get('pincode','')}."
        uid = f"{sc.id}_email"
        pcm = tts_telephony(spoken)
        wav_path = out_dir / f"{uid}.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(pcm)
        utterances.append({
            "id": uid, "audio_path": wav_path.name, "gold_transcript": spoken,
            "language": sc.persona.language,
            "expected_slots": {k: f[k] for k in ("email", "pincode") if f.get(k)},
            "synthetic": True,
        })
    manifest = out_dir / "manifest_synthetic.json"
    manifest.write_text(json.dumps({"provenance": "SYNTHETIC_TTS", "utterances": utterances}, indent=2))
    logger.warning("[corpus] wrote %d SYNTHETIC utterances to %s — not valid for real WER", len(utterances), manifest)
    return manifest
