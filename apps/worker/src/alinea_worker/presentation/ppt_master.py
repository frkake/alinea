"""Locked-down subprocess adapter for the pinned ppt-master toolchain.

Alinea's paper->PPTX feature reuses the vendored ``ppt-master`` project, but we
never trust it with ambient authority. Every upstream script is executed from
the *pinned submodule* only, with:

- ``shell=False`` and an explicit ``argv`` list (no shell parsing),
- a job-specific working directory,
- an allow-listed environment that deliberately omits ``OPENAI_API_KEY`` /
  ``ANTHROPIC_API_KEY`` (upstream must never reach an LLM or the network at
  runtime),
- a per-command timeout, and
- stdout/stderr truncated to 64 KiB each so a runaway script cannot exhaust
  memory or leak large payloads into logs.

The conversion runs a fixed pipeline::

    project_manager.py init
    svg_quality_checker.py   <project>
    total_md_split.py        <project>
    finalize_svg.py          <project>
    svg_to_pptx.py           <project> --merge-paragraphs

If the quality check fails, the remaining three stages are skipped. The
produced ``.pptx`` is opened as a ZIP and structurally validated before it is
returned.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import structlog

log = structlog.get_logger("alinea.worker.presentation")

# --------------------------------------------------------------------------- #
# Pinned contract
# --------------------------------------------------------------------------- #
#: Exact upstream commit the submodule must point at (ppt-master v2.8.0).
PPT_MASTER_REVISION = "0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f"

#: Upstream scripts executed, in order. A quality-check failure short-circuits
#: the remaining three stages.
SCRIPT_ORDER: tuple[str, ...] = (
    "svg_quality_checker.py",
    "total_md_split.py",
    "finalize_svg.py",
    "svg_to_pptx.py",
)

#: Environment variables allowed to reach the subprocess. LLM API keys are
#: deliberately absent so upstream cannot call a model or the network.
ALLOWED_ENV: tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONIOENCODING")

#: Upstream project_manager subcommand used to initialise a project skeleton.
_PROJECT_MANAGER = "project_manager.py"

#: Truncate captured stdout/stderr to this many bytes each.
_MAX_STREAM_BYTES = 64 * 1024

#: Default canvas format matching the fixture SVG viewBox (1280x720).
_DEFAULT_FORMAT = "ppt169"

#: Deterministic project name used under the job working directory.
_DEFAULT_PROJECT_NAME = "deck"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class PptMasterError(RuntimeError):
    """Base error for ppt-master adapter failures."""


class PptMasterQualityError(PptMasterError):
    """Raised when the SVG quality check fails (downstream stages skipped)."""


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PptMasterCommand:
    """A single upstream invocation.

    ``label`` is a human-readable summary used for assertions and logging;
    ``argv`` is the exact argument vector passed to the script.
    """

    script: Path
    argv: tuple[str, ...]
    label: tuple[object, ...]


@dataclass(frozen=True)
class CommandResult:
    """Result of running one command (streams already truncated)."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a successful paper->PPTX conversion."""

    project_dir: Path
    pptx_path: Path
    slide_count: int
    commands: tuple[tuple[object, ...], ...] = field(default_factory=tuple)


Runner = Callable[..., CommandResult]


# --------------------------------------------------------------------------- #
# PPTX package validation
# --------------------------------------------------------------------------- #
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _relationship_is_external_image(element: ET.Element) -> bool:
    rel_type = element.get("Type", "")
    target_mode = element.get("TargetMode", "")
    target = element.get("Target", "")
    is_image = rel_type.rstrip("/").endswith("image")
    is_external = target_mode == "External" or target.startswith(("http://", "https://"))
    return is_image and is_external


