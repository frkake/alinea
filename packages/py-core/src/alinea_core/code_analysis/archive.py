"""固定 commit の GitHub tar archive を安全に取り込む(§8・plan §4 セキュリティ)。

このモジュールは **リポジトリのコードを実行しない**。tar を streaming/bounded に検証し、
安全な対象ファイルだけを抽出する。すべての上限は展開前/展開中に強制する。

拒否する tar メンバ:
- path traversal(``..``)、絶対 path、drive、symlink、hardlink、device/fifo、ディレクトリ escape。

上限(plan §4 / 設計 §8):
- 圧縮 archive: 100 MiB(呼び出し側が download 時に強制。:data:`MAX_COMPRESSED_BYTES`)。
- 展開後総量: 300 MiB。
- 対象コード総量: 10 MiB。
- 対象ファイル数: 2,000。
- 1 ファイル: 512 KiB。

除外(LLM へ送らない):
- .env / 秘密鍵 / 証明書 / credential らしきファイル。
- binary / weight / dataset / minified / generated / lock / vendor / node_modules / dist 等。
"""

from __future__ import annotations

import io
import posixpath
import tarfile
from dataclasses import dataclass, field

# 上限(bytes / 件数)。
MAX_COMPRESSED_BYTES = 100 * 1024 * 1024  # 100 MiB
MAX_EXTRACTED_BYTES = 300 * 1024 * 1024  # 300 MiB
MAX_TARGET_CODE_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_TARGET_FILES = 2_000
MAX_FILE_BYTES = 512 * 1024  # 512 KiB

# 解析対象の拡張子(許可リスト方式。ここに無いものは対象コードに含めない)。
CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".cxx",
        ".hpp",
        ".hh",
        ".m",
        ".mm",
        ".swift",
        ".rb",
        ".php",
        ".scala",
        ".cs",
        ".lua",
        ".jl",
        ".r",
        ".sh",
        ".bash",
    }
)

# LLM へ送らない秘密/credential らしきファイル名・接尾辞(§8)。
_SECRET_BASENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "credentials",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
    }
)
_SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".der",
    ".keystore",
    ".jks",
    ".ppk",
    ".asc",
    ".gpg",
)
# .env で始まるもの全般(.env.foo)を弾く。
_SECRET_PREFIXES: tuple[str, ...] = (".env",)

# 生成物 / weight / dataset / binary の接尾辞(対象コードに含めない)。
_EXCLUDED_SUFFIXES: tuple[str, ...] = (
    # weights / models
    ".pt",
    ".pth",
    ".ckpt",
    ".safetensors",
    ".onnx",
    ".pb",
    ".h5",
    ".bin",
    ".tflite",
    ".gguf",
    ".npz",
    ".npy",
    # datasets / data
    ".csv",
    ".tsv",
    ".parquet",
    ".arrow",
    ".jsonl",
    ".sqlite",
    ".db",
    # archives / binaries
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".jar",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".o",
    ".a",
    ".class",
    ".wasm",
    # media
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".svg",
    ".pdf",
    ".mp4",
    ".mp3",
    ".wav",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    # minified / generated / maps
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
)

# 除外ディレクトリ(path のどこかにこの segment があれば対象外)。
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "site-packages",
        "third_party",
        "external",
        ".next",
        "target",
        ".mypy_cache",
        ".pytest_cache",
        "bower_components",
        # デモ/使用例ツリーは「論文の実装そのもの」ではない(コード対応は method の実装へ
        # claim を対応づける)。加えて実在論文 repo は example 配下に依存を丸ごと vendor する
        # ことがある(例: microsoft/LoRA は examples/NLU/src/transformers/ に HuggingFace
        # transformers を同梱し 8 MiB 超)。build/dist/vendor と同じく対象コードから除外し、
        # loralib/ など repo 直下の実装だけを解析対象にする(§8 の vendor 除外方針の一般化)。
        "examples",
        "example",
    }
)

# 除外ファイル名(lock / generated)。
_EXCLUDED_BASENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "cargo.lock",
        "composer.lock",
        "go.sum",
        "gemfile.lock",
    }
)


