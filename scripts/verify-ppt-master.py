#!/usr/bin/env python3
"""Verify the vendored ppt-master submodule is pinned and structurally intact.

Asserts:
1. The submodule HEAD equals the pinned revision (unless --revision overrides it,
   used by the update flow to validate an *update candidate*).
2. The expected script package layout exists (the scripts the adapter runs).

Exits non-zero with a human-readable reason on any failure. No network access.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Keep this in sync with alinea_worker.presentation.ppt_master.PPT_MASTER_REVISION.
PINNED_REVISION = "0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f"

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBMODULE_ROOT = REPO_ROOT / "vendor" / "ppt-master"
SCRIPTS_DIR = SUBMODULE_ROOT / "skills" / "ppt-master" / "scripts"

# Scripts the adapter is allowed to execute, plus the packages they import.
REQUIRED_SCRIPTS = (
    "project_manager.py",
    "svg_quality_checker.py",
    "total_md_split.py",
    "finalize_svg.py",
    "svg_to_pptx.py",
)
REQUIRED_PACKAGE_FILES = (
    "svg_to_pptx/__init__.py",
    "svg_to_pptx/pptx_cli.py",
    "svg_to_pptx/pptx_builder.py",
)


def _head_revision(submodule_root: Path) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(submodule_root), "rev-parse", "HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify pinned ppt-master submodule")
    parser.add_argument(
        "--revision",
        default=PINNED_REVISION,
        help="Expected HEAD revision (defaults to the pinned SHA).",
    )
    args = parser.parse_args(argv)

    problems: list[str] = []

    if not SUBMODULE_ROOT.is_dir():
        print(f"[FAIL] Submodule missing: {SUBMODULE_ROOT}", file=sys.stderr)
        print("       Run: git submodule update --init vendor/ppt-master", file=sys.stderr)
        return 1

    try:
        head = _head_revision(SUBMODULE_ROOT)
    except subprocess.CalledProcessError as exc:
        print(f"[FAIL] Unable to read submodule HEAD: {exc}", file=sys.stderr)
        return 1

    if head != args.revision:
        problems.append(
            f"HEAD {head} does not match expected revision {args.revision}"
        )

    if not SCRIPTS_DIR.is_dir():
        problems.append(f"scripts dir missing: {SCRIPTS_DIR}")
    else:
        for name in REQUIRED_SCRIPTS:
            if not (SCRIPTS_DIR / name).is_file():
                problems.append(f"missing script: {name}")
        for rel in REQUIRED_PACKAGE_FILES:
            if not (SCRIPTS_DIR / rel).is_file():
                problems.append(f"missing package file: {rel}")

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1

    print(f"[OK] ppt-master pinned at {head}")
    print(f"[OK] script package intact under {SCRIPTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