def validate_pptx_package(pptx_path: Path) -> int:
    """Validate a PPTX Open Packaging archive; return its slide count.

    Rejects: 0-byte output, broken ZIP, missing ``[Content_Types].xml`` or
    ``ppt/presentation.xml``, no ``ppt/slides/slide*.xml``, and any external
    image relationship.
    """
    pptx_path = Path(pptx_path)
    if not pptx_path.exists():
        raise PptMasterError(f"PPTX output missing: {pptx_path}")
    if pptx_path.stat().st_size == 0:
        raise PptMasterError(f"PPTX output is 0 bytes: {pptx_path}")

    try:
        with zipfile.ZipFile(pptx_path) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise PptMasterError(f"PPTX archive has a corrupt entry: {bad}")
            names = set(archive.namelist())

            if "[Content_Types].xml" not in names:
                raise PptMasterError("PPTX archive missing [Content_Types].xml")
            if "ppt/presentation.xml" not in names:
                raise PptMasterError("PPTX archive missing ppt/presentation.xml")

            slides = [
                name
                for name in names
                if name.startswith("ppt/slides/slide")
                and name.endswith(".xml")
                and "/" not in name[len("ppt/slides/") :]
            ]
            if not slides:
                raise PptMasterError("PPTX archive has no ppt/slides/slide*.xml")

            for name in names:
                if not name.endswith(".rels"):
                    continue
                raw = archive.read(name)
                # Defense in depth: refuse any DTD/entity declaration so the
                # stdlib parser can never be coaxed into entity expansion. The
                # OPC spec forbids DTDs in relationship parts anyway.
                if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
                    raise PptMasterError(f"PPTX relationship XML declares a DTD/entity: {name}")
                try:
                    # nosec B314 - input is DTD/entity-free (checked above) and
                    # ElementTree does not resolve external entities.
                    root = ET.fromstring(raw)  # noqa: S314
                except ET.ParseError as exc:
                    raise PptMasterError(f"PPTX relationship XML is malformed: {name}") from exc
                for rel in root.iter(f"{{{_REL_NS}}}Relationship"):
                    if _relationship_is_external_image(rel):
                        raise PptMasterError(
                            f"PPTX contains an external image relationship in {name}"
                        )
    except zipfile.BadZipFile as exc:
        raise PptMasterError(f"PPTX output is not a valid ZIP: {pptx_path}") from exc

    return len(slides)


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
class PptMasterAdapter:
    """Run the pinned ppt-master pipeline behind a hardened subprocess boundary."""

    def __init__(
        self,
        *,
        submodule_root: Path,
        python_executable: Path,
        runner: Runner | None = None,
        timeout_s: float = 120.0,
        project_name: str = _DEFAULT_PROJECT_NAME,
        canvas_format: str = _DEFAULT_FORMAT,
    ) -> None:
        self.submodule_root = Path(submodule_root)
        self.python_executable = Path(python_executable)
        self.timeout_s = float(timeout_s)
        self.project_name = project_name
        self.canvas_format = canvas_format
        self._runner: Runner = runner if runner is not None else self._run

    @property
    def scripts_dir(self) -> Path:
        """Directory that holds the *only* scripts we are allowed to run."""
        return self.submodule_root / "skills" / "ppt-master" / "scripts"

    # -- command construction ------------------------------------------------ #
    def build_argv(self, command: PptMasterCommand) -> list[str]:
        """Return the ``shell=False`` argv for ``command`` after path validation.

        The script must resolve to a file inside :attr:`scripts_dir`; anything
        else (absolute paths elsewhere, ``..`` traversal) is rejected.
        """
        scripts_dir = self.scripts_dir.resolve()
        script = Path(command.script).resolve()
        if not script.is_relative_to(scripts_dir):
            raise PptMasterError(
                f"Refusing to run script outside {scripts_dir}: {command.script}"
            )
        return [str(self.python_executable), str(script), *command.argv]

    def _script(self, name: str) -> Path:
        return self.scripts_dir / name

    # -- default runner (shell=False) ---------------------------------------- #
    def _run(
        self,
        command: PptMasterCommand,
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_s: float,
    ) -> CommandResult:
        argv = self.build_argv(command)
        try:
            completed = subprocess.run(  # noqa: S603 - argv validated, shell=False
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout_s,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PptMasterError(
                f"ppt-master command timed out after {timeout_s}s: {command.label[0]}"
            ) from exc
        stdout = _truncate(completed.stdout or "")
        stderr = _truncate(completed.stderr or "")
        return CommandResult(returncode=completed.returncode, stdout=stdout, stderr=stderr)

    def _dispatch(
        self,
        command: PptMasterCommand,
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> CommandResult:
        result = self._runner(command, cwd=cwd, env=env, timeout_s=self.timeout_s)
        log.info(
            "ppt_master.command",
            script=command.label[0],
            returncode=result.returncode,
        )
        return result

    # -- environment --------------------------------------------------------- #
    def _build_env(self) -> dict[str, str]:
        env: dict[str, str] = {key: os.environ[key] for key in ALLOWED_ENV if key in os.environ}
        # Force deterministic UTF-8 I/O for the upstream scripts.
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    # -- pipeline ------------------------------------------------------------ #
    def convert(
        self,
        *,
        svg_source_dir: Path,
        notes_path: Path | None,
        work_dir: Path,
    ) -> ConversionResult:
        """Run the full pipeline and return the validated PPTX."""
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        env = self._build_env()

        commands: list[tuple[object, ...]] = []

        # 1) project_manager.py init -- create the canonical project skeleton.
        init_command = PptMasterCommand(
            script=self._script(_PROJECT_MANAGER),
            argv=(
                "init",
                self.project_name,
                "--format",
                self.canvas_format,
                "--dir",
                str(work_dir),
            ),
            label=(_PROJECT_MANAGER, "init"),
        )
        init_result = self._dispatch(init_command, cwd=work_dir, env=env)
        commands.append(init_command.label)
        if init_result.returncode != 0:
            raise PptMasterError(
                f"project_manager init failed (rc={init_result.returncode})"
            )

        project_dir = self._resolve_project_dir(work_dir)
        self._seed_project(project_dir, svg_source_dir, notes_path)

        # 2) svg_quality_checker.py -- gate for the remaining stages.
        quality_command = PptMasterCommand(
            script=self._script("svg_quality_checker.py"),
            argv=(str(project_dir),),
            label=("svg_quality_checker.py", project_dir),
        )
        quality_result = self._dispatch(quality_command, cwd=work_dir, env=env)
        commands.append(quality_command.label)
        if quality_result.returncode != 0:
            raise PptMasterQualityError(
                "svg_quality_checker reported errors; skipping downstream stages "
                f"(rc={quality_result.returncode})"
            )

        # 3) total_md_split.py -- split speaker notes.
        # 4) finalize_svg.py -- post-process SVGs.
        for name in ("total_md_split.py", "finalize_svg.py"):
            command = PptMasterCommand(
                script=self._script(name),
                argv=(str(project_dir),),
                label=(name, project_dir),
            )
            result = self._dispatch(command, cwd=work_dir, env=env)
            commands.append(command.label)
            if result.returncode != 0:
                raise PptMasterError(f"{name} failed (rc={result.returncode})")

        # 5) svg_to_pptx.py --merge-paragraphs -- native PPTX export.
        export_command = PptMasterCommand(
            script=self._script("svg_to_pptx.py"),
            argv=(str(project_dir), "--merge-paragraphs"),
            label=("svg_to_pptx.py", project_dir, "--merge-paragraphs"),
        )
        export_result = self._dispatch(export_command, cwd=work_dir, env=env)
        commands.append(export_command.label)
        if export_result.returncode != 0:
            raise PptMasterError(f"svg_to_pptx failed (rc={export_result.returncode})")

        pptx_path = self._latest_export(project_dir)
        slide_count = validate_pptx_package(pptx_path)
        log.info(
            "ppt_master.converted",
            slide_count=slide_count,
            pptx=str(pptx_path),
        )
        return ConversionResult(
            project_dir=project_dir,
            pptx_path=pptx_path,
            slide_count=slide_count,
            commands=tuple(commands),
        )

    # -- helpers ------------------------------------------------------------- #
    def _resolve_project_dir(self, work_dir: Path) -> Path:
        """Find the project directory init created, else a deterministic fallback.

        ``project_manager.py init`` names the project ``<name>_<format>_<date>``;
        we pick the newest match. When no directory exists (e.g. a test injects a
        recording runner that does not actually create one) we fall back to a
        stable ``<work_dir>/<name>`` path.
        """
        candidates = sorted(
            (p for p in work_dir.glob(f"{self.project_name}_*") if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
        if candidates:
            return candidates[-1]
        return work_dir / self.project_name

    def _seed_project(
        self,
        project_dir: Path,
        svg_source_dir: Path,
        notes_path: Path | None,
    ) -> None:
        svg_out = project_dir / "svg_output"
        notes_dir = project_dir / "notes"
        for sub in ("svg_output", "svg_final", "notes", "exports"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

        svg_source_dir = Path(svg_source_dir)
        svg_files = sorted(svg_source_dir.glob("*.svg"))
        if not svg_files:
            raise PptMasterError(f"No SVG files found in {svg_source_dir}")
        for svg in svg_files:
            shutil.copy2(svg, svg_out / svg.name)

        if notes_path is not None:
            notes_path = Path(notes_path)
            if notes_path.exists():
                shutil.copy2(notes_path, notes_dir / "total.md")

    def _latest_export(self, project_dir: Path) -> Path:
        exports = project_dir / "exports"
        pptx_files = sorted(
            exports.glob("*.pptx"),
            key=lambda p: p.stat().st_mtime,
        )
        if not pptx_files:
            raise PptMasterError(f"No .pptx produced under {exports}")
        return pptx_files[-1]


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_STREAM_BYTES:
        return text
    return encoded[:_MAX_STREAM_BYTES].decode("utf-8", errors="replace")
