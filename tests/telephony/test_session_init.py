"""Tests for the ElevenLabs session-init payload and agent URL selection."""
from src.config import elevenlabs_settings
from src.constants import AgentType
from src.telephony.bridge import _build_session_init, _elevenlabs_url


def test_imagica_session_init_has_pcm_output_and_no_dynamic_vars(imagica_cart):
    payload = _build_session_init(imagica_cart, AgentType.IMAGICA)
    assert payload["type"] == "conversation_initiation_client_data"
    assert payload["conversation_config_override"]["tts"]["output_format"] == "pcm_16000"
    assert "dynamic_variables" not in payload


def test_kaya_session_init_includes_dynamic_variables(kaya_cart):
    payload = _build_session_init(kaya_cart, AgentType.KAYA)
    dyn = payload["dynamic_variables"]
    assert dyn["customer_name"] == kaya_cart["customer_name"]
    assert dyn["customer_phone"] == kaya_cart["customer_phone"]
    assert dyn["city"] == kaya_cart["city"]
    assert dyn["call_type"] == "OUTBOUND"


def test_kaya_asr_keywords_only_when_enabled(kaya_cart, monkeypatch):
    monkeypatch.setattr(elevenlabs_settings, "KAYA_ASR_KEYWORDS", False)
    payload = _build_session_init(kaya_cart, AgentType.KAYA)
    assert "asr" not in payload["conversation_config_override"]

    monkeypatch.setattr(elevenlabs_settings, "KAYA_ASR_KEYWORDS", True)
    payload = _build_session_init(kaya_cart, AgentType.KAYA)
    assert payload["conversation_config_override"]["asr"]["keywords"]


def test_elevenlabs_url_selects_agent(monkeypatch):
    monkeypatch.setattr(elevenlabs_settings, "ELEVENLABS_AGENT_ID", "agent_imagica")
    monkeypatch.setattr(elevenlabs_settings, "ELEVENLABS_KAYA_AGENT_ID", "agent_kaya")
    assert _elevenlabs_url(AgentType.IMAGICA).endswith("agent_id=agent_imagica")
    assert _elevenlabs_url(AgentType.KAYA).endswith("agent_id=agent_kaya")
    assert _elevenlabs_url(AgentType.IMAGICA).startswith("wss://")