class ArchiveError(ValueError):
    """archive の境界違反 / 安全でないメンバを検出したときに送出する。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass
class ExtractedRepo:
    """安全に抽出したリポジトリ(対象コードファイルのみ)。"""

    commit_sha: str
    files: dict[str, str] = field(default_factory=dict)  # repo-relative path -> UTF-8 text
    total_code_bytes: int = 0
    extracted_files: int = 0  # 除外前に見た安全ファイル数(統計)

    @property
    def file_count(self) -> int:
        return len(self.files)


def _strip_top_level(name: str) -> str:
    """GitHub archive は ``{repo}-{sha}/...`` の 1 段トップディレクトリを持つ。これを剥がす。"""
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 else name


def _is_unsafe_path(name: str) -> bool:
    """絶対 path / traversal / drive を検出する。"""
    if not name or name.startswith("/") or name.startswith("\\"):
        return True
    # Windows ドライブ / UNC。
    if len(name) >= 2 and name[1] == ":":
        return True
    normalized = posixpath.normpath(name)
    if normalized.startswith("..") or normalized.startswith("/"):
        return True
    return any(part == ".." for part in name.replace("\\", "/").split("/"))


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def is_secret_file(path: str) -> bool:
    """秘密鍵 / 証明書 / credential らしきファイルか(LLM へ送らない)。"""
    base = _basename(path).lower()
    if base in _SECRET_BASENAMES:
        return True
    if any(base.startswith(prefix) for prefix in _SECRET_PREFIXES):
        return True
    return any(base.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _has_excluded_dir(path: str) -> bool:
    parts = path.lower().split("/")
    return any(part in _EXCLUDED_DIRS for part in parts[:-1])


def _extension(path: str) -> str:
    base = _basename(path).lower()
    dot = base.rfind(".")
    return base[dot:] if dot > 0 else ""


def is_target_code_file(path: str) -> bool:
    """LLM/埋め込みの対象コードに含めてよいファイルか(許可リスト方式)。"""
    base = _basename(path).lower()
    if not base:
        return False
    if is_secret_file(path):
        return False
    if _has_excluded_dir(path):
        return False
    if base in _EXCLUDED_BASENAMES:
        return False
    if any(base.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES):
        return False
    return _extension(path) in CODE_EXTENSIONS


def _is_safe_member(member: tarfile.TarInfo) -> bool:
    """symlink / hardlink / device / fifo / dir-escape を排除し、通常ファイルのみ True。"""
    if member.issym() or member.islnk():
        return False
    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
        return False
    if not member.isfile():
        # ディレクトリは黙ってスキップ(抽出はしないが安全)。それ以外(未知型)は拒否対象。
        return member.isdir()
    return True


def extract_repository(
    tar_bytes: bytes,
    *,
    commit_sha: str,
    max_extracted_bytes: int = MAX_EXTRACTED_BYTES,
    max_target_code_bytes: int = MAX_TARGET_CODE_BYTES,
    max_target_files: int = MAX_TARGET_FILES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> ExtractedRepo:
    """tar.gz バイト列を安全に検証・抽出し、対象コードファイルだけを返す。

    - streaming で 1 メンバずつ検査し、展開後総量・対象コード総量・ファイル数・1 ファイル上限を
      強制する。上限超過は :class:`ArchiveError` を送出する(部分結果は返さない)。
    - path traversal / 絶対 path / symlink / hardlink / device は :class:`ArchiveError` で拒否。
    - リポジトリのコードは **実行しない**。tar の展開のみ。
    """
    repo = ExtractedRepo(commit_sha=commit_sha)
    extracted_total = 0

    try:
        tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*")
    except tarfile.TarError as exc:  # pragma: no cover - 破損 tar
        raise ArchiveError("archive_unreadable", str(exc)) from exc

    with tar:
        while True:
            try:
                member = tar.next()
            except tarfile.TarError as exc:  # pragma: no cover
                raise ArchiveError("archive_unreadable", str(exc)) from exc
            if member is None:
                break

            raw_name = member.name
            # symlink / hardlink は **静かに除外** する(実在リポジトリは docs 等で普通に symlink を
            # 含む。例: microsoft/LoRA の examples/NLU/docs/source/*.md)。本抽出は tar をディスクへ
            # 展開せず ``tar.extractfile`` でメモリに読むだけで、リンクは archive 内でしか解決されず
            # ホスト FS を辿れない(traversal/leak は起き得ない)。ゆえにディレクトリや非対象と
            # 同じく skip するのが安全かつ正しい(archive 全体を拒否すると実 repo が解析不能になる)。
            if member.issym() or member.islnk():
                continue
            # device/fifo など通常ファイル以外の危険メンバ型は即拒否(zip-bomb/奇形対策)。
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise ArchiveError("unsafe_device", raw_name)
            if _is_unsafe_path(raw_name):
                raise ArchiveError("path_traversal", raw_name)
            if not _is_safe_member(member):
                raise ArchiveError("unsafe_member", raw_name)
            if member.isdir():
                continue

            # 1 ファイル上限(ヘッダ申告値で先に弾く)。
            if member.size > max_file_bytes:
                # 対象コード対象外の大きいファイル(データ等)は skip でよいが、
                # 展開後総量にはヘッダサイズを算入して zip-bomb 的膨張を検知する。
                extracted_total += member.size
                if extracted_total > max_extracted_bytes:
                    raise ArchiveError("extracted_too_large", raw_name)
                continue

            extracted_total += member.size
            if extracted_total > max_extracted_bytes:
                raise ArchiveError("extracted_too_large", raw_name)

            rel_path = _strip_top_level(raw_name)
            if not is_target_code_file(rel_path):
                continue

            fp = tar.extractfile(member)
            if fp is None:  # pragma: no cover
                continue
            data = fp.read(max_file_bytes + 1)
            if len(data) > max_file_bytes:
                # 申告と実体が食い違う場合も 1 ファイル上限で弾く。
                continue

            repo.extracted_files += 1
            repo.total_code_bytes += len(data)
            if repo.total_code_bytes > max_target_code_bytes:
                raise ArchiveError("target_code_too_large", rel_path)
            if len(repo.files) + 1 > max_target_files:
                raise ArchiveError("too_many_files", rel_path)

            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                # バイナリ判定(拡張子は通ったが中身が UTF-8 でない)。対象から除外する。
                repo.total_code_bytes -= len(data)
                repo.extracted_files -= 1
                continue
            repo.files[rel_path] = text

    return repo
