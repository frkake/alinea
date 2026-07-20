"""Boundary tests for the ppt-master subprocess adapter (Task 27).

These tests pin the *contract* of ``PptMasterAdapter`` without executing any
upstream script for the sequencing/security assertions: a recording runner is
injected so we observe exactly which scripts the adapter would run, in what
order, with which environment, and that a quality-check failure short-circuits
the remaining stages. The PPTX package validator is exercised directly against
synthetic archives.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from alinea_worker.presentation.ppt_master import (
    ALLOWED_ENV,
    PPT_MASTER_REVISION,
    SCRIPT_ORDER,
    CommandResult,
    PptMasterAdapter,
    PptMasterCommand,
    PptMasterError,
    PptMasterQualityError,
    validate_pptx_package,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VENDOR_ROOT = REPO_ROOT / "vendor" / "ppt-master"
FIXTURE_PROJECT = Path(__file__).resolve().parent / "fixtures" / "presentation" / "minimal_project"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _write_min_pptx(path: Path, *, slide_count: int, external: bool = False) -> None:
    """Write a minimal-but-structurally-valid .pptx (Open Packaging zip)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
            '/package/2006/content-types"/>',
        )
        zf.writestr("ppt/presentation.xml", "<p:presentation/>")
        for index in range(1, slide_count + 1):
            zf.writestr(f"ppt/slides/slide{index}.xml", "<p:sld/>")
        if external:
            zf.writestr(
                "ppt/slides/_rels/slide1.xml.rels",
                '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                'openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId9" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/image" Target="https://evil.example/x.png" '
                'TargetMode="External"/></Relationships>',
            )


class RecordingRunner:
    """Injectable runner that records the logical command labels and env."""

    def __init__(self, *, fail_script: str | None = None, slide_count: int = 2) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.envs: list[dict[str, str]] = []
        self.timeouts: list[float] = []
        self.fail_script = fail_script
        self.slide_count = slide_count

    def __call__(
        self,
        command: PptMasterCommand,
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float,
    ) -> CommandResult:
        self.calls.append(command.label)
        self.envs.append(dict(env))
        self.timeouts.append(timeout_s)
        if command.script.name == self.fail_script:
            return CommandResult(returncode=1, stdout="quality failed", stderr="boom")
        if command.script.name == "svg_to_pptx.py":
            project_dir = Path(command.argv[0])
            _write_min_pptx(
                project_dir / "exports" / "deck.pptx", slide_count=self.slide_count
            )
        return CommandResult(returncode=0, stdout="ok", stderr="")


