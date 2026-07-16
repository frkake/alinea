"""Worker dispatches reason='easy' to translate_section (same as literal)."""
from alinea_worker.tasks.translate import _SECTION_REASONS


def test_easy_in_section_reasons():
    assert "easy" in _SECTION_REASONS
