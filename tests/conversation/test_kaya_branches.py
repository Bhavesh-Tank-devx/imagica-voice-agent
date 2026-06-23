"""Tests for pincode / city -> Kaya branch resolution."""
from src.conversation.kaya_branches import get_closest_branches


def test_pincode_resolves_to_city_and_branches():
    result = get_closest_branches(pincode="395007")
    assert result["city"] == "Surat"
    assert result["branches"] == ["Vesu", "Ghod Dod Road"]
    assert result["confirm_address"] is False


def test_city_name_exact_match():
    result = get_closest_branches(city="surat")
    assert result["city"] == "Surat"
    assert "Vesu" in result["message"]


def test_unknown_area_asks_for_city():
    result = get_closest_branches(pincode="000000")
    assert result["city"] is None
    assert result["branches"] == []
    assert "could not find" in result["message"].lower()


def test_confirm_address_city_has_no_listed_branches():
    # Jaipur is a "confirm address" city — known, but branches unlisted.
    result = get_closest_branches(city="Jaipur")
    assert result["city"] == "Jaipur"
    assert result["branches"] == []
    assert result["confirm_address"] is True


def test_two_to_three_branches_are_all_named():
    result = get_closest_branches(city="Surat")
    assert "Vesu and Ghod Dod Road" in result["message"]


def test_many_branches_ask_for_area_first():
    result = get_closest_branches(city="Mumbai")
    assert len(result["branches"]) >= 9
    assert "area" in result["message"].lower()
    assert result["confirm_address"] is False


def test_pincode_takes_precedence_over_city():
    # 395xxx -> Surat even if a different city is also passed.
    result = get_closest_branches(pincode="395007", city="Mumbai")
    assert result["city"] == "Surat"