def _make_adapter(runner: Any, **kwargs: Any) -> PptMasterAdapter:
    return PptMasterAdapter(
        submodule_root=VENDOR_ROOT,
        python_executable=Path("/nonexistent/python"),
        runner=runner,
        timeout_s=45.0,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
def test_pinned_revision_and_constants() -> None:
    assert PPT_MASTER_REVISION == "0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f"
    assert SCRIPT_ORDER == (
        "svg_quality_checker.py",
        "total_md_split.py",
        "finalize_svg.py",
        "svg_to_pptx.py",
    )
    assert ALLOWED_ENV == ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONIOENCODING")


# --------------------------------------------------------------------------- #
# Command sequence + environment (the brief's core assert block)
# --------------------------------------------------------------------------- #
def test_convert_runs_expected_command_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Secrets present in the parent environment must never reach the subprocess.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-should-not-leak")

    runner = RecordingRunner(slide_count=2)
    adapter = _make_adapter(runner)

    conversion = adapter.convert(
        svg_source_dir=FIXTURE_PROJECT / "svg_output",
        notes_path=FIXTURE_PROJECT / "notes" / "total.md",
        work_dir=tmp_path,
    )

    project_dir = conversion.project_dir

    assert runner.calls == [
        ("project_manager.py", "init"),
        ("svg_quality_checker.py", project_dir),
        ("total_md_split.py", project_dir),
        ("finalize_svg.py", project_dir),
        ("svg_to_pptx.py", project_dir, "--merge-paragraphs"),
    ]

    # No LLM API keys in any captured environment.
    for captured_env in runner.envs:
        assert "OPENAI_API_KEY" not in captured_env
        assert "ANTHROPIC_API_KEY" not in captured_env
        assert set(captured_env).issubset(set(ALLOWED_ENV))

    # Per-command timeout honoured.
    assert runner.timeouts == [45.0] * 5

    # Conversion result reflects the produced package.
    assert conversion.slide_count == 2
    assert conversion.pptx_path.exists()
    assert conversion.pptx_path.suffix == ".pptx"


def test_quality_failure_skips_downstream(tmp_path: Path) -> None:
    runner = RecordingRunner(fail_script="svg_quality_checker.py")
    adapter = _make_adapter(runner)

    with pytest.raises(PptMasterQualityError):
        adapter.convert(
            svg_source_dir=FIXTURE_PROJECT / "svg_output",
            notes_path=FIXTURE_PROJECT / "notes" / "total.md",
            work_dir=tmp_path,
        )

    # Only init + the quality check ran; the last three stages were skipped.
    assert [label[0] for label in runner.calls] == [
        "project_manager.py",
        "svg_quality_checker.py",
    ]


# --------------------------------------------------------------------------- #
# Security boundary
# --------------------------------------------------------------------------- #
def test_scripts_resolve_inside_submodule_scripts_dir(tmp_path: Path) -> None:
    runner = RecordingRunner()
    adapter = _make_adapter(runner)
    scripts_dir = adapter.scripts_dir
    assert scripts_dir == VENDOR_ROOT / "skills" / "ppt-master" / "scripts"

    for name in ("project_manager.py", *SCRIPT_ORDER):
        argv = adapter.build_argv(
            PptMasterCommand(script=scripts_dir / name, argv=(), label=(name,))
        )
        # A list argv is what enables shell=False execution.
        assert isinstance(argv, list)
        assert argv[0] == str(adapter.python_executable)
        assert argv[1] == str(scripts_dir / name)


def test_rejects_script_outside_scripts_dir() -> None:
    runner = RecordingRunner()
    adapter = _make_adapter(runner)
    with pytest.raises(PptMasterError):
        adapter.build_argv(
            PptMasterCommand(
                script=Path("/usr/bin/evil.py"), argv=(), label=("evil.py",)
            )
        )
    with pytest.raises(PptMasterError):
        # Path traversal escaping the scripts dir must also be rejected.
        adapter.build_argv(
            PptMasterCommand(
                script=adapter.scripts_dir / ".." / ".." / "SKILL.md",
                argv=(),
                label=("SKILL.md",),
            )
        )


def test_default_runner_uses_shell_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(args: Any, **kwargs: Any) -> Any:
        captured["args"] = args
        captured["kwargs"] = kwargs

        class _Completed:
            returncode = 0
            stdout = "out"
            stderr = "err"

        return _Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = PptMasterAdapter(
        submodule_root=VENDOR_ROOT,
        python_executable=Path("/nonexistent/python"),
        timeout_s=10.0,
    )
    command = PptMasterCommand(
        script=adapter.scripts_dir / "svg_quality_checker.py",
        argv=(str(tmp_path),),
        label=("svg_quality_checker.py", tmp_path),
    )
    result = adapter._run(command, cwd=tmp_path, env={"PATH": "/usr/bin"}, timeout_s=10.0)

    assert result.returncode == 0
    assert isinstance(captured["args"], list)
    # shell=False is the security-critical default.
    assert captured["kwargs"].get("shell", False) is False
    assert captured["kwargs"].get("cwd") == tmp_path
    assert captured["kwargs"].get("timeout") == 10.0
    assert "OPENAI_API_KEY" not in captured["kwargs"].get("env", {})


# --------------------------------------------------------------------------- #
# PPTX package validation
# --------------------------------------------------------------------------- #
def test_validate_pptx_accepts_well_formed_package(tmp_path: Path) -> None:
    pptx = tmp_path / "ok.pptx"
    _write_min_pptx(pptx, slide_count=3)
    slide_count = validate_pptx_package(pptx)
    assert slide_count == 3


def test_validate_pptx_rejects_zero_byte(tmp_path: Path) -> None:
    pptx = tmp_path / "empty.pptx"
    pptx.write_bytes(b"")
    with pytest.raises(PptMasterError):
        validate_pptx_package(pptx)


def test_validate_pptx_rejects_broken_zip(tmp_path: Path) -> None:
    pptx = tmp_path / "broken.pptx"
    pptx.write_bytes(b"not a zip at all")
    with pytest.raises(PptMasterError):
        validate_pptx_package(pptx)


def test_validate_pptx_rejects_missing_slides(tmp_path: Path) -> None:
    pptx = tmp_path / "noslides.pptx"
    with zipfile.ZipFile(pptx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/presentation.xml", "<p:presentation/>")
    with pytest.raises(PptMasterError):
        validate_pptx_package(pptx)


def test_validate_pptx_rejects_external_image_relationship(tmp_path: Path) -> None:
    pptx = tmp_path / "external.pptx"
    _write_min_pptx(pptx, slide_count=1, external=True)
    with pytest.raises(PptMasterError):
        validate_pptx_package(pptx)
