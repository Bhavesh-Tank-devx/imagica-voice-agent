"""Tests for the system-prompt builders."""
from src.conversation.imagica_prompt import build_system_prompt
from src.conversation.kaya_prompt import build_kaya_system_prompt


def test_imagica_prompt_injects_cart_fields(imagica_cart):
    prompt = build_system_prompt(imagica_cart)
    assert imagica_cart["customer_name"] in prompt
    assert imagica_cart["visit_date"] in prompt
    assert str(imagica_cart["total_amount"]) in prompt
    assert imagica_cart["park_name"] in prompt
    # tickets summary "2 Adult"
    assert "2 Adult" in prompt
    # attempt number referenced
    assert "attempt #1" in prompt


def test_imagica_prompt_keeps_feminine_gender_rule(imagica_cart):
    prompt = build_system_prompt(imagica_cart)
    assert "bol rahi hoon" in prompt


def test_kaya_prompt_replaces_dynamic_placeholders(kaya_cart):
    prompt = build_kaya_system_prompt(kaya_cart)
    assert kaya_cart["customer_name"] in prompt
    assert "{{customer_name}}" not in prompt
    assert "{{call_type}}" not in prompt
    assert "{{customer_phone}}" not in prompt
    # OUTBOUND call type substituted in the Context block
    assert "OUTBOUND" in prompt
