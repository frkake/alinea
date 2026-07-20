"""Test that build_system_preamble returns the correct style section for easy."""
from alinea_core.translation.prompts.templates import build_system_preamble


def test_easy_preamble_contains_easy_style_section() -> None:
    preamble = build_system_preamble("easy")
    assert "やさしい訳" in preamble
    assert "平易な日本語" in preamble
    # Must not contain literal-specific text
    assert "語順・構文を可能な限り写像" not in preamble

def test_easy_preamble_contains_shared_rules() -> None:
    preamble = build_system_preamble("easy")
    assert "トークン完全保持" in preamble
    assert "忠実性" in preamble

def test_natural_preamble_unchanged() -> None:
    preamble = build_system_preamble("natural")
    assert "学術書として自然で読みやすい" in preamble
    assert "やさしい訳" not in preamble

def test_literal_preamble_unchanged() -> None:
    preamble = build_system_preamble("literal")
    assert "語順・構文を可能な限り写像" in preamble
    assert "やさしい訳" not in preamble
