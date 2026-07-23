"""Offline smoke test for the ppt-master adapter (``pnpm ppt-master:smoke``).

Builds a native PPTX from the checked-in fixture SVGs using the *pinned*
submodule and the dedicated ``.venv-ppt-master`` interpreter. Uses NO network
and NO LLM: only the native (pure-Python) svg->pptx path runs, and the
subprocess environment is the adapter's allow-list (which omits every proxy and
API-key variable).

Checks:
  - the pipeline runs the fixed script order without error,
  - the produced PPTX passes package-structure validation, and
  - its slide count matches the number of fixture SVG pages.

Exit code 0 on success, non-zero otherwise. Safe to run in CI.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from alinea_worker.presentation.ppt_master import (
    PptMasterAdapter,
    PptMasterError,
    validate_pptx_package,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
SUBMODULE_ROOT = REPO_ROOT / "vendor" / "ppt-master"
FIXTURE_PROJECT = (
    REPO_ROOT
    / "apps"
    / "worker"
    / "tests"
    / "fixtures"
    / "presentation"
    / "minimal_project"
)
DEFAULT_VENV_PYTHON = REPO_ROOT / ".venv-ppt-master" / "bin" / "python"


def _resolve_python() -> Path:
    override = os.environ.get("PPT_MASTER_PYTHON")
    if override:
        return Path(override)
    return DEFAULT_VENV_PYTHON


def main() -> int:
    python_executable = _resolve_python()
    if not python_executable.exists():
        print(
            f"[FAIL] dedicated venv interpreter missing: {python_executable}\n"
            "       Create it: uv venv --python 3.12 .venv-ppt-master && "
            "uv pip install --python .venv-ppt-master/bin/python "
            "python-pptx==1.0.2 lxml==6.1.1 pillow==12.3.0",
            file=sys.stderr,
        )
        return 1

    if not (SUBMODULE_ROOT / "skills" / "ppt-master" / "scripts").is_dir():
        print(
            f"[FAIL] submodule missing: {SUBMODULE_ROOT}\n"
            "       Run: git submodule update --init vendor/ppt-master",
            file=sys.stderr,
        )
        return 1

    svg_source_dir = FIXTURE_PROJECT / "svg_output"
    notes_path = FIXTURE_PROJECT / "notes" / "total.md"
    expected_slides = len(sorted(svg_source_dir.glob("*.svg")))

    adapter = PptMasterAdapter(
        submodule_root=SUBMODULE_ROOT,
        python_executable=python_executable,
        timeout_s=180.0,
    )

    with tempfile.TemporaryDirectory(prefix="ppt-master-smoke-") as tmp:
        work_dir = Path(tmp)
        try:
            result = adapter.convert(
                svg_source_dir=svg_source_dir,
                notes_path=notes_path,
                work_dir=work_dir,
            )
        except PptMasterError as exc:
            print(f"[FAIL] conversion error: {exc}", file=sys.stderr)
            return 1

        # Re-validate independently of the adapter's own check.
        slide_count = validate_pptx_package(result.pptx_path)
        if slide_count != expected_slides:
            print(
                f"[FAIL] slide count {slide_count} != fixture pages {expected_slides}",
                file=sys.stderr,
            )
            return 1

        print(
            f"[OK] native PPTX built offline: {slide_count} slide(s), "
            f"structure valid ({result.pptx_path.name})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
