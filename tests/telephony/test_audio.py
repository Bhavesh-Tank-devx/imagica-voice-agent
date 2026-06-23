"""Tests for Twilio mu-law <-> ElevenLabs PCM transcoding.

``audioop.ratecv`` is stateless per call, so the upsample is ~4x the input
(2x rate * 2 bytes/sample) minus a couple of bytes of filter slack — assertions
use a small tolerance for the upsample but require exact round-trip sizes.
"""
import pytest

from src.telephony.audio import mulaw_to_pcm16k, pcm16k_to_mulaw


@pytest.mark.parametrize("n", [160, 512])
def test_mulaw_to_pcm_is_about_four_times_larger(n):
    pcm = mulaw_to_pcm16k(b"\x00" * n)
    assert isinstance(pcm, bytes)
    assert abs(len(pcm) - 4 * n) <= 4  # 2x rate * 2 bytes/sample, +/- filter slack


@pytest.mark.parametrize("n", [160, 512])
def test_round_trip_preserves_frame_size(n):
    mulaw_in = bytes((i % 256) for i in range(n))
    mulaw_out = pcm16k_to_mulaw(mulaw_to_pcm16k(mulaw_in))
    assert len(mulaw_out) == n


def test_pcm_to_mulaw_halves_sample_count():
    pcm = b"\x00\x00" * 320  # 320 samples @ 16kHz
    mulaw = pcm16k_to_mulaw(pcm)
    assert isinstance(mulaw, bytes)
    assert len(mulaw) == 160  # downsampled to 8kHz, 1 byte/sample
