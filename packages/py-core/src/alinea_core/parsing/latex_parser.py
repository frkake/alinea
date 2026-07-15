"""arXiv e-print(LaTeX ソース)パーサ(plans/05 §5・M2-01。docs/01 §4・docs/02 §3)。

**決定(本タスクの deviations)**: plans/05 §5 は pandoc(JSON AST)+自前後処理を正としているが、
実行環境に pandoc バイナリが存在せず `uv sync`/新規依存追加が禁止されているため、
stdlib(`tarfile`/`gzip`)+自前の LaTeX トークナイザで docs/01 §4 の IR へ変換する
(担当タスク指示に基づく明示的な逸脱。deviations に記載)。

入力は `sources/{paper_id}/{sv}/latex.tar.gz`(複数ファイル tar.gz)または単一ファイル
gzip(1 ファイル投稿の arXiv 慣習)。メインファイルは `\\documentclass` + `\\begin{document}`
を持つ .tex を `ms.tex` → `main.tex` → 最大サイズの順で選ぶ(plans/05 §5)。

`\\input`/`\\include` を再帰展開し、`\\bibliography{...}` は同梱 `.bbl` があれば埋め込む。
出力は `alinea_core.parsing.html_parser.ParsedDocument`(既存 IR を再利用。重複定義しない)
で `quality_level="A"`, `source_format="latex"`, `parser_version="latex-1.3.7"`。

相互参照(`\\ref`/`\\eqref`)は 2 パスで解決する: 1 パス目で全ブロックを構築しつつ `\\label` を
label→kind map に記録し、2 パス目で保留中の `ref` インラインへ `kind` を確定する(HTML パーサの
DOM id パターン方式に対する LaTeX 版の等価物。ラベル名は自由文字列でパターン推定できないため)。
未解決は `section` へ縮退+warn(plans/05 §4.3 の HTML パーサと同方針)。
"""

from __future__ import annotations

import bz2
import contextlib
import io
import lzma
import posixpath
import re
import tarfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, NoReturn, cast

from alinea_core.document.blocks import Block, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.parsing.block_ids import assign_block_ids
from alinea_core.parsing.html_parser import ParsedDocument

PARSER_VERSION = "latex-1.3.7"

_WS = re.compile(r"\s+")

__all__ = [
    "PARSER_VERSION",
    "LatexArchive",
    "LatexParseError",
    "ParsedDocument",
    "extract_latex_archive",
    "parse_arxiv_latex",
    "parse_latex_source",
    "select_main_tex",
]


class LatexParseError(Exception):
    """LaTeX ソースの取得・展開・解析に失敗(`kind` で分類。§2.4 の FetchError と同方針)。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class LatexArchive:
    """e-print 展開結果。テキスト(.tex/.bbl/.cls/.sty)とバイナリ(図等)を分けて保持する。"""

    __slots__ = ("binary_files", "raw_text_files", "text_files")

    def __init__(
        self,
        text_files: dict[str, str],
        binary_files: dict[str, bytes],
        raw_text_files: dict[str, str] | None = None,
    ) -> None:
        self.text_files = text_files
        self.binary_files = binary_files
        # Parsing uses comment-stripped text, but rebuilding must retain comments:
        # a trailing ``%`` can suppress a layout-significant newline in a macro or
        # style file.  Two-argument construction remains compatible for tests.
        self.raw_text_files = raw_text_files or dict(text_files)


def _collapse(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


# ============================================================================
# アーカイブ展開(tar.gz / 単一ファイル gzip / 無圧縮)
# ============================================================================

_TEXT_EXTS = (".tex", ".bbl", ".bib", ".cls", ".sty")
MAX_LATEX_ARCHIVE_INPUT_BYTES = 128 * 1024 * 1024
MAX_LATEX_ARCHIVE_MEMBERS = 10_000
MAX_LATEX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_LATEX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_LATEX_SINGLE_GZIP_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_LATEX_ARCHIVE_HEADERS = 20_000
MAX_LATEX_ARCHIVE_EXTENSION_HEADERS = 1_024
MAX_LATEX_ARCHIVE_EXTENSION_DEPTH = 32
MAX_LATEX_ARCHIVE_EXTENSION_BYTES = 256 * 1024
MAX_LATEX_PAX_HEADER_BYTES = 256 * 1024
MAX_LATEX_PAX_TOTAL_BYTES = 1024 * 1024
MAX_LATEX_PAX_RECORDS_PER_HEADER = 1_024
MAX_LATEX_PAX_TOTAL_RECORDS = 4_096
MAX_LATEX_XZ_DECODER_MEMORY_BYTES = 128 * 1024 * 1024
MAX_LATEX_COMPRESSED_STREAMS = 1_024
_SINGLE_TEX_ALLOWED_CONTROL_BYTES = frozenset({0x09, 0x0A, 0x0C, 0x0D})
_PAX_HEADER_TYPES = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE)
_GNU_LONG_HEADER_TYPES = (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
_TAR_EXTENSION_TYPES = _PAX_HEADER_TYPES + _GNU_LONG_HEADER_TYPES
_PAX_NAME_FIELDS = frozenset({"path", "linkpath", "uname", "gname"})
_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_COMPRESSED_READ_CHUNK_BYTES = 64 * 1024
_MAX_PAX_LENGTH_DIGITS = 20


def _raise_invalid_archive(message: str) -> NoReturn:
    raise LatexParseError("invalid_archive", message)


def _validate_tar_info_size(info: tarfile.TarInfo) -> None:
    if info.size < 0:
        _raise_invalid_archive("e-print tar header has a negative size")
    if info.size > MAX_LATEX_ARCHIVE_MEMBER_BYTES:
        if info.isfile():
            raise LatexParseError(
                "archive_member_too_large",
                "e-print archive member exceeds the limit",
            )
        _raise_invalid_archive("e-print tar header size exceeds the limit")


class _TarMetadataBudget:
    """実memberへ畳み込まれるraw tar/PAX/GNU headerも事前課金する。"""

    __slots__ = (
        "extension_depth",
        "extension_headers",
        "headers",
        "pax_bytes",
        "pax_records",
    )

    def __init__(self) -> None:
        self.headers = 0
        self.extension_headers = 0
        self.extension_depth = 0
        self.pax_bytes = 0
        self.pax_records = 0

    def reserve_header(self) -> None:
        self.headers += 1
        if self.headers > MAX_LATEX_ARCHIVE_HEADERS:
            _raise_invalid_archive("e-print tar archive has too many raw headers")

    def enter_extension(self, size: int) -> None:
        if size < 0 or size > MAX_LATEX_ARCHIVE_EXTENSION_BYTES:
            _raise_invalid_archive("e-print tar extension header exceeds the limit")
        if self.extension_headers >= MAX_LATEX_ARCHIVE_EXTENSION_HEADERS:
            _raise_invalid_archive("e-print tar archive has too many extension headers")
        if self.extension_depth >= MAX_LATEX_ARCHIVE_EXTENSION_DEPTH:
            _raise_invalid_archive("e-print tar extension nesting exceeds the limit")
        self.extension_headers += 1
        self.extension_depth += 1

    def leave_extension(self) -> None:
        self.extension_depth -= 1

    def reserve_pax_payload(self, size: int) -> None:
        if size < 0 or size > MAX_LATEX_PAX_HEADER_BYTES:
            _raise_invalid_archive("e-print PAX header exceeds the byte limit")
        if self.pax_bytes + size > MAX_LATEX_PAX_TOTAL_BYTES:
            _raise_invalid_archive("e-print PAX metadata exceeds the aggregate byte limit")
        self.pax_bytes += size

    def reserve_pax_record(self, header_records: int) -> None:
        if header_records > MAX_LATEX_PAX_RECORDS_PER_HEADER:
            _raise_invalid_archive("e-print PAX header has too many records")
        if self.pax_records >= MAX_LATEX_PAX_TOTAL_RECORDS:
            _raise_invalid_archive("e-print PAX metadata has too many aggregate records")
        self.pax_records += 1


class _BoundedTarInfo(tarfile.TarInfo):
    """CPython 3.12 ``fromtarfile/_proc_pax`` semantics with pre-parse limits.

    ``tarfile`` recursively consumes PAX/GNU extension headers before yielding a
    member.  Keeping this small compatibility fork lets limits run before that
    recursion and before sparse metadata is expanded into Python objects.
    """

    @classmethod
    def fromtarfile(cls, archive: Any) -> _BoundedTarInfo:
        budget: _TarMetadataBudget = archive._alinea_metadata_budget
        buffer = archive.fileobj.read(tarfile.BLOCKSIZE)
        info = cls.frombuf(buffer, archive.encoding, archive.errors)
        budget.reserve_header()
        info.offset = archive.fileobj.tell() - tarfile.BLOCKSIZE

        _validate_tar_info_size(info)
        if info.type == tarfile.GNUTYPE_SPARSE:
            _raise_invalid_archive("e-print tar archive uses unsupported sparse metadata")

        is_extension = info.type in _TAR_EXTENSION_TYPES
        if is_extension:
            budget.enter_extension(info.size)
        try:
            private_info = cast(Any, info)
            return cast(_BoundedTarInfo, private_info._proc_member(archive))
        finally:
            if is_extension:
                budget.leave_extension()

    def _proc_pax(self, archive: Any) -> _BoundedTarInfo:
        """Bounded equivalent of CPython 3.12 ``TarInfo._proc_pax``."""

        budget: _TarMetadataBudget = archive._alinea_metadata_budget
        budget.reserve_pax_payload(self.size)
        private_self = cast(Any, self)
        padded_size = private_self._block(self.size)
        buffer = archive.fileobj.read(padded_size)
        if len(buffer) != padded_size:
            _raise_invalid_archive("e-print PAX header is truncated")

        pax_headers = (
            archive.pax_headers if self.type == tarfile.XGLTYPE else archive.pax_headers.copy()
        )
        raw_headers, encoding = self._bounded_pax_records(
            buffer[: self.size],
            budget,
            archive.encoding,
        )

        for _length, raw_keyword, raw_value in raw_headers:
            keyword = private_self._decode_pax_field(
                raw_keyword,
                "utf-8",
                "utf-8",
                archive.errors,
            )
            if keyword in _PAX_NAME_FIELDS:
                value = private_self._decode_pax_field(
                    raw_value,
                    encoding,
                    archive.encoding,
                    archive.errors,
                )
            else:
                value = private_self._decode_pax_field(
                    raw_value,
                    "utf-8",
                    "utf-8",
                    archive.errors,
                )
            pax_headers[keyword] = value

        try:
            next_info = self.fromtarfile(archive)
        except tarfile.HeaderError as exc:
            raise tarfile.ReadError(str(exc)) from None
        _validate_tar_info_size(next_info)

        if self.type in (tarfile.XHDTYPE, tarfile.SOLARIS_XHDTYPE):
            private_next = cast(Any, next_info)
            private_next._apply_pax_info(
                pax_headers,
                archive.encoding,
                archive.errors,
            )
            _validate_tar_info_size(next_info)
            next_info.offset = self.offset
            if "size" in pax_headers:
                offset = next_info.offset_data
                if next_info.isreg() or next_info.type not in tarfile.SUPPORTED_TYPES:
                    offset += private_next._block(next_info.size)
                archive.offset = offset

        return next_info

    @staticmethod
    def _bounded_pax_records(
        payload: bytes,
        budget: _TarMetadataBudget,
        fallback_encoding: str,
    ) -> tuple[list[tuple[int, bytes, bytes]], str]:
        records: list[tuple[int, bytes, bytes]] = []
        position = 0
        encoding: str | None = None

        while position < len(payload) and payload[position] != 0:
            digit_end = payload.find(
                b" ",
                position,
                min(len(payload), position + _MAX_PAX_LENGTH_DIGITS + 1),
            )
            if digit_end < 0:
                _raise_invalid_archive("e-print PAX record has invalid framing")
            length_digits = payload[position:digit_end]
            if not length_digits or not all(48 <= byte <= 57 for byte in length_digits):
                _raise_invalid_archive("e-print PAX record has invalid length")
            length = int(length_digits)
            record_end = position + length
            if length < 5 or record_end > len(payload):
                _raise_invalid_archive("e-print PAX record exceeds its header")
            value_end = record_end - 1
            keyword_and_value = payload[digit_end + 1 : value_end]
            raw_keyword, equals, raw_value = keyword_and_value.partition(b"=")
            if not raw_keyword or equals != b"=" or payload[value_end] != 0x0A:
                _raise_invalid_archive("e-print PAX record has invalid framing")
            if raw_keyword.startswith(b"GNU.sparse."):
                _raise_invalid_archive("e-print tar archive uses unsupported sparse metadata")

            budget.reserve_pax_record(len(records) + 1)
            records.append((length, raw_keyword, raw_value))
            if raw_keyword == b"hdrcharset" and encoding is None:
                encoding = "utf-8" if raw_value != b"BINARY" else fallback_encoding
            position = record_end

        return records, encoding or "utf-8"


class _BoundedTarFile(tarfile.TarFile):
    """Install the metadata budget before ``TarFile.__init__`` reads header 1."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._alinea_metadata_budget = _TarMetadataBudget()
        kwargs["tarinfo"] = _BoundedTarInfo
        super().__init__(*args, **kwargs)


def _reserve_compressed_stream(streams: int, compression: str) -> int:
    if streams >= MAX_LATEX_COMPRESSED_STREAMS:
        _raise_invalid_archive(f"e-print {compression} archive has too many streams")
    return streams + 1


def _complete_compressed_magic(
    source: Any,
    compressed: bytes,
    magic: bytes,
    compression: str,
) -> bytes:
    while len(compressed) < len(magic) and magic.startswith(compressed):
        more = source.read(_COMPRESSED_READ_CHUNK_BYTES)
        if not isinstance(more, bytes):
            raise OSError(f"{compression} source returned non-bytes data")
        if not more:
            raise EOFError(f"compressed {compression} archive has a truncated stream header")
        compressed += more
    return compressed


def _scan_xz_stream_padding(source: Any, compressed: bytes) -> bytes | None:
    """Consume spec-valid four-byte XZ stream padding without buffering it."""

    padding_bytes = 0
    while True:
        if not isinstance(compressed, bytes):
            raise OSError("xz source returned non-bytes data")
        candidate = compressed.lstrip(b"\0")
        padding_bytes += len(compressed) - len(candidate)
        if candidate:
            break

        compressed = source.read(_COMPRESSED_READ_CHUNK_BYTES)
        if not compressed:
            if padding_bytes % 4:
                _raise_invalid_archive("e-print xz archive has invalid stream padding")
            return None

    if padding_bytes % 4:
        _raise_invalid_archive("e-print xz archive has invalid stream padding")
    return candidate


class _BoundedGzipReader(io.RawIOBase):
    """Forward gzip reader with CRC validation and a concatenated-member cap."""

    __slots__ = ("_decompressor", "_eof", "_pending", "_source", "_streams")

    def __init__(self, source: Any) -> None:
        super().__init__()
        self._source = source
        self._eof = False
        self._pending = b""
        self._streams = 1
        self._decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size == 0 or self._eof:
            return b""
        if size < 0:
            chunks: list[bytes] = []
            while chunk := self.read(_COMPRESSED_READ_CHUNK_BYTES):
                chunks.append(chunk)
            return b"".join(chunks)

        while True:
            if self._decompressor.eof:
                compressed = self._decompressor.unused_data or self._pending
                self._pending = b""
                while not compressed or not compressed.lstrip(b"\0"):
                    compressed = self._source.read(_COMPRESSED_READ_CHUNK_BYTES)
                    if not isinstance(compressed, bytes):
                        raise OSError("gzip source returned non-bytes data")
                    if not compressed:
                        self._eof = True
                        return b""
                compressed = compressed.lstrip(b"\0")
                self._streams = _reserve_compressed_stream(self._streams, "gzip")
                self._decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            else:
                compressed = self._pending or self._source.read(_COMPRESSED_READ_CHUNK_BYTES)
                self._pending = b""
                if not isinstance(compressed, bytes):
                    raise OSError("gzip source returned non-bytes data")
                if not compressed:
                    raise EOFError("compressed gzip archive ended before the stream marker")

            data = self._decompressor.decompress(compressed, size)
            self._pending = self._decompressor.unconsumed_tail
            if data:
                return data


class _BoundedBzip2Reader(io.RawIOBase):
    """Forward bzip2 reader with a deterministic concatenated-stream cap."""

    __slots__ = ("_decompressor", "_eof", "_source", "_streams")

    def __init__(self, source: Any) -> None:
        super().__init__()
        self._source = source
        self._eof = False
        self._streams = 1
        self._decompressor = bz2.BZ2Decompressor()

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size == 0 or self._eof:
            return b""
        if size < 0:
            chunks: list[bytes] = []
            while chunk := self.read(_COMPRESSED_READ_CHUNK_BYTES):
                chunks.append(chunk)
            return b"".join(chunks)

        while True:
            if self._decompressor.eof:
                compressed = self._decompressor.unused_data or self._source.read(
                    _COMPRESSED_READ_CHUNK_BYTES
                )
                if not isinstance(compressed, bytes):
                    raise OSError("bzip2 source returned non-bytes data")
                if not compressed:
                    self._eof = True
                    return b""
                compressed = _complete_compressed_magic(
                    self._source,
                    compressed,
                    _BZIP2_MAGIC,
                    "bzip2",
                )
                if not compressed.startswith(_BZIP2_MAGIC):
                    self._eof = True
                    return b""
                self._streams = _reserve_compressed_stream(self._streams, "bzip2")
                self._decompressor = bz2.BZ2Decompressor()
                data = self._decompressor.decompress(compressed, max_length=size)
            else:
                compressed = (
                    self._source.read(_COMPRESSED_READ_CHUNK_BYTES)
                    if self._decompressor.needs_input
                    else b""
                )
                if not isinstance(compressed, bytes):
                    raise OSError("bzip2 source returned non-bytes data")
                if not compressed and self._decompressor.needs_input:
                    raise EOFError("compressed bzip2 archive ended before the stream marker")
                data = self._decompressor.decompress(compressed, max_length=size)
            if data:
                return data


class _MemoryLimitedLzmaReader(io.RawIOBase):
    """Forward xz reader with a fresh decoder memory cap for every stream."""

    __slots__ = ("_decompressor", "_eof", "_source", "_streams")

    def __init__(self, source: Any) -> None:
        super().__init__()
        self._source = source
        self._eof = False
        self._streams = 1
        self._decompressor: lzma.LZMADecompressor = self._new_decompressor()

    @staticmethod
    def _new_decompressor() -> lzma.LZMADecompressor:
        return lzma.LZMADecompressor(
            format=lzma.FORMAT_AUTO,
            memlimit=MAX_LATEX_XZ_DECODER_MEMORY_BYTES,
        )

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size == 0 or self._eof:
            return b""
        if size < 0:
            chunks: list[bytes] = []
            while chunk := self.read(_COMPRESSED_READ_CHUNK_BYTES):
                chunks.append(chunk)
            return b"".join(chunks)

        while True:
            if self._decompressor.eof:
                compressed = self._decompressor.unused_data or self._source.read(
                    _COMPRESSED_READ_CHUNK_BYTES
                )
                if not compressed:
                    self._eof = True
                    return b""
                if not isinstance(compressed, bytes):
                    raise OSError("xz source returned non-bytes data")
                compressed = _scan_xz_stream_padding(self._source, compressed)
                if compressed is None:
                    self._eof = True
                    return b""
                compressed = _complete_compressed_magic(
                    self._source,
                    compressed,
                    _XZ_MAGIC,
                    "xz",
                )
                if not compressed.startswith(_XZ_MAGIC):
                    self._eof = True
                    return b""
                self._streams = _reserve_compressed_stream(self._streams, "xz")
                self._decompressor = self._new_decompressor()
                data = self._decompressor.decompress(compressed, size)
            else:
                compressed = (
                    self._source.read(_COMPRESSED_READ_CHUNK_BYTES)
                    if self._decompressor.needs_input
                    else b""
                )
                if not compressed and self._decompressor.needs_input:
                    raise EOFError("compressed xz archive ended before the stream marker")
                data = self._decompressor.decompress(compressed, size)
            if data:
                return data


class _ArchiveExpansionReader(io.RawIOBase):
    """tar parserへ渡す展開済みstreamを、EOF確認込みで累積制限する。"""

    __slots__ = ("_consumed", "_limit", "_source")

    def __init__(self, source: Any, limit: int) -> None:
        super().__init__()
        self._source = source
        self._limit = limit
        self._consumed = 0

    def readable(self) -> bool:
        return True

    @staticmethod
    def _raise_limit() -> NoReturn:
        raise LatexParseError(
            "archive_expanded_too_large",
            "e-print archive exceeds the aggregate expansion limit",
        )

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        remaining = self._limit - self._consumed
        request_size = remaining + 1 if size < 0 else min(size, remaining + 1)
        data = self._source.read(request_size)
        if not isinstance(data, bytes):
            raise OSError("decompressed archive stream returned non-bytes data")
        if len(data) > remaining:
            self._raise_limit()
        self._consumed += len(data)
        return data

    def drain(self) -> None:
        while self.read(64 * 1024):
            pass


def _has_valid_raw_tar_header(archive: bytes) -> bool:
    if len(archive) < tarfile.BLOCKSIZE:
        return False
    header = archive[: tarfile.BLOCKSIZE]
    checksum_field = header[148:156].strip(b" \0")
    if not checksum_field or not all(48 <= byte <= 55 for byte in checksum_field):
        return False
    stored_checksum = int(checksum_field, 8)
    unsigned_checksum = sum(header[:148]) + (8 * 32) + sum(header[156:])
    signed_checksum = (
        sum(byte if byte < 128 else byte - 256 for byte in header[:148])
        + (8 * 32)
        + sum(byte if byte < 128 else byte - 256 for byte in header[156:])
    )
    return stored_checksum in {unsigned_checksum, signed_checksum}


def _archive_compression(archive: bytes) -> str | None:
    if _has_valid_raw_tar_header(archive):
        return None
    if archive.startswith(_GZIP_MAGIC):
        return "gzip"
    if archive.startswith(_BZIP2_MAGIC):
        return "bzip2"
    if archive.startswith(_XZ_MAGIC):
        return "xz"
    return None


def _is_plausible_single_tex(data: bytes) -> bool:
    """単一TeX fallbackを、バイナリ制御文字を含まないtextに限定する。"""

    return not any(
        (byte < 0x20 and byte not in _SINGLE_TEX_ALLOWED_CONTROL_BYTES) or byte == 0x7F
        for byte in data
    )


@contextlib.contextmanager
def _open_decompressed_tar_stream(
    archive: bytes,
    compression: str | None,
) -> Iterator[Any]:
    """tar圧縮を明示的に開き、tarfileには非圧縮streamだけを渡す。"""

    with io.BytesIO(archive) as source:
        if compression == "gzip":
            with _BoundedGzipReader(source) as decompressed:
                yield decompressed
            return
        if compression == "bzip2":
            with _BoundedBzip2Reader(source) as decompressed:
                yield decompressed
            return
        if compression == "xz":
            with _MemoryLimitedLzmaReader(source) as decompressed:
                yield decompressed
            return
        yield source


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _is_plausible_single_tex_source(data: bytes) -> bool:
    return _is_plausible_single_tex(data) and "\\documentclass" in _decode(data)


def _read_decompressed_prefix(reader: io.RawIOBase, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def extract_latex_archive(archive: bytes) -> LatexArchive:
    """e-print バイト列を展開する(tar.gz 優先 → 単一ファイル gzip → 無圧縮 .tex)。"""
    if not archive:
        raise LatexParseError("empty_archive", "e-print archive is empty")
    if len(archive) > MAX_LATEX_ARCHIVE_INPUT_BYTES:
        raise LatexParseError("archive_too_large", "e-print archive exceeds the input limit")

    compression = _archive_compression(archive)
    bzip2_raw_fallback = compression == "bzip2" and _is_plausible_single_tex_source(archive)
    tar_recognized = False
    limited: _ArchiveExpansionReader | None = None
    extracted_files: tuple[dict[str, str], dict[str, bytes], dict[str, str]] | None = None
    try:
        with _open_decompressed_tar_stream(archive, compression) as decompressed:
            limited = _ArchiveExpansionReader(
                decompressed,
                MAX_LATEX_ARCHIVE_EXPANDED_BYTES,
            )
            with _BoundedTarFile.open(fileobj=limited, mode="r|") as tar:
                tar_recognized = True
                text_files: dict[str, str] = {}
                raw_text_files: dict[str, str] = {}
                binary_files: dict[str, bytes] = {}
                member_count = 0
                expanded_bytes = 0
                for member in tar:
                    member_count += 1
                    if member_count > MAX_LATEX_ARCHIVE_MEMBERS:
                        raise LatexParseError(
                            "archive_member_limit", "e-print archive has too many members"
                        )
                    _validate_tar_info_size(member)
                    if not member.isfile():
                        continue
                    remaining = MAX_LATEX_ARCHIVE_EXPANDED_BYTES - expanded_bytes
                    if member.size > remaining:
                        raise LatexParseError(
                            "archive_expanded_too_large",
                            "e-print archive exceeds the aggregate expansion limit",
                        )
                    name = member.name.removeprefix("./")
                    fh = tar.extractfile(member)
                    if fh is None:
                        continue
                    with fh:
                        data = fh.read(min(MAX_LATEX_ARCHIVE_MEMBER_BYTES, remaining) + 1)
                    if len(data) > MAX_LATEX_ARCHIVE_MEMBER_BYTES:
                        raise LatexParseError(
                            "archive_member_too_large",
                            "e-print archive member exceeds the limit",
                        )
                    expanded_bytes += len(data)
                    if expanded_bytes > MAX_LATEX_ARCHIVE_EXPANDED_BYTES:
                        raise LatexParseError(
                            "archive_expanded_too_large",
                            "e-print archive exceeds the aggregate expansion limit",
                        )
                    if name.lower().endswith(_TEXT_EXTS):
                        decoded = _decode(data)
                        raw_text_files[name] = decoded
                        text_files[name] = _strip_comments(decoded)
                    else:
                        binary_files[name] = data
                limited.drain()
                if text_files:
                    extracted_files = (text_files, binary_files, raw_text_files)
                else:
                    raise LatexParseError("no_main_tex", "no .tex content found in e-print archive")
    except LatexParseError:
        raise
    except tarfile.ReadError as exc:
        if tar_recognized or (compression in {"bzip2", "xz"} and not bzip2_raw_fallback):
            raise LatexParseError("invalid_archive", "e-print tar archive is invalid") from exc
    except (tarfile.TarError, ValueError, OverflowError, RecursionError) as exc:
        raise LatexParseError("invalid_archive", "e-print tar archive is invalid") from exc
    except (EOFError, OSError, lzma.LZMAError, zlib.error) as exc:
        if tar_recognized or (compression is not None and not bzip2_raw_fallback):
            raise LatexParseError("invalid_archive", "e-print tar archive is invalid") from exc

    if extracted_files is not None:
        return LatexArchive(*extracted_files)

    raw = archive
    if archive.startswith(_GZIP_MAGIC):
        try:
            with io.BytesIO(archive) as source:
                with _BoundedGzipReader(source) as compressed:
                    raw = _read_decompressed_prefix(
                        compressed,
                        MAX_LATEX_SINGLE_GZIP_EXPANDED_BYTES,
                    )
        except (EOFError, OSError, zlib.error) as exc:
            raise LatexParseError("invalid_archive", "e-print gzip archive is invalid") from exc
        if len(raw) > MAX_LATEX_SINGLE_GZIP_EXPANDED_BYTES:
            raise LatexParseError(
                "archive_expanded_too_large", "single-file e-print exceeds expansion limit"
            )
    elif len(raw) > MAX_LATEX_SINGLE_GZIP_EXPANDED_BYTES:
        raise LatexParseError(
            "archive_expanded_too_large", "single-file e-print exceeds expansion limit"
        )
    if not _is_plausible_single_tex(raw):
        raise LatexParseError(
            "invalid_archive", "single-file e-print contains binary control bytes"
        )
    raw_text = _decode(raw)
    text = _strip_comments(raw_text)
    if "\\documentclass" not in text:
        raise LatexParseError("no_main_tex", "no .tex content found in e-print archive")
    return LatexArchive({"main.tex": text}, {}, {"main.tex": raw_text})


def select_main_tex(text_files: dict[str, str]) -> tuple[str, str]:
    """メイン .tex の特定(plans/05 §5): `ms.tex` → `main.tex` → 最大サイズ。"""
    candidates = [
        name
        for name, content in text_files.items()
        if name.lower().endswith(".tex")
        and "\\documentclass" in content
        and "\\begin{document}" in content
    ]
    if not candidates:
        raise LatexParseError(
            "no_main_tex", "no file with \\documentclass + \\begin{document} found"
        )
    for preferred in ("ms.tex", "main.tex"):
        if preferred in candidates:
            return preferred, text_files[preferred]
    best = max(candidates, key=lambda n: len(text_files[n]))
    return best, text_files[best]


# ============================================================================
# コメント除去(verbatim/lstlisting 内は保護)
# ============================================================================

_UNESCAPED_PERCENT_RE = re.compile(r"(?<!\\)%")
_VERB_BEGIN_RE = re.compile(r"\\begin\{(verbatim\*?|lstlisting|minted)\}")
_INLINE_VERB_START_RE = re.compile(r"\\verb\*?(?![A-Za-z])")
_LITERAL_START_RE = re.compile(
    r"\\(?:"
    r"begin\{(?P<environment>verbatim\*?|lstlisting|minted)\}|"
    r"(?P<inline>verb\*?)(?![A-Za-z])"
    r")"
)
_ENVIRONMENT_BOUNDARY_RE = re.compile(r"\\(?P<kind>begin|end)\{(?P<name>[a-zA-Z]+\*?)\}")
_VERB_END_TMPL = r"\\end\{{{}}}"


def _strip_line_comment(text: str) -> str:
    literal = _next_literal_region(text, 0)
    for match in _UNESCAPED_PERCENT_RE.finditer(text):
        while literal is not None and match.start() >= literal[1]:
            literal = _next_literal_region(text, literal[1])
        if literal is not None and literal[0] <= match.start() < literal[1]:
            continue
        return text[: match.start()]
    return text


def _strip_comments(text: str) -> str:
    out = io.StringIO()
    in_verbatim = False
    verb_name = ""
    for line_with_ending in io.StringIO(text):
        has_newline = line_with_ending.endswith("\n")
        line = line_with_ending[:-1] if has_newline else line_with_ending
        if in_verbatim:
            out.write(line)
            if re.search(_VERB_END_TMPL.format(re.escape(verb_name)), line):
                in_verbatim = False
        else:
            out.write(_strip_line_comment(line))
            m = _VERB_BEGIN_RE.search(line)
            if m:
                in_verbatim = True
                verb_name = m.group(1)
        if has_newline:
            out.write("\n")
    return out.getvalue()


# ============================================================================
# \input/\include 展開・\bibliography{} の .bbl 埋め込み
# ============================================================================

_BIBLIOGRAPHY_CMD_RE = re.compile(r"\\bibliography\{([^}]*)\}")
_PRINT_BIBLIOGRAPHY_RE = re.compile(r"\\printbibliography\b(?:\s*\[[^\]]*\])?")
_BIB_RESOURCE_RE = re.compile(r"\\(?:addbibresource|bibliography)\{([^}]*)\}")
_CITE_KEY_RE = re.compile(
    r"\\(?:cite|citet|citep|citeauthor|citeyear|citealt|citealp)\*?"
    r"(?:\s*\[[^\]]*\])*\s*\{([^}]*)\}"
)
_NOCITE_ALL_RE = re.compile(r"\\nocite\s*\{\s*\*\s*\}")
_BIB_ENTRY_START_RE = re.compile(
    r"@(?P<type>[A-Za-z]+)\s*(?P<open>[{(])\s*(?P<key>[^,\s]+)\s*,", re.IGNORECASE
)


def _resolve_bibliography(
    text: str,
    files: dict[str, str],
    *,
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    """`\\bibliography{}` を同梱 `.bbl` / `.bib` の内容で置換する。

    arXiv e-print は `.bbl` を含むことが多いが、`.bib` だけの投稿もある。`.bbl` が無い場合は
    cited key と `.bib` entry から最小限の `thebibliography` を合成し、既存の bibitem パーサへ流す。
    """
    if "\\begin{thebibliography}" in text:
        return text
    m = _BIBLIOGRAPHY_CMD_RE.search(text)
    m_print = _PRINT_BIBLIOGRAPHY_RE.search(text)
    if not m and not m_print:
        return text
    bbl_name = next((n for n in files if n.lower().endswith(".bbl")), None)
    replacement: str | None
    if bbl_name is not None:
        replacement = files[bbl_name]
        if budget is not None:
            budget.reserve_operation()
            budget.reserve_source_visit()
            budget.reserve_evaluated_text(replacement)
    else:
        replacement = _build_thebibliography_from_bib(text, files, budget=budget)
    if not replacement:
        return text
    target = m or m_print
    if target is None:
        return text
    if budget is not None:
        budget.reserve_operation()
        budget.reserve_emitted_text(replacement)
        budget.ensure_emittable_parts(
            iter(
                (
                    (text, 0, target.start()),
                    (replacement, 0, len(replacement)),
                    (text, target.end(), len(text)),
                )
            )
        )
    return text[: target.start()] + replacement + text[target.end() :]


def _iter_comma_separated(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> Iterator[str]:
    start = 0
    for index, char in enumerate(text):
        if char != ",":
            continue
        if budget is not None:
            budget.reserve_structure_match()
        yield text[start:index]
        start = index + 1
    yield text[start:]


def _bibliography_names(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> list[str]:
    names: list[str] = []
    for m in _BIB_RESOURCE_RE.finditer(text):
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_control_token()
        for name in _iter_comma_separated(m.group(1), budget):
            clean = name.strip()
            if not clean:
                continue
            if budget is not None:
                budget.reserve_ir_object()
            names.append(clean)
    return names


def _matching_bib_files(
    names: list[str],
    files: dict[str, str],
    budget: _LatexEvaluationBudget | None = None,
) -> list[str]:
    bib_files: list[str] = []
    for filename in files:
        if not filename.lower().endswith(".bib"):
            continue
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_ir_object()
        bib_files.append(filename)
    if not names:
        return bib_files
    wanted: set[str] = set()
    for name in names:
        normalized = name.strip().removeprefix("./")
        variants = [normalized]
        if not normalized.lower().endswith(".bib"):
            variants.append(f"{normalized}.bib")
        for variant in variants:
            if variant in wanted:
                continue
            if budget is not None:
                budget.reserve_ir_object()
            wanted.add(variant)

    suffix_trie: dict[Any, Any] = {}
    for wanted_name in wanted:
        node = suffix_trie
        for char in reversed(wanted_name):
            if budget is not None:
                budget.reserve_operation()
            child = node.get(char)
            if child is None:
                if budget is not None:
                    budget.reserve_ir_object()
                child = {}
                node[char] = child
            node = child
        if budget is not None:
            budget.reserve_ir_object()
        node[None] = True

    out: list[str] = []
    for filename in bib_files:
        node = suffix_trie
        for index in range(len(filename) - 1, -1, -1):
            if budget is not None:
                budget.reserve_operation()
            child = node.get(filename[index])
            if child is None:
                break
            node = child
            if None in node and (index == 0 or filename[index - 1] == "/"):
                if budget is not None:
                    budget.reserve_ir_object()
                out.append(filename)
                break
    return out or bib_files


def _cited_keys(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for m in _CITE_KEY_RE.finditer(text):
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_control_token()
        for key in _iter_comma_separated(m.group(1), budget):
            clean = key.strip()
            if clean and clean not in seen:
                if budget is not None:
                    budget.reserve_ir_object()
                keys.append(clean)
                seen.add(clean)
    return keys


def _read_bib_entry_body(
    text: str,
    start: int,
    opener: str,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, int] | None:
    closer = "}" if opener == "{" else ")"
    depth = 1
    quote = False
    escaped = False
    i = start
    while i < len(text):
        c = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if c == "\\":
            escaped = True
            i += 1
            continue
        if c == '"':
            quote = not quote
            i += 1
            continue
        if not quote:
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    if budget is not None:
                        budget.reserve_operation()
                        budget.reserve_evaluated_text(text, start, i)
                    return text[start:i], i + 1
        i += 1
    return None


def _iter_bib_top_level(
    text: str,
    sep: str,
    budget: _LatexEvaluationBudget | None = None,
) -> Iterator[str]:
    start = 0
    depth = 0
    quote = False
    escaped = False
    for i, c in enumerate(text):
        if escaped:
            escaped = False
            continue
        if c == "\\":
            escaped = True
            continue
        if c == '"':
            quote = not quote
            continue
        if quote:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == sep and depth == 0:
            if budget is not None:
                budget.reserve_structure_match()
                budget.reserve_ir_object()
            yield text[start:i]
            start = i + 1
    if budget is not None:
        budget.reserve_ir_object()
    yield text[start:]


def _balanced_outer(value: str, opener: str, closer: str) -> bool:
    if not (value.startswith(opener) and value.endswith(closer)):
        return False
    depth = 0
    quote = False
    escaped = False
    for i, c in enumerate(value):
        if escaped:
            escaped = False
            continue
        if c == "\\":
            escaped = True
            continue
        if c == '"' and opener != '"':
            quote = not quote
            continue
        if quote:
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0 and i != len(value) - 1:
                return False
    return depth == 0


def _outer_brace_layers(value: str) -> int:
    """外側を完全に包むbrace層数を1回の走査で返す。"""

    if not _balanced_outer(value, "{", "}"):
        return 0
    prefix = 0
    while prefix < len(value) and value[prefix] == "{":
        prefix += 1
    suffix = 0
    while suffix < len(value) and value[len(value) - suffix - 1] == "}":
        suffix += 1
    depth = prefix
    minimum_depth = prefix
    escaped = False
    quote = False
    for index in range(prefix, len(value) - suffix):
        char = value[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            quote = not quote
            continue
        if quote:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        minimum_depth = min(minimum_depth, depth)
    return min(prefix, suffix, minimum_depth)


def _clean_bib_value(
    raw: str,
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    parts = list(_iter_bib_top_level(raw.strip(), "#", budget))
    joined = " ".join(p.strip() for p in parts if p.strip())
    changed = True
    unwrap_passes = 0
    while changed:
        unwrap_passes += 1
        if budget is not None:
            budget.ensure_parser_depth(unwrap_passes)
        elif unwrap_passes > _MAX_LATEX_PARSER_DEPTH:
            _LatexEvaluationBudget._raise_limit()
        changed = False
        s = joined.strip()
        brace_layers = _outer_brace_layers(s)
        if brace_layers:
            joined = s[brace_layers:-brace_layers]
            changed = True
            continue
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            joined = s[1:-1]
            changed = True
    joined = re.sub(r"\\[\"'`^~=.uvHtcbd]\s*\{?([A-Za-z])\}?", r"\1", joined)
    joined = joined.replace("\\&", "&").replace("\\_", "_")
    joined = joined.replace("{", "").replace("}", "")
    return _strip_markup(joined, budget)


def _parse_bib_fields(
    body: str,
    budget: _LatexEvaluationBudget | None = None,
) -> dict[str, str]:
    fields: dict[str, str] = {}
    for chunk in _iter_bib_top_level(body, ",", budget):
        if "=" not in chunk:
            continue
        name, raw_value = chunk.split("=", 1)
        clean_name = name.strip().lower()
        if not clean_name:
            continue
        value = _clean_bib_value(raw_value, budget)
        if value:
            if budget is not None:
                budget.reserve_ir_object()
            fields[clean_name] = value
    return fields


def _parse_bib_entries(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    pos = 0
    while True:
        m = _BIB_ENTRY_START_RE.search(text, pos)
        if m is None:
            break
        if budget is not None:
            budget.reserve_structure_match()
        read = _read_bib_entry_body(text, m.end(), m.group("open"), budget)
        if read is None:
            pos = m.end()
            continue
        body, pos = read
        typ = m.group("type").lower()
        if typ in {"comment", "preamble", "string"}:
            continue
        key = m.group("key").strip()
        if key:
            if budget is not None:
                budget.reserve_ir_object()
            entries[key] = _parse_bib_fields(body, budget)
            if budget is not None:
                budget.reserve_ir_object()
            entries[key]["entry_type"] = typ
    return entries


def _sentence(text: str) -> str:
    s = text.strip()
    return s if not s or s.endswith((".", "?", "!")) else f"{s}."


def _bib_entry_to_bibitem(
    key: str,
    fields: dict[str, str],
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    authors_raw = fields.get("author") or fields.get("editor") or ""
    authors: list[str] = []
    author_start = 0
    for separator in re.finditer(r"\s+and\s+", authors_raw):
        if budget is not None:
            budget.reserve_structure_match()
        author = _collapse(authors_raw[author_start : separator.start()])
        if author:
            if budget is not None:
                budget.reserve_ir_object()
            authors.append(author)
        author_start = separator.end()
    author = _collapse(authors_raw[author_start:])
    if author:
        if budget is not None:
            budget.reserve_ir_object()
        authors.append(author)
    authors_text = (
        _join_emittable(authors, ", ", budget) if budget is not None else ", ".join(authors)
    )
    title = fields.get("title") or ""
    venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
    year = fields.get("year") or fields.get("date", "")[:4]
    doi = fields.get("doi") or ""
    url = fields.get("url") or ""
    arxiv_id = fields.get("eprint") or ""
    archive = fields.get("archiveprefix") or fields.get("eprinttype") or ""

    parts: list[str] = []
    if authors_text:
        parts.append(_sentence(authors_text))
    if title:
        parts.append(_sentence(rf"\emph{{{title}}}"))
    if venue:
        parts.append(_sentence(venue))
    if arxiv_id and (archive.lower() == "arxiv" or re.match(r"\d{4}\.\d{4,5}", arxiv_id)):
        parts.append(_sentence(f"arXiv:{arxiv_id}"))
    if doi:
        parts.append(_sentence(rf"\url{{https://doi.org/{doi.removeprefix('https://doi.org/')}}}"))
    elif url:
        parts.append(_sentence(rf"\url{{{url}}}"))
    if year and not any(re.search(rf"\b{re.escape(year)}\b", part) for part in parts):
        parts.append(_sentence(year))
    raw = " ".join(parts).strip() or key
    return rf"\bibitem{{{key}}} {raw}"


def _build_thebibliography_from_bib(
    text: str,
    files: dict[str, str],
    *,
    budget: _LatexEvaluationBudget | None = None,
) -> str | None:
    entries: dict[str, dict[str, str]] = {}
    for name in _matching_bib_files(
        _bibliography_names(text, budget),
        files,
        budget,
    ):
        if budget is not None:
            budget.reserve_operation()
            budget.reserve_source_visit()
            budget.reserve_evaluated_text(files[name])
        entries.update(_parse_bib_entries(files[name], budget))
    if not entries:
        return None

    if _NOCITE_ALL_RE.search(text):
        ordered_keys = list(entries)
    else:
        cited = _cited_keys(text, budget)
        ordered_keys = [key for key in cited if key in entries]
        if not ordered_keys and not cited:
            ordered_keys = list(entries)
    if not ordered_keys:
        return None

    items: list[str] = []
    for key in ordered_keys:
        if budget is not None:
            budget.reserve_structure_match()
        items.append(_bib_entry_to_bibitem(key, entries[key], budget))
    parts = [
        "\\begin{thebibliography}{" + str(len(items)) + "}",
        *items,
        "\\end{thebibliography}",
    ]
    if budget is not None:
        return _join_emittable(parts, "\n", budget)
    return "\n".join(parts)


def _extract_document_body(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    m = next(
        (
            candidate
            for candidate in re.finditer(r"\\begin\{document\}", text)
            if not re.search(r"\\string\s*$", text[max(0, candidate.start() - 32) : candidate.start()])
        ),
        None,
    )
    if not m:
        raise LatexParseError("no_main_tex", "no \\begin{document} found")
    inner, _end = _read_environment(text, m.end(), "document", budget)
    return inner


_FRONTMATTER_CMDS = (
    "maketitle",
    "title",
    "author",
    "date",
    "thanks",
    "affil",
    "affiliation",
    "thispagestyle",
    "pagestyle",
    "tableofcontents",
    "and",
    "institute",
    "email",
    "IEEEauthorblockN",
    "IEEEauthorblockA",
)

_SETUP_CMDS = frozenset(
    {
        "addtolength",
        "bibliographystyle",
        "colorlet",
        "DeclareMathOperator",
        "DeclarePairedDelimiter",
        "DeclareRobustCommand",
        "DeclareTextFontCommand",
        "def",
        "definecolor",
        "graphicspath",
        "hypersetup",
        "let",
        "newcommand",
        "newenvironment",
        "newif",
        "newlength",
        "newtheorem",
        "providecommand",
        "renewcommand",
        "renewenvironment",
        "setcounter",
        "setlength",
        "tikzset",
    }
)
_SETUP_CMD_RE = re.compile(
    r"\\("
    + "|".join(sorted((re.escape(cmd) for cmd in _SETUP_CMDS), key=len, reverse=True))
    + r")\*?(?![A-Za-z])"
)
_CONTROL_WORD_RE = re.compile(r"\\[A-Za-z@]+")
_TEX_CONTROL_WORD_TOKEN_RE = re.compile(r"\\[A-Za-z@]+")
_FRONTMATTER_CMD_RE = re.compile(
    r"\\(" + "|".join(re.escape(cmd) for cmd in _FRONTMATTER_CMDS) + r")\*?(?![A-Za-z])"
)


def _read_tex_control_token(text: str, pos: int) -> tuple[str, int, bool] | None:
    """``pos`` の TeX control word / control symbol を 1 token 読む。"""

    if pos >= len(text) or text[pos] != "\\":
        return None
    word = _TEX_CONTROL_WORD_TOKEN_RE.match(text, pos)
    if word is not None:
        return word.group(0), word.end(), True
    end = min(len(text), pos + 2)
    return text[pos:end], end, False


def _parse_let_assignment(
    text: str,
    start: int,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, str, int] | None:
    r"""Read the two TeX tokens in ``\let<target>[=]<source>``."""

    if budget is not None:
        budget.reserve_operation()
    target_pos = _skip_space(text, start)
    target = _read_tex_control_token(text, target_pos)
    if target is None:
        return None
    target_raw, target_end, target_is_word = target
    if budget is not None and target_is_word:
        budget.reserve_control_token()

    source_pos = _skip_space(text, target_end)
    if source_pos < len(text) and text[source_pos] == "=":
        source_pos = _skip_space(text, source_pos + 1)
    if source_pos >= len(text):
        return None
    source = _read_tex_control_token(text, source_pos)
    if source is None:
        return target_raw, text[source_pos], source_pos + 1
    source_raw, source_end, source_is_word = source
    if budget is not None and source_is_word:
        budget.reserve_control_token()
    return target_raw, source_raw, source_end


def _is_tex_control_word_start(text: str, pos: int) -> bool:
    """連続 backslash を先頭からtokenizeし、``pos`` がcontrol word開始か判定する。"""

    if pos >= len(text) or text[pos] != "\\":
        return False
    run_start = pos
    while run_start > 0 and text[run_start - 1] == "\\":
        run_start -= 1
    cursor = run_start
    while cursor <= pos:
        token = _read_tex_control_token(text, cursor)
        if token is None:
            return False
        _raw, end, is_word = token
        if cursor == pos:
            return is_word
        cursor = end
    return False


def _search_tex_command(pattern: re.Pattern[str], text: str, pos: int) -> re.Match[str] | None:
    """TeX token境界にあるcontrol wordだけをregexで検索する。"""

    match = pattern.search(text, pos)
    while match is not None and not _is_tex_control_word_start(text, match.start()):
        match = pattern.search(text, match.end())
    return match


def _strip_frontmatter_commands(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    """frontmatter commandを1 passで除去し、反復全文copyを避ける。"""

    out: list[str] = []
    copied_until = 0
    for match in _evaluated_matches(_FRONTMATTER_CMD_RE, text, budget):
        if match.start() < copied_until:
            continue
        _argument, end = _read_bounded_optional_braced(
            text,
            match.end(),
            budget,
        )
        end = max(end, match.end())
        out.append(text[copied_until : match.start()])
        copied_until = end
    if not out:
        return text
    out.append(text[copied_until:])
    if budget is not None:
        budget.ensure_emittable_parts(_iter_join_ranges(out, ""))
    return "".join(out)


def _consume_setup_command(
    text: str,
    start: int,
    cmd: str,
    budget: _LatexEvaluationBudget | None = None,
) -> int:
    """本文中に残ったマクロ定義・色定義など、表示しない setup command 全体を読む。"""
    i = start
    while i < len(text) and text[i].isspace():
        i += 1

    if cmd == "let":
        assignment = _parse_let_assignment(text, start, budget)
        return assignment[2] if assignment is not None else start

    if cmd == "def":
        m = _CONTROL_WORD_RE.match(text, i)
        if m:
            i = m.end()
        parameter_end = min(
            len(text),
            i + _MAX_LATEX_MACRO_PARAMETER_CHARS + 1,
        )
        open_pos = text.find("{", i, parameter_end)
        if open_pos < 0 and parameter_end < len(text) and budget is not None:
            budget._raise_limit()
        if open_pos >= 0:
            try:
                close_pos = _matching_brace(text, open_pos, budget)
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                return open_pos
            if budget is not None:
                budget.reserve_structure_match()
            i = close_pos + 1
        return i

    consumed_group = False
    group_count = 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        has_group = i < len(text) and text[i] in "[{"
        if has_group and group_count >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            if budget is not None:
                budget._raise_limit()
            _raise_unsupported_macro_group_limit()
        if i < len(text) and text[i] == "[":
            end = _matching_square(text, i, budget)
            if end is None:
                return i
            if budget is not None:
                budget.reserve_structure_match()
            i = end + 1
            consumed_group = True
            group_count += 1
            continue
        if i < len(text) and text[i] == "{":
            try:
                close_pos = _matching_brace(text, i, budget)
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                return i
            if budget is not None:
                budget.reserve_structure_match()
            i = close_pos + 1
            consumed_group = True
            group_count += 1
            continue
        break
    return i if consumed_group else start


def _strip_setup_commands(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    out: list[str] = []
    i = 0
    for match in _evaluated_matches(_SETUP_CMD_RE, text, budget):
        if match.start() < i:
            continue
        out.append(text[i : match.start()])
        i = _consume_setup_command(text, match.end(), match.group(1), budget)
    out.append(text[i:])
    if budget is not None:
        budget.ensure_emittable_parts(_iter_join_ranges(out, ""))
    return "".join(out)


# ============================================================================
# 文書固有マクロ
# ============================================================================


@dataclass(frozen=True)
class _MacroDefinition:
    """本文抽出に必要な範囲へ縮約した LaTeX マクロ定義。"""

    arg_count: int
    body: str
    optional_default: str | None = None


_MAX_SUPPORTED_MACRO_ARGUMENTS = 9
_MAX_LATEX_MACRO_PARAMETER_CHARS = 4_096
_NEWCOMMAND_RE = re.compile(
    r"\\(newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?(?![A-Za-z])"
)
_DEF_RE = re.compile(r"\\(?:def|gdef|edef|xdef)\s*\\([A-Za-z@]+)")
_MACRO_NAME_RE = re.compile(r"\\([A-Za-z@]+)\*?")
_PARAMETER_RE = re.compile(r"#([1-9])")


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _read_square(text: str, open_pos: int) -> tuple[str, int] | None:
    end = _matching_square(text, open_pos)
    if end is None:
        return None
    return text[open_pos + 1 : end], end + 1


def _parse_newcommand_definition(
    text: str,
    match: re.Match[str],
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, _MacroDefinition, int] | None:
    i = _skip_space(text, match.end())
    if i >= len(text):
        return None

    if text[i] == "{":
        try:
            raw_name, i = (
                _read_budgeted_braced(text, i, budget)
                if budget is not None
                else _read_braced(text, i)
            )
        except LatexParseError as error:
            if error.kind == "source_evaluation_limit":
                raise
            return None
        name_match = _MACRO_NAME_RE.fullmatch(raw_name.strip())
    else:
        name_match = _MACRO_NAME_RE.match(text, i)
        if name_match is not None:
            i = name_match.end()
    if name_match is None:
        return None
    name = name_match.group(1)

    i = _skip_space(text, i)
    arg_count = 0
    optional_default: str | None = None
    if i < len(text) and text[i] == "[":
        count_group = (
            _read_budgeted_square(text, i, budget) if budget is not None else _read_square(text, i)
        )
        if count_group is None:
            return None
        count_raw, i = count_group
        clean_count = count_raw.strip()
        if re.fullmatch(r"[0-9]+", clean_count):
            if len(clean_count) > 1 or int(clean_count) > _MAX_SUPPORTED_MACRO_ARGUMENTS:
                raise LatexParseError(
                    "parse_error",
                    "macro definition argument count exceeds the supported limit",
                )
            arg_count = int(clean_count)
            i = _skip_space(text, i)
            if i < len(text) and text[i] == "[":
                default_group = (
                    _read_budgeted_square(text, i, budget)
                    if budget is not None
                    else _read_square(text, i)
                )
                if default_group is None:
                    return None
                optional_default, i = default_group

    i = _skip_space(text, i)
    if i >= len(text) or text[i] != "{":
        return None
    try:
        body, end = (
            _read_budgeted_braced(text, i, budget) if budget is not None else _read_braced(text, i)
        )
    except LatexParseError as error:
        if error.kind == "source_evaluation_limit":
            raise
        return None
    return name, _MacroDefinition(arg_count, body, optional_default), end


def _parse_def_definition(
    text: str,
    match: re.Match[str],
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, _MacroDefinition, int, str] | None:
    name = match.group(1)
    parameter_end = min(
        len(text),
        match.end() + _MAX_LATEX_MACRO_PARAMETER_CHARS + 1,
    )
    open_pos = text.find("{", match.end(), parameter_end)
    if open_pos == -1:
        if parameter_end < len(text) and budget is not None:
            budget._raise_limit()
        return None
    if budget is not None:
        budget.reserve_operation()
        budget.reserve_evaluated_text(text, match.end(), open_pos)
    params = text[match.end() : open_pos]
    arg_count = max((int(m.group(1)) for m in _PARAMETER_RE.finditer(params)), default=0)
    try:
        body, end = (
            _read_budgeted_braced(text, open_pos, budget)
            if budget is not None
            else _read_braced(text, open_pos)
        )
    except LatexParseError as error:
        if error.kind == "source_evaluation_limit":
            raise
        return None
    return name, _MacroDefinition(arg_count, body), end, params


def _is_second_control_word_operand_of_primitive(text: str, command_start: int) -> bool:
    r"""Recognize ``\ifx\foo\def``/``\let\foo=\def`` token operands."""

    prefix_start = max(0, command_start - 512)
    previous = list(_CONTROL_WORD_RE.finditer(text, prefix_start, command_start))
    if len(previous) < 2:
        return False
    conditional, first_operand = previous[-2:]
    return bool(
        conditional.group(0) in {r"\ifx", r"\let"}
        and not text[conditional.end() : first_operand.start()].strip()
        and text[first_operand.end() : command_start].strip() in {"", "="}
    )


# Primitives whose next control-word token is a *reference* (its meaning is
# examined or copied), never a definition site.  A definition-shaped control
# word appearing here is an operand, e.g.
# ``\ifdefined\NewDocumentCommand`` or ``\let\foo=\def``.
_CONTROL_WORD_OPERAND_PRIMITIVES = frozenset(
    {r"\ifdefined", r"\ifx", r"\ifcsname", r"\let", r"\expandafter", r"\csname"}
)


def _is_control_word_operand_of_primitive(text: str, command_start: int) -> bool:
    r"""Recognize a control word used as a primitive's token operand.

    Covers the first operand of ``\ifdefined``/``\ifx``/``\let`` (as in
    ``\ifdefined\NewDocumentCommand``) as well as the second ``\ifx``/``\let``
    operand handled by :func:`_is_second_control_word_operand_of_primitive`.
    """

    if _is_second_control_word_operand_of_primitive(text, command_start):
        return True
    prefix_start = max(0, command_start - 512)
    previous = list(_CONTROL_WORD_RE.finditer(text, prefix_start, command_start))
    if not previous:
        return False
    preceding = previous[-1]
    return bool(
        preceding.group(0) in _CONTROL_WORD_OPERAND_PRIMITIVES
        and not text[preceding.end() : command_start].strip()
    )


# ============================================================================
# 汎用ブレース/環境スキャナ
# ============================================================================


def _matching_brace(
    text: str,
    open_pos: int,
    budget: _LatexEvaluationBudget | None = None,
    *,
    base_depth: int = 0,
) -> int:
    """`{` の位置から対応する `}` の位置をsliceせず返す。"""

    if open_pos >= len(text) or text[open_pos] != "{":
        raise LatexParseError("unbalanced_braces", f"expected '{{' at {open_pos}")
    depth = 1
    if budget is not None:
        budget.ensure_parser_depth(base_depth + depth)
    i = open_pos + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            inline_match = _INLINE_VERB_START_RE.match(text, i)
            if inline_match is not None:
                inline_argument = _read_inline_verb_argument(text, inline_match.end())
                if inline_argument is not None:
                    _literal, i = inline_argument
                    continue
            environment_match = _VERB_BEGIN_RE.match(text, i)
            if environment_match is not None:
                try:
                    _literal, i = _read_environment(
                        text, environment_match.end(), environment_match.group(1)
                    )
                except LatexParseError:
                    i = n
                continue
            i += 2
            continue
        if c == "{":
            depth += 1
            if budget is not None:
                budget.ensure_parser_depth(base_depth + depth)
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise LatexParseError("unbalanced_braces", "unbalanced braces in latex source")


def _read_braced(text: str, open_pos: int) -> tuple[str, int]:
    """`{` の位置(``open_pos``)から対応する `}` までを読む。"""

    close_pos = _matching_brace(text, open_pos)
    return text[open_pos + 1 : close_pos], close_pos + 1


def _read_budgeted_braced(
    text: str,
    open_pos: int,
    budget: _LatexEvaluationBudget,
) -> tuple[str, int]:
    close_pos = _matching_brace(text, open_pos, budget)
    budget.reserve_operation()
    budget.reserve_evaluated_text(text, open_pos + 1, close_pos)
    return text[open_pos + 1 : close_pos], close_pos + 1


def _read_budgeted_square(
    text: str,
    open_pos: int,
    budget: _LatexEvaluationBudget,
) -> tuple[str, int] | None:
    close_pos = _matching_square(text, open_pos, budget)
    if close_pos is None:
        return None
    budget.reserve_operation()
    budget.reserve_evaluated_text(text, open_pos + 1, close_pos)
    return text[open_pos + 1 : close_pos], close_pos + 1


def _matching_square(
    text: str,
    open_pos: int,
    budget: _LatexEvaluationBudget | None = None,
    *,
    base_depth: int = 0,
) -> int | None:
    depth = 1
    if budget is not None:
        budget.ensure_parser_depth(base_depth + depth)
    i = open_pos + 1
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            inline_match = _INLINE_VERB_START_RE.match(text, i)
            if inline_match is not None:
                inline_argument = _read_inline_verb_argument(text, inline_match.end())
                if inline_argument is not None:
                    _literal, i = inline_argument
                    continue
            i += 2
            continue
        if c == "[":
            depth += 1
            if budget is not None:
                budget.ensure_parser_depth(base_depth + depth)
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _read_optional_braced(text: str, pos: int) -> tuple[str | None, int]:
    """コマンド直後の空白と任意個の `[...]` を読み飛ばし、続く `{...}` があれば内容を返す。"""
    i = pos
    while i < len(text) and text[i].isspace():
        i += 1
    while i < len(text) and text[i] == "[":
        j = _matching_square(text, i)
        if j is None:
            return None, i
        i = j + 1
        while i < len(text) and text[i].isspace():
            i += 1
    if i < len(text) and text[i] == "{":
        content, end = _read_braced(text, i)
        return content, end
    return None, i


def _read_bounded_optional_braced(
    text: str,
    pos: int,
    budget: _LatexEvaluationBudget | None,
    *,
    base_depth: int = 0,
) -> tuple[str | None, int]:
    """post parser用。option/group数を課金し、slice前にサイズを検査する。"""

    if budget is None:
        return _read_optional_braced(text, pos)
    i = _skip_space(text, pos)
    option_count = 0
    while i < len(text) and text[i] == "[":
        option_count += 1
        if option_count > _MAX_UNSUPPORTED_MACRO_GROUPS:
            budget._raise_limit()
        end = _matching_square(
            text,
            i,
            budget,
            base_depth=base_depth,
        )
        if end is None:
            return None, i
        budget.reserve_structure_match()
        i = _skip_space(text, end + 1)
    if i < len(text) and text[i] == "{":
        close_pos = _matching_brace(
            text,
            i,
            budget,
            base_depth=base_depth,
        )
        budget.reserve_structure_match()
        budget.ensure_emittable_parts(iter(((text, i + 1, close_pos),)))
        return text[i + 1 : close_pos], close_pos + 1
    return None, i


def _read_macro_argument(text: str, pos: int) -> tuple[str | None, int]:
    """TeX の必須引数を 1 個読む(波括弧または単一トークン)。"""

    i = _skip_space(text, pos)
    if i >= len(text):
        return None, pos
    if text[i] == "{":
        return _read_braced(text, i)
    control = _read_tex_control_token(text, i)
    if control is not None:
        raw, end, _is_word = control
        return raw, end
    return text[i], i + 1


def _read_budgeted_macro_argument(
    text: str,
    pos: int,
    budget: _LatexEvaluationBudget,
) -> tuple[str | None, int]:
    """unsupported invocation引数を、slice前に共有budgetへ予約して読む。"""

    i = _skip_space(text, pos)
    if i >= len(text):
        return None, pos
    if text[i] == "{":
        return _read_budgeted_braced(text, i, budget)
    control = _read_tex_control_token(text, i)
    if control is not None:
        _raw, end, _is_word = control
        budget.reserve_operation()
        budget.reserve_evaluated_text(text, i, end)
        return text[i:end], end
    budget.reserve_operation()
    budget.reserve_evaluated_text(text, i, i + 1)
    return text[i], i + 1


def _read_environment(
    text: str,
    start: int,
    name: str,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, int]:
    """`\\begin{name}` の直後(``start``)から対応する `\\end{name}` までを読む(同名の入れ子対応)。"""
    boundary_pattern = _ENVIRONMENT_BOUNDARY_RE
    if name in {"verbatim", "verbatim*", "lstlisting", "minted"}:
        end = next(
            (
                match
                for match in boundary_pattern.finditer(text, start)
                if match.group("kind") == "end"
                and match.group("name") == name
                and _is_tex_control_word_start(text, match.start())
            ),
            None,
        )
        if end is None:
            raise LatexParseError("unterminated_environment", f"unterminated environment: {name}")
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_control_token()
            budget.ensure_emittable_parts(iter(((text, start, end.start()),)))
        return text[start : end.start()], end.end()

    target_depth = 1
    total_depth = 1
    if budget is not None:
        budget.ensure_parser_depth(total_depth)
    literal = _next_literal_region(text, start)
    for boundary in boundary_pattern.finditer(text, start):
        if not _is_tex_control_word_start(text, boundary.start()):
            continue
        # ``\string\begin{...}`` is a logging/diagnostic string, not an
        # environment boundary.  Treating it as a real begin makes classes
        # such as MLSys report an unterminated document environment.
        if re.search(r"\\string\s*$", text[max(0, boundary.start() - 32) : boundary.start()]):
            continue
        while literal is not None and literal[1] <= boundary.start():
            literal = _next_literal_region(text, literal[1])
        if literal is not None and literal[0] <= boundary.start() < literal[1]:
            continue
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_control_token()
        boundary_name = boundary.group("name")
        if boundary.group("kind") == "begin":
            total_depth += 1
            if budget is not None:
                budget.ensure_parser_depth(total_depth)
            if boundary_name == name:
                target_depth += 1
            continue
        total_depth = max(0, total_depth - 1)
        if boundary_name != name:
            continue
        target_depth -= 1
        if target_depth == 0:
            if budget is not None:
                budget.ensure_emittable_parts(iter(((text, start, boundary.start()),)))
            return text[start : boundary.start()], boundary.end()
    raise LatexParseError("unterminated_environment", f"unterminated environment: {name}")


def _read_inline_verb_argument(text: str, pos: int) -> tuple[str, int] | None:
    """``verb`` / ``verb*`` の区切り文字形式を 1 個読む。"""

    if pos >= len(text) or text[pos].isspace() or text[pos] == "{":
        return None
    delimiter = text[pos]
    end = text.find(delimiter, pos + 1)
    if end == -1:
        return None
    line_end = text.find("\n", pos + 1, end)
    if line_end != -1:
        return None
    return text[pos + 1 : end], end + 1


def _next_literal_region(text: str, pos: int) -> tuple[int, int] | None:
    """次の verbatim 系環境または inline verb の非評価範囲を返す。"""

    for match in _LITERAL_START_RE.finditer(text, pos):
        if not _is_tex_control_word_start(text, match.start()):
            continue
        environment_name = match.group("environment")
        if environment_name is not None:
            try:
                _inner, end = _read_environment(
                    text,
                    match.end(),
                    environment_name,
                )
            except LatexParseError:
                end = len(text)
            return match.start(), end
        argument = _read_inline_verb_argument(text, match.end())
        if argument is not None:
            _content, end = argument
            return match.start(), end
    return None


# 最上位構造tokenを1本の前進専用iteratorで走査する。
# worker の translated-PDF source walker が import する互換定数。parser本体は下の
# combined patternを使い、候補ごとの再検索は行わない。
_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z]+\*?)\}")
_TOP_LEVEL_STRUCTURAL_RE = re.compile(
    r"(?P<display_dollar>\$\$)|"
    r"\\(?:"
    r"(?P<section>section|subsection|subsubsection)(?P<star>\*)?(?![A-Za-z])|"
    r"begin\{(?P<environment>[a-zA-Z]+\*?)\}|"
    r"(?P<appendix>appendix|beginappendix)\b"
    r")"
)
_LABEL_AFTER_RE = re.compile(r"\s*\\label\{([^}]*)\}")

_LEVEL_OF = {"section": 1, "subsection": 2, "subsubsection": 3}


def _read_section_title(
    text: str,
    pos: int,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, int] | None:
    """節コマンド直後の任意の短縮題 ``[...]`` と本題 ``{...}`` を読む。"""

    title, end = _read_bounded_optional_braced(text, pos, budget)
    return (title, end) if title is not None else None


def _environment_prefix(
    inner: str,
    *,
    required_args: int = 0,
    budget: _LatexEvaluationBudget | None = None,
    base_depth: int = 0,
) -> tuple[list[str], list[str], str]:
    """環境本体先頭の ``[...]`` と既知個数の ``{...}`` 引数を本文から分離する。"""

    i = _skip_space(inner, 0)
    options: list[str] = []
    while i < len(inner) and inner[i] == "[":
        if len(options) >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            if budget is not None:
                budget._raise_limit()
            _raise_unsupported_macro_group_limit()
        close_pos = _matching_square(
            inner,
            i,
            budget,
            base_depth=base_depth,
        )
        if close_pos is None:
            break
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_ir_object()
            budget.ensure_emittable_parts(iter(((inner, i + 1, close_pos),)))
        options.append(inner[i + 1 : close_pos])
        i = close_pos + 1
        i = _skip_space(inner, i)

    arguments: list[str] = []
    for _ in range(required_args):
        if i >= len(inner) or inner[i] != "{":
            break
        close_pos = _matching_brace(
            inner,
            i,
            budget,
            base_depth=base_depth,
        )
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_ir_object()
            budget.ensure_emittable_parts(iter(((inner, i + 1, close_pos),)))
        arguments.append(inner[i + 1 : close_pos])
        i = close_pos + 1
        i = _skip_space(inner, i)
    if budget is not None:
        budget.ensure_emittable_parts(iter(((inner, i, len(inner)),)))
    return options, arguments, inner[i:]


def _iter_option_list(
    raw: str,
    budget: _LatexEvaluationBudget | None = None,
) -> Iterator[str]:
    """tcolorbox optionを全件list化せず、波括弧内のカンマを保って返す。"""

    start = 0
    curly = 0
    square = 0
    escaped = False
    for i, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            curly += 1
        elif char == "}":
            curly = max(0, curly - 1)
        elif char == "[":
            square += 1
        elif char == "]":
            square = max(0, square - 1)
        elif char == "," and curly == 0 and square == 0:
            if budget is not None:
                budget.reserve_structure_match()
                budget.reserve_ir_object()
            item = raw[start:i].strip()
            if item:
                yield item
            start = i + 1
    item = raw[start:].strip()
    if item:
        if budget is not None:
            budget.reserve_ir_object()
        yield item


def _environment_option(
    options: list[str],
    key: str,
    budget: _LatexEvaluationBudget | None = None,
    *,
    base_depth: int = 0,
) -> str | None:
    for option_group in options:
        for item in _iter_option_list(option_group, budget):
            name, separator, value = item.partition("=")
            if not separator or name.strip() != key:
                continue
            clean = value.strip()
            if clean.startswith("{"):
                try:
                    close_pos = _matching_brace(
                        clean,
                        0,
                        budget,
                        base_depth=base_depth,
                    )
                    if budget is not None:
                        budget.reserve_structure_match()
                        budget.ensure_emittable_parts(iter(((clean, 1, close_pos),)))
                    unwrapped, end = clean[1:close_pos], close_pos + 1
                except LatexParseError as error:
                    if error.kind == "source_evaluation_limit":
                        raise
                    return clean
                if not clean[end:].strip():
                    return unwrapped
            return clean
    return None


def _iter_top_level(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> list[tuple[Any, ...]]:
    """最上位ノード列(`section` / `appendix` / `env` / `text`)を文書順で返す。"""
    nodes: list[tuple[Any, ...]] = []

    def append_text(chunk: str) -> None:
        if not chunk.strip():
            return
        if budget is not None:
            budget.reserve_structure_match()
        nodes.append(("text", chunk))

    i = 0
    n = len(text)
    literal = _next_literal_region(text, 0)
    structural_matches = _TOP_LEVEL_STRUCTURAL_RE.finditer(text)

    def is_escaped(position: int) -> bool:
        backslashes = 0
        cursor = position - 1
        while cursor >= 0 and text[cursor] == "\\":
            if budget is not None:
                budget.reserve_operation()
            backslashes += 1
            cursor -= 1
        return backslashes % 2 == 1

    def next_structural() -> re.Match[str] | None:
        for candidate in structural_matches:
            if candidate.group("display_dollar") is not None:
                if is_escaped(candidate.start()):
                    continue
                if budget is not None:
                    budget.reserve_structure_match()
            else:
                if not _is_tex_control_word_start(text, candidate.start()):
                    continue
                if re.search(r"\\string\s*$", text[max(0, candidate.start() - 32) : candidate.start()]):
                    continue
                if budget is not None:
                    budget.reserve_structure_match()
                    budget.reserve_control_token()
            return candidate
        return None

    def next_display_end(start: int) -> int:
        end = text.find("$$", start)
        while end >= 0 and is_escaped(end):
            if budget is not None:
                budget.reserve_structure_match()
            end = text.find("$$", end + 2)
        return end

    structural = next_structural()
    while i < n:
        if budget is not None:
            budget.reserve_operation()
        while structural is not None and structural.start() < i:
            structural = next_structural()
        while literal is not None and literal[1] <= i:
            literal = _next_literal_region(text, literal[1])
        if literal is not None and (structural is None or literal[0] <= structural.start()):
            start, end = literal
            append_text(text[i:start])
            environment = _VERB_BEGIN_RE.match(text, start)
            if environment is None:
                append_text(text[start:end])
            else:
                inner, parsed_end = _read_environment(
                    text,
                    environment.end(),
                    environment.group(1),
                    budget,
                )
                if budget is not None:
                    budget.reserve_structure_match()
                nodes.append(("env", environment.group(1), inner))
                end = parsed_end
            i = end
            literal = _next_literal_region(text, end)
            continue
        if structural is None:
            append_text(text[i:])
            break
        m = structural
        structural = next_structural()
        if m.start() > i:
            append_text(text[i : m.start()])
        if m.group("display_dollar") is not None:
            end = next_display_end(m.end())
            if end < 0:
                append_text(text[m.start() :])
                break
            if budget is not None:
                budget.ensure_emittable_parts(iter(((text, m.end(), end),)))
            nodes.append(("env", "equation*", text[m.end() : end]))
            i = end + 2
            continue
        if m.group("appendix") is not None:
            nodes.append(("appendix",))
            i = m.end()
            continue
        section_name = m.group("section")
        if section_name is not None:
            level = _LEVEL_OF[section_name]
            starred = m.group("star") == "*"
            title_group = _read_section_title(text, m.end(), budget)
            if title_group is None:
                i = m.end()
                continue
            title_raw, end = title_group
            label: str | None = None
            lm = _LABEL_AFTER_RE.match(text, end)
            if lm:
                label = lm.group(1).strip()
                end = lm.end()
            nodes.append(("section", level, title_raw, label, starred))
            i = end
            continue
        name = m.group("environment")
        assert name is not None
        inner, end = _read_environment(text, m.end(), name, budget)
        nodes.append(("env", name, inner))
        i = end
    return nodes


# ============================================================================
# reference_entry 構造化 / 簡易マークアップ除去
# ============================================================================

_ARXIV_RE = re.compile(
    r"(?:arXiv:|arxiv\.org/abs/)\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.IGNORECASE
)
_YEAR_PAREN_RE = re.compile(r"\((19|20)\d{2}\)")
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_EMPH_TITLE_RE = re.compile(r"\\(?:emph|textit)\{([^{}]+)\}")
_QUOTE_TITLE_RE = re.compile('[\u201c"\u2018]([^\u201d"\u2019]+)[\u201d"\u2019]')
_DOI_RE = re.compile(r"doi\.org/(\S+)", re.IGNORECASE)
_SENTENCE_BOUNDARY_RE = re.compile(r"\.\s+")


def _structure_reference(
    raw: str,
    budget: _LatexEvaluationBudget | None = None,
) -> dict[str, str] | None:
    out: dict[str, str] = {}
    am = _ARXIV_RE.search(raw)
    if am:
        out["arxiv_id"] = am.group(1)
    ym = _YEAR_PAREN_RE.search(raw)
    if ym:
        out["year"] = ym.group()[1:-1]
    else:
        # 末尾に出版年が置かれる書式が多いため、複数マッチ時は最後の一致を採る
        # (DOI/arXiv ID に埋め込まれた年紀らしき数字列を誤って採らないため)。
        last_year: re.Match[str] | None = None
        for year_match in _YEAR_RE.finditer(raw):
            if budget is not None:
                budget.reserve_structure_match()
            last_year = year_match
        if last_year is not None:
            out["year"] = last_year.group()
    tm = _EMPH_TITLE_RE.search(raw)
    if tm:
        out["title"] = _collapse(tm.group(1))
    else:
        qm = _QUOTE_TITLE_RE.search(raw)
        if qm:
            out["title"] = qm.group(1).strip()
        else:
            sentence_boundary = _SENTENCE_BOUNDARY_RE.search(raw)
            if sentence_boundary is not None:
                if budget is not None:
                    budget.reserve_structure_match()
                next_boundary = _SENTENCE_BOUNDARY_RE.search(
                    raw,
                    sentence_boundary.end(),
                )
                title_end = next_boundary.start() if next_boundary is not None else len(raw)
                out["title"] = raw[sentence_boundary.end() : title_end].strip()
    dm = _DOI_RE.search(raw)
    if dm:
        out["doi"] = dm.group(1).rstrip(".,;}])\"'")
    return out or None


_CMD_WITH_ARG_RE = re.compile(
    r"\\(?:emph|textit|textbf|textsc|texttt|uline|underline|textrm|textsf|textnormal)"
    r"\{([^{}]*)\}"
)
_URL_WITH_ARG_RE = re.compile(r"\\(?:url|doi)\{([^{}]*)\}")
_HREF_RE = re.compile(r"\\href\{[^{}]*\}\{([^{}]*)\}")
_BARE_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?")


def _strip_markup(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    """表示用の簡易マークアップ除去(`\\emph{X}` → `X` 等)。構造化失敗でも読める表示に。"""
    if budget is not None:
        budget.reserve_operation(3)
    out = _HREF_RE.sub(r"\1", text)
    out = _URL_WITH_ARG_RE.sub(r"\1", out)
    out = _CMD_WITH_ARG_RE.sub(r"\1", out)
    out = out.replace("~", " ")
    out = out.replace("\\\\", " ").replace("\\ ", " ")
    out = out.replace("\\&", "&").replace("\\_", "_").replace("\\%", "%")
    out = _BARE_CMD_RE.sub(" ", out)
    out = out.replace("{", "").replace("}", "")
    return _collapse(out)


def _flatten_plain(inlines: list[Inline]) -> str:
    parts: list[str] = []
    for il in inlines:
        if il.t in ("text", "emphasis", "code_inline"):
            parts.append(il.v)
        elif il.t == "math_inline":
            parts.append(f"${il.v}$")
        elif il.t == "url":
            parts.append(il.v or il.href or "")
    return _collapse(" ".join(p for p in parts if p))


def _merge_text(inlines: list[Inline]) -> list[Inline]:
    out: list[Inline] = []
    text_parts: list[str] = []

    def flush_text() -> None:
        if not text_parts:
            return
        out.append(Inline(t="text", v=_WS.sub(" ", "".join(text_parts))))
        text_parts.clear()

    for il in inlines:
        if il.t == "text":
            text_parts.append(il.v)
            continue
        flush_text()
        out.append(il)
    flush_text()
    while out and out[0].t == "text" and out[0].v == " ":
        out.pop(0)
    while out and out[-1].t == "text" and out[-1].v == " ":
        out.pop()
    return out


def _append_text(
    out: list[Inline],
    raw: str,
    budget: _LatexEvaluationBudget | None = None,
) -> None:
    if raw.strip():
        # TeX の本文用引用符・ダッシュはソース記号ではなく表示文字へ正規化する。
        normalized = raw.replace("``", "“").replace("''", "”")
        normalized = normalized.replace("---", "\u2014").replace("--", "\u2013")
        if budget is not None:
            budget.reserve_ir_object()
        out.append(Inline(t="text", v=_WS.sub(" ", normalized)))
    elif raw:
        if budget is not None:
            budget.reserve_ir_object()
        out.append(Inline(t="text", v=" "))


# 種別名 → 表示名(theorem 系。plans/05 §4.2 の「種別名+番号」を LaTeX でも保持)。
_THEOREM_ENVS = {
    "theorem": "Theorem",
    "assumption": "Assumption",
    "lemma": "Lemma",
    "corollary": "Corollary",
    "proposition": "Proposition",
    "definition": "Definition",
    "remark": "Remark",
    "claim": "Claim",
    "example": "Example",
    "proof": "Proof",
}

# tabularray (tblr 系) のセル/行頭書式コマンド。グリッド抽出前に除去する。
_TBLR_CELL_PREFIX_RE = re.compile(r"\\Set(?:Cell|Row)\b")

_CITE_CMDS = {"cite", "citet", "citep", "citeauthor", "citeyear", "citealt", "citealp"}
_REF_CMDS = {"ref", "eqref", "autoref", "cref", "Cref", "nameref"}
_PRESENTATION_SWITCH_CMDS = {
    "centering",
    "raggedleft",
    "raggedright",
    "itshape",
    "upshape",
    "bfseries",
    "mdseries",
    "rmfamily",
    "sffamily",
    "ttfamily",
    "tiny",
    "scriptsize",
    "footnotesize",
    "small",
    "normalsize",
    "large",
    "Large",
    "LARGE",
    "huge",
    "Huge",
}
_NO_OUTPUT_CMDS = {
    "noindent",
    "par",
    "newline",
    "clearpage",
    "newpage",
    "bigskip",
    "medskip",
    "smallskip",
    "vfill",
    "hfill",
    "label",
    "displaystyle",
    "textstyle",
    "scriptstyle",
    "scriptscriptstyle",
    "selectfont",
    "FloatBarrier",
    "sloppy",
    "fussy",
    "protect",
    "unskip",
    *_PRESENTATION_SWITCH_CMDS,
}
_DISCARD_ARGUMENT_CMDS = {
    "vspace": 1,
    "hspace": 1,
    "addvspace": 1,
    "enlargethispage": 1,
    "setlength": 2,
    "addtolength": 2,
    "setcounter": 2,
    "addtocounter": 2,
    "Needspace": 1,
    "needspace": 1,
    "color": 1,
    "pagecolor": 1,
    "thispagestyle": 1,
    "pagestyle": 1,
    "begin": 1,
    "end": 1,
}
_DISCARD_DIMENSION_CMDS = {"kern", "hskip", "vskip"}
_DIMENSION_RE = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)?(?:\\[A-Za-z]+|[A-Za-z]+)"
    r"(?:\s+(?:plus|minus)\s+\S+)*"
)
_SPACE_CMDS = {"quad", "qquad"}
_SYMBOL_CMDS = {
    "LaTeX": "LaTeX",
    "TeX": "TeX",
    "eg": "e.g.",
    "ie": "i.e.",
    "etal": "et al.",
    "ldots": "...",
    "cdots": "...",
    "dots": "...",
    "checkmark": "✓",
    "times": "\u00d7",
}

_SPECIAL_RE = re.compile(r"\$|\\\(|\\\)|\\\[|\\\]|\\\\|\\\s|\\[A-Za-z@]+\*?|\\[%&_#{}$~^]|~|[{}]")
_BIBITEM_RE = re.compile(r"\\bibitem(?:\[([^\]]*)\])?\{([^}]+)\}")
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}")
_MAKETITLE_RE = re.compile(r"\\maketitle\*?(?![A-Za-z])")
_THEBIB_BEGIN_RE = re.compile(r"\\begin\{thebibliography\}")
_STRUCTURAL_CONTROL_WORDS = (
    frozenset(
        {
            "LoadClass",
            "LoadClassWithOptions",
            "RequirePackage",
            "RequirePackageWithOptions",
            "addbibresource",
            "appendix",
            "begin",
            "beginappendix",
            "bibitem",
            "bibliography",
            "caption",
            "documentclass",
            "end",
            "footnote",
            "href",
            "include",
            "includegraphics",
            "includepdf",
            "input",
            "item",
            "label",
            "maketitle",
            "printbibliography",
            "section",
            "subsection",
            "subsubsection",
            "url",
            "usepackage",
        }
    )
    | frozenset(_CITE_CMDS)
    | frozenset(_REF_CMDS)
    | frozenset(_FRONTMATTER_CMDS)
)
_PRESERVED_SEMANTIC_COMMANDS = _STRUCTURAL_CONTROL_WORDS - {"maketitle"}
_STRUCTURAL_CONTROL_SYMBOLS = frozenset({r"\(", r"\)", r"\[", r"\]"})
_STRUCTURAL_SYMBOL_SCAN_RE = re.compile(r"[\\$]")

_MAX_DISPLAY_MACRO_DEPTH = 32
_MAX_DISPLAY_MACRO_INVOCATIONS = 65_536
_MAX_DISPLAY_MACRO_GROWTH = 8 * 1024 * 1024
_MAX_LATEX_SOURCE_VISITS = 50_000
_MAX_LATEX_EVALUATED_CHARS = 256 * 1024 * 1024
_MAX_LATEX_EVALUATED_BYTES = 512 * 1024 * 1024
_MAX_LATEX_EMITTED_CHARS = 128 * 1024 * 1024
_MAX_LATEX_EMITTED_BYTES = 256 * 1024 * 1024
_MAX_LATEX_EVALUATION_OPERATIONS = 2_000_000
_MAX_LATEX_OUTPUT_CHUNKS = 500_000
_MAX_LATEX_CONTROL_TOKENS = 1_000_000
_MAX_LATEX_STRUCTURE_MATCHES = 100_000
_MAX_LATEX_IR_OBJECTS = 20_000
_MAX_LATEX_PARSER_DEPTH = 128


@dataclass
class _MacroExpansionBudget:
    """文書固有マクロの構造展開に対する共有上限。"""

    invocations: int = _MAX_DISPLAY_MACRO_INVOCATIONS
    growth_chars: int = _MAX_DISPLAY_MACRO_GROWTH


@dataclass
class _LatexEvaluationBudget:
    """1文書のsource評価・中間出力に共有する決定的budget。"""

    max_source_visits: int
    max_evaluated_chars: int
    max_evaluated_bytes: int
    max_emitted_chars: int
    max_emitted_bytes: int
    max_operations: int
    max_output_chunks: int
    max_control_tokens: int
    max_structure_matches: int
    max_ir_objects: int
    max_parser_depth: int
    source_visits: int = 0
    evaluated_chars: int = 0
    evaluated_bytes: int = 0
    emitted_chars: int = 0
    emitted_bytes: int = 0
    operations: int = 0
    output_chunks: int = 0
    control_tokens: int = 0
    structure_matches: int = 0
    ir_objects: int = 0

    @classmethod
    def from_limits(cls) -> _LatexEvaluationBudget:
        return cls(
            max_source_visits=_MAX_LATEX_SOURCE_VISITS,
            max_evaluated_chars=_MAX_LATEX_EVALUATED_CHARS,
            max_evaluated_bytes=_MAX_LATEX_EVALUATED_BYTES,
            max_emitted_chars=_MAX_LATEX_EMITTED_CHARS,
            max_emitted_bytes=_MAX_LATEX_EMITTED_BYTES,
            max_operations=_MAX_LATEX_EVALUATION_OPERATIONS,
            max_output_chunks=_MAX_LATEX_OUTPUT_CHUNKS,
            max_control_tokens=_MAX_LATEX_CONTROL_TOKENS,
            max_structure_matches=_MAX_LATEX_STRUCTURE_MATCHES,
            max_ir_objects=_MAX_LATEX_IR_OBJECTS,
            max_parser_depth=_MAX_LATEX_PARSER_DEPTH,
        )

    @staticmethod
    def _raise_limit() -> NoReturn:
        raise LatexParseError(
            "source_evaluation_limit",
            "latex source evaluation exceeded a deterministic resource limit",
        )

    def _utf8_bytes_within(
        self,
        text: str,
        start: int,
        end: int,
        remaining: int,
    ) -> int:
        total = 0
        for index in range(start, end):
            codepoint = ord(text[index])
            total += (
                1
                if codepoint <= 0x7F
                else 2
                if codepoint <= 0x7FF
                else 3
                if codepoint <= 0xFFFF
                else 4
            )
            if total > remaining:
                self._raise_limit()
        return total

    def reserve_source_visit(self) -> None:
        if self.source_visits >= self.max_source_visits:
            self._raise_limit()
        self.source_visits += 1

    def reserve_operation(self, count: int = 1) -> None:
        if count < 0 or self.operations + count > self.max_operations:
            self._raise_limit()
        self.operations += count

    def reserve_control_token(self) -> None:
        if self.control_tokens >= self.max_control_tokens:
            self._raise_limit()
        self.control_tokens += 1

    def reserve_structure_match(self, count: int = 1) -> None:
        self.reserve_operation(count)
        if count < 0 or self.structure_matches + count > self.max_structure_matches:
            self._raise_limit()
        self.structure_matches += count

    def reserve_ir_object(self, count: int = 1) -> None:
        self.reserve_operation(count)
        if count < 0 or self.ir_objects + count > self.max_ir_objects:
            self._raise_limit()
        self.ir_objects += count

    def ensure_parser_depth(self, depth: int) -> None:
        self.reserve_operation()
        if depth > self.max_parser_depth:
            self._raise_limit()

    def reserve_evaluated_size(self, chars: int, byte_count: int) -> None:
        if (
            chars < 0
            or byte_count < 0
            or self.evaluated_chars + chars > self.max_evaluated_chars
            or self.evaluated_bytes + byte_count > self.max_evaluated_bytes
        ):
            self._raise_limit()
        self.evaluated_chars += chars
        self.evaluated_bytes += byte_count

    def reserve_evaluated_text(self, text: str, start: int = 0, end: int | None = None) -> None:
        stop = len(text) if end is None else end
        chars = stop - start
        if chars < 0 or self.evaluated_chars + chars > self.max_evaluated_chars:
            self._raise_limit()
        byte_count = self._utf8_bytes_within(
            text,
            start,
            stop,
            self.max_evaluated_bytes - self.evaluated_bytes,
        )
        self.reserve_evaluated_size(chars, byte_count)

    def reserve_emitted_text(self, text: str, start: int = 0, end: int | None = None) -> bool:
        stop = len(text) if end is None else end
        chars = stop - start
        if chars <= 0:
            return False
        if (
            self.output_chunks >= self.max_output_chunks
            or self.emitted_chars + chars > self.max_emitted_chars
        ):
            self._raise_limit()
        byte_count = self._utf8_bytes_within(
            text,
            start,
            stop,
            self.max_emitted_bytes - self.emitted_bytes,
        )
        self.output_chunks += 1
        self.emitted_chars += chars
        self.emitted_bytes += byte_count
        return True

    def ensure_final_output(self, text: str) -> None:
        if len(text) > self.max_emitted_chars:
            self._raise_limit()
        self._utf8_bytes_within(text, 0, len(text), self.max_emitted_bytes)

    def ensure_emittable_parts(
        self,
        parts: Iterator[tuple[str, int, int]],
    ) -> None:
        """複数sliceのjoin/concatを、文字列生成前に絶対出力上限で検査する。"""

        chars = 0
        byte_count = 0
        for text, start, end in parts:
            chars += end - start
            if chars > self.max_emitted_chars:
                self._raise_limit()
            byte_count += self._utf8_bytes_within(
                text,
                start,
                end,
                self.max_emitted_bytes - byte_count,
            )


def _iter_join_ranges(parts: list[str], separator: str) -> Iterator[tuple[str, int, int]]:
    for index, part in enumerate(parts):
        if index:
            yield separator, 0, len(separator)
        yield part, 0, len(part)


def _join_emittable(parts: list[str], separator: str, budget: _LatexEvaluationBudget) -> str:
    """join結果を生成する前に出力絶対上限を検査する。"""

    budget.ensure_emittable_parts(_iter_join_ranges(parts, separator))
    return separator.join(parts)


def _evaluated_matches(
    pattern: re.Pattern[str],
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> Iterator[re.Match[str]]:
    """リテラル範囲外の一致を、全件list化せず文書順に返す。"""

    literal = _next_literal_region(text, 0)
    for match in pattern.finditer(text):
        if not _is_tex_control_word_start(text, match.start()):
            continue
        while literal is not None and match.start() >= literal[1]:
            literal = _next_literal_region(text, literal[1])
        if literal is not None and literal[0] <= match.start() < literal[1]:
            continue
        if budget is not None:
            budget.reserve_structure_match()
            budget.reserve_control_token()
        yield match


def _evaluated_includegraphics_matches(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> Iterator[re.Match[str]]:
    return _evaluated_matches(_INCLUDEGRAPHICS_RE, text, budget)


def _raise_macro_expansion_limit(reason: str) -> None:
    raise LatexParseError(
        "macro_expansion_limit",
        f"display macro expansion exceeded deterministic {reason} limit",
    )


def _instantiate_macro_source(
    text: str,
    pos: int,
    definition: _MacroDefinition,
    budget: _LatexEvaluationBudget,
) -> tuple[int, str]:
    """定義へ引数を代入する。展開サイズを予約してから文字列を生成する。"""

    args: list[str] = []
    i = pos
    if definition.optional_default is not None:
        option_pos = _skip_space(text, i)
        if option_pos < len(text) and text[option_pos] == "[":
            option = _read_budgeted_square(text, option_pos, budget)
            if option is not None:
                value, i = option
                args.append(value)
            else:
                args.append(definition.optional_default)
        else:
            args.append(definition.optional_default)

    required = definition.arg_count - (1 if definition.optional_default is not None else 0)
    for _ in range(max(0, required)):
        argument, end = _read_budgeted_macro_argument(text, i, budget)
        if argument is None:
            argument = ""
        else:
            i = end
        args.append(argument)

    # ``\\name{}`` は 0 引数マクロ後の空グループであり、表示内容を持たない。
    if definition.arg_count == 0:
        empty_pos = _skip_space(text, i)
        if empty_pos < len(text) and text[empty_pos] == "{":
            try:
                empty, end = _read_budgeted_braced(text, empty_pos, budget)
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                empty = "not-empty"
                end = i
            if not empty.strip():
                i = end

    argument_sizes = [
        (
            len(value),
            budget._utf8_bytes_within(
                value,
                0,
                len(value),
                budget.max_evaluated_bytes,
            ),
        )
        for value in args
    ]
    expanded_chars = 0
    expanded_bytes = 0
    body = definition.body
    body_pos = 0
    while body_pos < len(body):
        budget.reserve_operation()
        if body[body_pos] == "#" and body_pos + 1 < len(body):
            marker = body[body_pos + 1]
            if marker == "#":
                expanded_chars += 1
                expanded_bytes += 1
                body_pos += 2
                continue
            if marker.isdigit():
                argument_index = int(marker) - 1
                if 0 <= argument_index < len(argument_sizes):
                    chars, byte_count = argument_sizes[argument_index]
                    expanded_chars += chars
                    expanded_bytes += byte_count
                    body_pos += 2
                    continue
        codepoint = ord(body[body_pos])
        expanded_chars += 1
        expanded_bytes += (
            1 if codepoint <= 0x7F else 2 if codepoint <= 0x7FF else 3 if codepoint <= 0xFFFF else 4
        )
        body_pos += 1
    budget.reserve_evaluated_size(expanded_chars, expanded_bytes)

    pieces: list[str] = []
    body_pos = 0
    literal_start = 0
    while body_pos < len(body):
        replacement: str | None = None
        if body[body_pos] == "#" and body_pos + 1 < len(body):
            marker = body[body_pos + 1]
            if marker == "#":
                replacement = "#"
            elif marker.isdigit():
                argument_index = int(marker) - 1
                if 0 <= argument_index < len(args):
                    replacement = args[argument_index]
        if replacement is None:
            body_pos += 1
            continue
        if literal_start < body_pos:
            pieces.append(body[literal_start:body_pos])
        pieces.append(replacement)
        body_pos += 2
        literal_start = body_pos
    if literal_start < len(body):
        pieces.append(body[literal_start:])
    separated: list[str] = []
    separator_count = 0
    previous_nonempty = ""
    for piece in pieces:
        if (
            piece
            and previous_nonempty
            and piece[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz@"
            and re.search(r"\\[A-Za-z@]+\Z", previous_nonempty) is not None
        ):
            # TeX tokenizes the macro body and its argument before substitution.  The
            # string evaluator must retain that token boundary (``\else#1`` must not
            # become a new command such as ``\elseOuter``).
            separated.append(" ")
            separator_count += 1
        separated.append(piece)
        if piece:
            previous_nonempty = piece
    if separator_count:
        budget.reserve_evaluated_size(separator_count, separator_count)
    return i, "".join(separated)


_MAKETITLE_FIELD_ORDER = (
    "title",
    "author",
    "IEEEauthorblockN",
    "IEEEauthorblockA",
    "institute",
    "affil",
    "affiliation",
    "email",
    "thanks",
    "date",
)
_MAKETITLE_SINGLETON_FIELDS = frozenset({"title", "author", "date"})


@dataclass(frozen=True)
class _UnsupportedMacroDefinition:
    """名前と body は読めるが、引数言語を完全には評価できない定義。"""

    body: str
    argument_spec: str
    family: str
    argument_layout: tuple[str, ...] | None = None


_UNSUPPORTED_DEFINITION_RE = re.compile(
    r"\\("
    r"NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand|DeclareDocumentCommand|"
    r"newrobustcmd|renewrobustcmd|providerobustcmd|"
    r"newcommandx|renewcommandx|providecommandx"
    r")\*?(?![A-Za-z])"
)
_MAX_UNSUPPORTED_MACRO_GROUPS = 32
_MAX_UNSUPPORTED_ARGUMENT_SPEC_TOKENS = 256


def _raise_unsupported_macro_group_limit() -> NoReturn:
    raise LatexParseError(
        "unsupported_structural_macro",
        "an unsupported macro has too many groups to evaluate safely",
    )


def _raise_unsupported_argument_spec_limit() -> NoReturn:
    raise LatexParseError(
        "unsupported_structural_macro",
        "an unsupported macro argument specification exceeds the deterministic limit",
    )


def _parse_unsupported_macro_definition(
    text: str,
    match: re.Match[str],
    budget: _LatexEvaluationBudget,
) -> tuple[str, _UnsupportedMacroDefinition, int] | None:
    i = _skip_space(text, match.end())
    if i >= len(text):
        return None
    if text[i] == "{":
        try:
            raw_name, i = _read_budgeted_braced(text, i, budget)
        except LatexParseError as error:
            if error.kind == "source_evaluation_limit":
                raise
            return None
        name_match = _MACRO_NAME_RE.fullmatch(raw_name.strip())
    else:
        name_match = _MACRO_NAME_RE.match(text, i)
        if name_match is not None:
            i = name_match.end()
    if name_match is None:
        return None

    groups: list[tuple[str, str]] = []
    while True:
        i = _skip_space(text, i)
        has_group = i < len(text) and text[i] in "[{"
        if has_group and len(groups) >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            _raise_unsupported_macro_group_limit()
        if i < len(text) and text[i] == "[":
            square = _read_budgeted_square(text, i, budget)
            if square is None:
                return None
            value, i = square
            groups.append(("square", value))
            continue
        if i < len(text) and text[i] == "{":
            try:
                value, i = _read_budgeted_braced(text, i, budget)
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                return None
            groups.append(("brace", value))
            continue
        break
    body_index = next(
        (index for index in range(len(groups) - 1, -1, -1) if groups[index][0] == "brace"),
        None,
    )
    if body_index is None:
        return None
    body = groups[body_index][1]
    spec_parts: list[str] = []
    for kind, value in groups[:body_index]:
        opener, closer = ("[", "]") if kind == "square" else ("{", "}")
        spec_parts.extend((opener, value, closer))
    spec = _join_emittable(spec_parts, "", budget)
    family = match.group(1)
    partial_definition = _UnsupportedMacroDefinition(
        body=body,
        argument_spec=spec,
        family=family,
    )
    argument_layout = _unsupported_invocation_argument_layout(
        partial_definition,
        budget,
    )
    return (
        name_match.group(1),
        _UnsupportedMacroDefinition(
            body=body,
            argument_spec=spec,
            family=family,
            argument_layout=argument_layout,
        ),
        i,
    )


def _consume_unsupported_definition_envelope(
    text: str,
    pos: int,
    budget: _LatexEvaluationBudget,
) -> tuple[int, str]:
    """未対応定義の連続 group を消費し、body を通常 source として実行させない。"""

    i = _skip_space(text, pos)
    if i < len(text) and text[i] == "{":
        try:
            raw_name, i = _read_budgeted_braced(text, i, budget)
        except LatexParseError as error:
            if error.kind == "source_evaluation_limit":
                raise
            pass
    else:
        macro_name = _MACRO_NAME_RE.match(text, i)
        if macro_name is not None:
            i = macro_name.end()
    values: list[str] = []
    while True:
        i = _skip_space(text, i)
        has_group = i < len(text) and text[i] in "[{"
        if has_group and len(values) >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            _raise_unsupported_macro_group_limit()
        if i < len(text) and text[i] == "[":
            square = _read_budgeted_square(text, i, budget)
            if square is None:
                break
            value, i = square
            values.append(value)
            continue
        if i < len(text) and text[i] == "{":
            try:
                value, i = _read_budgeted_braced(text, i, budget)
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                break
            values.append(value)
            continue
        break
    if not values:
        line_end = text.find("\n", pos)
        i = len(text) if line_end == -1 else line_end
        budget.reserve_operation()
        budget.reserve_evaluated_text(text, pos, i)
        values.append(text[pos:i])
    return i, _join_emittable(values, "\n", budget)


def _def_uses_simple_positional_parameters(parameters: str) -> bool:
    parameters = parameters.lstrip()
    count = max(
        (int(parameter.group(1)) for parameter in _PARAMETER_RE.finditer(parameters)),
        default=0,
    )
    return parameters == "".join(f"#{number}" for number in range(1, count + 1))


@dataclass
class _LatexEvaluationState:
    """読み込まれた source を TeX の出現順で評価する最小状態。"""

    files: dict[str, str]
    macros: dict[str, _MacroDefinition] = field(default_factory=dict)
    unsupported_macros: dict[str, _UnsupportedMacroDefinition] = field(default_factory=dict)
    conditionals: dict[str, bool] = field(default_factory=dict)
    frontmatter: dict[str, list[str]] = field(
        default_factory=lambda: {name: [] for name in _MAKETITLE_FIELD_ORDER}
    )
    loaded_sources: set[str] = field(default_factory=set)
    active_sources: set[str] = field(default_factory=set)
    source_depth: int = 0
    budget: _MacroExpansionBudget = field(default_factory=lambda: _MacroExpansionBudget())
    evaluation_budget: _LatexEvaluationBudget = field(
        default_factory=lambda: _LatexEvaluationBudget.from_limits()
    )


_TEX_CONDITIONAL_PRIMITIVES = frozenset(
    {
        "if",
        "ifcase",
        "ifcat",
        "ifcsname",
        "ifdefined",
        "ifdim",
        "ifeof",
        "iffalse",
        "ifhbox",
        "ifhmode",
        "ifinner",
        "ifmmode",
        "ifnum",
        "ifodd",
        "iftrue",
        "ifvbox",
        "ifvmode",
        "ifvoid",
        "ifx",
    }
)


# Control words that close an open TeX conditional.  `\fi` is the primitive;
# `\repeat` is plain TeX's loop terminator, defined as `\let\repeat=\fi`, so it
# closes the conditional that guards a `\loop` body.
_TEX_CONDITIONAL_TERMINATORS = frozenset({"fi", "repeat"})


def _parse_newif_declaration(
    text: str,
    start: int,
    state: _LatexEvaluationState,
) -> int | None:
    token = _read_tex_control_token(text, _skip_space(text, start))
    if token is None:
        return None
    raw, end, is_word = token
    name = raw[1:] if is_word else ""
    if not name.startswith("if") or len(name) <= 2:
        return None
    state.evaluation_budget.reserve_control_token()
    state.conditionals[name] = False
    return end


def _apply_newif_setting(command: str, state: _LatexEvaluationState) -> bool:
    for suffix, enabled in (("true", True), ("false", False)):
        if not command.endswith(suffix):
            continue
        conditional = f"if{command[: -len(suffix)]}"
        if conditional in state.conditionals:
            state.conditionals[conditional] = enabled
            return True
    return False


def _read_known_conditional_branch(
    text: str,
    start: int,
    state: _LatexEvaluationState,
    *,
    enabled: bool,
) -> tuple[int, str]:
    depth = 1
    cursor = start
    else_start: int | None = None
    else_body_start: int | None = None
    while True:
        match = _search_tex_command(_CONTROL_WORD_RE, text, cursor)
        if match is None:
            raise LatexParseError(
                "unsupported_structural_macro",
                "a declared TeX conditional is missing its closing \\fi",
            )
        state.evaluation_budget.reserve_operation()
        state.evaluation_budget.reserve_control_token()
        command = match.group(0)[1:].rstrip("*")
        if command in state.conditionals or command in _TEX_CONDITIONAL_PRIMITIVES:
            depth += 1
        elif command in _TEX_CONDITIONAL_TERMINATORS:
            # `\fi` closes a conditional; plain TeX's `\loop ... \repeat`
            # ends the loop with `\let\repeat=\fi`, so `\repeat` closes the
            # conditional guarding the loop body (e.g. `acl.sty`'s
            # `\fillzeros`).  Both decrement the same nesting counter.
            depth -= 1
            if depth == 0:
                if enabled:
                    selected = text[start : else_start if else_start is not None else match.start()]
                else:
                    selected = (
                        text[else_body_start : match.start()] if else_body_start is not None else ""
                    )
                return match.end(), selected
        elif command == "else" and depth == 1 and else_start is None:
            else_start = match.start()
            else_body_start = match.end()
        cursor = match.end()


def _apply_let_assignment(
    state: _LatexEvaluationState,
    target_raw: str,
    source_raw: str,
) -> None:
    r"""Apply the subset of ``\let`` meanings represented by the evaluator."""

    target_match = _CONTROL_WORD_RE.fullmatch(target_raw)
    if target_match is None:
        return
    target = target_raw[1:]
    if target in _PRESERVED_SEMANTIC_COMMANDS:
        return
    state.macros.pop(target, None)
    state.unsupported_macros.pop(target, None)

    source_match = _CONTROL_WORD_RE.fullmatch(source_raw)
    if source_match is None:
        state.macros[target] = _MacroDefinition(0, source_raw)
        return
    source = source_raw[1:]
    if source in state.macros:
        state.macros[target] = state.macros[source]
    elif source in state.unsupported_macros:
        state.unsupported_macros[target] = state.unsupported_macros[source]
    elif source == "relax":
        state.macros[target] = _MacroDefinition(0, "")


@dataclass(frozen=True)
class _LatexLoaderSpec:
    """source command が読むファイル種別と再評価契約。"""

    suffix: str
    loaded_once: bool
    allow_unbraced: bool = False
    allow_multiple: bool = False


_LATEX_LOADER_SPECS = {
    "documentclass": _LatexLoaderSpec(".cls", loaded_once=True),
    "LoadClass": _LatexLoaderSpec(".cls", loaded_once=True),
    "LoadClassWithOptions": _LatexLoaderSpec(".cls", loaded_once=True),
    "usepackage": _LatexLoaderSpec(".sty", loaded_once=True, allow_multiple=True),
    "RequirePackage": _LatexLoaderSpec(".sty", loaded_once=True, allow_multiple=True),
    "RequirePackageWithOptions": _LatexLoaderSpec(".sty", loaded_once=True, allow_multiple=True),
    "input": _LatexLoaderSpec(".tex", loaded_once=False, allow_unbraced=True),
    "include": _LatexLoaderSpec(".tex", loaded_once=False, allow_unbraced=True),
}
_UNBRACED_SOURCE_NAME_BOUNDARIES = frozenset("\\{}[]%")
_MAX_LATEX_SOURCE_NAME_CHARS = 4_096
_MAX_LATEX_SOURCE_DEPTH = 64


def _read_latex_loader_argument(
    text: str,
    pos: int,
    *,
    allow_unbraced: bool,
    budget: _LatexEvaluationBudget,
) -> tuple[str | None, int]:
    """loader の options と braced/unbraced filename を有限に読む。"""

    i = _skip_space(text, pos)
    option_count = 0
    while i < len(text) and text[i] == "[":
        if option_count >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            budget._raise_limit()
        close_pos = _matching_square(text, i, budget)
        if close_pos is None:
            return None, i
        budget.reserve_structure_match()
        option_count += 1
        i = close_pos + 1
        i = _skip_space(text, i)
    if i < len(text) and text[i] == "{":
        return _read_budgeted_braced(text, i, budget)
    if not allow_unbraced:
        return None, i

    start = i
    while i < len(text):
        char = text[i]
        if (
            char.isspace()
            or char in _UNBRACED_SOURCE_NAME_BOUNDARIES
            or ord(char) < 0x20
            or ord(char) == 0x7F
        ):
            break
        if i - start >= _MAX_LATEX_SOURCE_NAME_CHARS:
            raise LatexParseError(
                "source_name_limit",
                "latex source filename exceeds the deterministic limit",
            )
        i += 1
    if i == start:
        return None, start
    budget.reserve_operation()
    budget.reserve_evaluated_text(text, start, i)
    return text[start:i], i


def _resolve_latex_source_name(
    current_name: str,
    requested: str,
    files: dict[str, str],
    *,
    suffix: str,
) -> str | None:
    clean = requested.strip()
    if not clean:
        return None
    names = [clean] if clean.lower().endswith(suffix) else [clean, clean + suffix]
    directory = posixpath.dirname(current_name)
    candidates: list[str] = []
    for name in names:
        if directory:
            candidates.append(posixpath.normpath(posixpath.join(directory, name)))
        candidates.append(posixpath.normpath(name))
    return next((candidate for candidate in candidates if candidate in files), None)


def _apply_macro_definition(
    state: _LatexEvaluationState,
    name: str,
    definition: _MacroDefinition,
    *,
    command: str,
) -> None:
    if name in _PRESERVED_SEMANTIC_COMMANDS:
        return
    if command == "providecommand" and (name in state.macros or name in state.unsupported_macros):
        return
    state.evaluation_budget.reserve_ir_object()
    state.macros[name] = definition
    state.unsupported_macros.pop(name, None)


def _apply_unsupported_macro_definition(
    state: _LatexEvaluationState,
    name: str,
    definition: _UnsupportedMacroDefinition,
) -> None:
    if name in _PRESERVED_SEMANTIC_COMMANDS:
        return
    if definition.family.lower().startswith("provide") and (
        name in state.macros or name in state.unsupported_macros
    ):
        return
    state.evaluation_budget.reserve_ir_object()
    state.unsupported_macros[name] = definition
    state.macros.pop(name, None)


def _assign_frontmatter_field(state: _LatexEvaluationState, name: str, argument: str) -> None:
    state.evaluation_budget.reserve_ir_object()
    if name in _MAKETITLE_SINGLETON_FIELDS:
        state.frontmatter[name] = [argument]
    else:
        state.frontmatter[name].append(argument)


_UNSUPPORTED_REQUIRED_ARGUMENT = "required"
_UNSUPPORTED_OPTIONAL_SQUARE_ARGUMENT = "optional_square"
_UNSUPPORTED_OPTIONAL_STAR_ARGUMENT = "optional_star"


def _square_argument_spec_groups(
    argument_spec: str,
    budget: _LatexEvaluationBudget | None = None,
) -> list[str] | None:
    groups: list[str] = []
    i = 0
    while True:
        i = _skip_space(argument_spec, i)
        if i >= len(argument_spec):
            return groups
        if argument_spec[i] != "[":
            return None
        if len(groups) >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            return None
        square = (
            _read_budgeted_square(argument_spec, i, budget)
            if budget is not None
            else _read_square(argument_spec, i)
        )
        if square is None:
            return None
        value, i = square
        groups.append(value)


def _xparse_argument_layout(
    argument_spec: str,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, ...] | None:
    i = _skip_space(argument_spec, 0)
    if i >= len(argument_spec) or argument_spec[i] != "{":
        return None
    try:
        spec, end = (
            _read_budgeted_braced(argument_spec, i, budget)
            if budget is not None
            else _read_braced(argument_spec, i)
        )
    except LatexParseError as error:
        if error.kind == "source_evaluation_limit":
            raise
        return None
    if _skip_space(argument_spec, end) != len(argument_spec):
        return None

    layout: list[str] = []
    token_count = 0
    i = 0
    while True:
        i = _skip_space(spec, i)
        if i >= len(spec):
            return tuple(layout)
        token_count += 1
        if token_count > _MAX_UNSUPPORTED_ARGUMENT_SPEC_TOKENS:
            _raise_unsupported_argument_spec_limit()
        if budget is not None:
            budget.reserve_operation()
        if len(layout) >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            return None
        command = spec[i]
        if command in "+!":
            i += 1
            continue
        if command == ">":
            i = _skip_space(spec, i + 1)
            if i >= len(spec) or spec[i] != "{":
                return None
            try:
                _processor, i = (
                    _read_budgeted_braced(spec, i, budget)
                    if budget is not None
                    else _read_braced(spec, i)
                )
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                return None
            continue
        if command == "m":
            layout.append(_UNSUPPORTED_REQUIRED_ARGUMENT)
            i += 1
            continue
        if command == "o":
            layout.append(_UNSUPPORTED_OPTIONAL_SQUARE_ARGUMENT)
            i += 1
            continue
        if command == "O":
            layout.append(_UNSUPPORTED_OPTIONAL_SQUARE_ARGUMENT)
            i = _skip_space(spec, i + 1)
            if i >= len(spec) or spec[i] != "{":
                return None
            try:
                _default, i = (
                    _read_budgeted_braced(spec, i, budget)
                    if budget is not None
                    else _read_braced(spec, i)
                )
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                return None
            continue
        if command == "s":
            layout.append(_UNSUPPORTED_OPTIONAL_STAR_ARGUMENT)
            i += 1
            continue
        return None


def _newcommand_argument_layout(
    argument_spec: str,
    *,
    newcommandx: bool,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, ...] | None:
    groups = _square_argument_spec_groups(argument_spec, budget)
    if groups is None:
        return None
    if not groups:
        return ()
    count_raw = groups[0].strip()
    count = _bounded_unsupported_argument_number(count_raw, allow_zero=True)
    if count is None:
        return None
    if len(groups) > 2:
        return None

    optional_indexes: set[int] = set()
    if len(groups) == 2:
        if newcommandx:
            option_count = 0
            for match in re.finditer(r"(?:^|,)\s*([1-9]\d*)\s*=", groups[1]):
                option_count += 1
                if option_count > _MAX_UNSUPPORTED_ARGUMENT_SPEC_TOKENS:
                    _raise_unsupported_argument_spec_limit()
                if budget is not None:
                    budget.reserve_structure_match()
                index = _bounded_unsupported_argument_number(match.group(1), allow_zero=False)
                if index is None or index > count:
                    return None
                optional_indexes.add(index)
            if not optional_indexes:
                return None
        elif count:
            optional_indexes = {1}
    return tuple(
        _UNSUPPORTED_OPTIONAL_SQUARE_ARGUMENT
        if index in optional_indexes
        else _UNSUPPORTED_REQUIRED_ARGUMENT
        for index in range(1, count + 1)
    )


def _bounded_unsupported_argument_number(raw: str, *, allow_zero: bool) -> int | None:
    clean = raw.strip()
    max_digits = len(str(_MAX_UNSUPPORTED_MACRO_GROUPS))
    if not re.fullmatch(r"[0-9]+", clean) or len(clean) > max_digits:
        return None
    value = int(clean)
    if value > _MAX_UNSUPPORTED_MACRO_GROUPS or (not allow_zero and value == 0):
        return None
    return value


def _unsupported_invocation_argument_layout(
    definition: _UnsupportedMacroDefinition,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, ...] | None:
    family = definition.family.lower()
    if family.endswith("documentcommand"):
        return _xparse_argument_layout(definition.argument_spec, budget)
    if family.endswith("robustcmd"):
        return _newcommand_argument_layout(
            definition.argument_spec,
            newcommandx=False,
            budget=budget,
        )
    if family.endswith("commandx"):
        return _newcommand_argument_layout(
            definition.argument_spec,
            newcommandx=True,
            budget=budget,
        )
    return None


def _consume_grouped_unsupported_arguments(
    text: str,
    pos: int,
    budget: _LatexEvaluationBudget,
) -> tuple[int, list[str]]:
    i = pos
    arguments: list[str] = []
    while True:
        group_start = _skip_space(text, i)
        has_group = group_start < len(text) and text[group_start] in "[{"
        if has_group and len(arguments) >= _MAX_UNSUPPORTED_MACRO_GROUPS:
            _raise_unsupported_macro_group_limit()
        if group_start < len(text) and text[group_start] == "[":
            square = _read_budgeted_square(text, group_start, budget)
            if square is None:
                break
            value, i = square
            arguments.append(value)
            continue
        if group_start < len(text) and text[group_start] == "{":
            try:
                value, i = _read_budgeted_braced(text, group_start, budget)
            except LatexParseError as error:
                if error.kind == "source_evaluation_limit":
                    raise
                break
            arguments.append(value)
            continue
        break
    return i, arguments


def _consume_unsupported_invocation_arguments(
    text: str,
    pos: int,
    definition: _UnsupportedMacroDefinition,
    budget: _LatexEvaluationBudget,
) -> tuple[int, str]:
    layout = definition.argument_layout
    if layout is None:
        end, fallback_arguments = _consume_grouped_unsupported_arguments(text, pos, budget)
        # 不明な引数言語では次 token を構造リスク検査にだけ使う。0 引数かもしれないため
        # ``end`` は進めず、非構造 token を invocation の一部として消費しない。
        probe, _probe_end = _read_budgeted_macro_argument(text, end, budget)
        if probe is not None:
            fallback_arguments.append(probe)
        return end, _join_emittable(fallback_arguments, "\n", budget)

    i = pos
    argument_values: list[str] = []
    for argument_kind in layout:
        start = _skip_space(text, i)
        if argument_kind == _UNSUPPORTED_OPTIONAL_SQUARE_ARGUMENT:
            if start >= len(text) or text[start] != "[":
                continue
            square = _read_budgeted_square(text, start, budget)
            if square is None:
                break
            square_value, i = square
            argument_values.append(square_value)
            continue
        if argument_kind == _UNSUPPORTED_OPTIONAL_STAR_ARGUMENT:
            if start < len(text) and text[start] == "*":
                budget.reserve_operation()
                budget.reserve_evaluated_text(text, start, start + 1)
                argument_values.append("*")
                i = start + 1
            continue
        required_value, end = _read_budgeted_macro_argument(text, i, budget)
        if required_value is None:
            break
        argument_values.append(required_value)
        i = end
    return i, _join_emittable(argument_values, "\n", budget)


def _source_has_structural_control_symbol_or_math_shift(
    source: str,
    budget: _LatexEvaluationBudget,
) -> bool:
    """リテラル外のmath delimiterを共有TeX token scannerで検出する。"""

    i = 0
    literal = _next_literal_region(source, 0)
    while i < len(source):
        while literal is not None and literal[1] <= i:
            literal = _next_literal_region(source, literal[1])
        candidate_match = _STRUCTURAL_SYMBOL_SCAN_RE.search(source, i)
        if candidate_match is None:
            return False
        candidate = candidate_match.start()
        if literal is not None and literal[0] <= candidate < literal[1]:
            i = literal[1]
            continue
        budget.reserve_operation()
        if source[candidate] == "$":
            budget.reserve_structure_match()
            return True
        control = _read_tex_control_token(source, candidate)
        if control is None:
            i = candidate + 1
            continue
        budget.reserve_control_token()
        raw, i, is_word = control
        if not is_word and raw in _STRUCTURAL_CONTROL_SYMBOLS:
            budget.reserve_structure_match()
            return True
    return False


def _source_reaches_document_structure(
    source: str,
    state: _LatexEvaluationState,
    *,
    seen: frozenset[str] = frozenset(),
    _memo: dict[str, bool] | None = None,
    _visiting: set[str] | None = None,
) -> bool:
    memo = {} if _memo is None else _memo
    visiting = set(seen) if _visiting is None else _visiting
    if _source_has_structural_control_symbol_or_math_shift(
        source,
        state.evaluation_budget,
    ):
        return True
    for match in _evaluated_matches(
        _CONTROL_WORD_RE,
        source,
        state.evaluation_budget,
    ):
        command = match.group(0)[1:].rstrip("*")
        if command in _STRUCTURAL_CONTROL_WORDS:
            return True
        if command in visiting:
            continue
        cached = memo.get(command)
        if cached is not None:
            if cached:
                return True
            continue
        if len(visiting) >= _MAX_DISPLAY_MACRO_DEPTH:
            return True
        state.evaluation_budget.reserve_operation()
        supported = state.macros.get(command)
        unsupported = state.unsupported_macros.get(command)
        if supported is None and unsupported is None:
            memo[command] = False
            continue
        visiting.add(command)
        reaches_structure = False
        if supported is not None:
            supported_sources = [supported.body]
            if supported.optional_default is not None:
                supported_sources.append(supported.optional_default)
            reaches_structure = any(
                _source_reaches_document_structure(
                    nested_source,
                    state,
                    _memo=memo,
                    _visiting=visiting,
                )
                for nested_source in supported_sources
            )
        elif unsupported is not None:
            reaches_structure = any(
                _source_reaches_document_structure(
                    nested_source,
                    state,
                    _memo=memo,
                    _visiting=visiting,
                )
                for nested_source in (unsupported.body, unsupported.argument_spec)
            )
        visiting.remove(command)
        memo[command] = reaches_structure
        if reaches_structure:
            return True
    return False


def _macro_source_reaches_command(
    source: str,
    target: str,
    state: _LatexEvaluationState,
    *,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Distinguish a definition cycle from a finite same-macro call in an argument."""

    if len(seen) >= _MAX_DISPLAY_MACRO_DEPTH:
        return True
    for match in _evaluated_matches(
        _CONTROL_WORD_RE,
        source,
        state.evaluation_budget,
    ):
        command = match.group(0)[1:].rstrip("*")
        if command == target:
            return True
        if command in seen:
            continue
        supported = state.macros.get(command)
        unsupported = state.unsupported_macros.get(command)
        if supported is None and unsupported is None:
            continue
        if supported is not None:
            nested_sources = [supported.body]
        else:
            assert unsupported is not None
            nested_sources = [unsupported.body, unsupported.argument_spec]
        if any(
            _macro_source_reaches_command(
                nested_source,
                target,
                state,
                seen=seen | {command},
            )
            for nested_source in nested_sources
        ):
            return True
    return False


_EXPANDAFTER_SPLICE_PREFIX_RE = re.compile(r"\\expandafter\s*\{\s*\Z")
_EXPANDAFTER_SPLICE_LOOKAROUND_CHARS = 32


def _is_expandafter_value_splice_reference(text: str, match: re.Match[str]) -> bool:
    r"""``\expandafter{\foo}`` は再帰呼び出しではなく、``\foo`` の直前の値を差し込む定型句である。

    ``\xdef\foo{\expandafter{\foo}...}`` ( ``\g@addto@macro`` 系ヘルパーが生成する、リストへ要素を
    蓄積していく古典的な慣用句。例えば ``\xdef\metadatalist{\expandafter{\metadatalist}\sep ...}`` )
    は、実際の TeX では新しい ``\xdef`` が確定する前に ``\foo`` を一度だけ展開してから差し込むため、
    最終的な定義に ``\foo`` 自身への参照は残らず、有限回で必ず終了する。本evaluatorには
    ``\expandafter`` の一発展開という primitive が無く、``#3`` のような引用元の control sequence を
    そのまま文字列として body へ埋め込むため、この位置に現れる ``\foo`` を見かけ上の再帰呼び出しと
    誤認してしまう。ちょうどこの構文位置( ``\expandafter{`` の直後かつ ``}`` の直前で他の内容を
    伴わない )に現れる場合に限り、不透明な値参照として扱い、構造到達判定・fail-closed の対象から
    外す。
    """

    prefix = text[max(0, match.start() - _EXPANDAFTER_SPLICE_LOOKAROUND_CHARS) : match.start()]
    if _EXPANDAFTER_SPLICE_PREFIX_RE.search(prefix) is None:
        return False
    suffix = text[match.end() : match.end() + _EXPANDAFTER_SPLICE_LOOKAROUND_CHARS]
    return suffix.lstrip(" \t\r\n").startswith("}")


def _evaluate_unsupported_macro_invocation(
    text: str,
    match: re.Match[str],
    state: _LatexEvaluationState,
) -> int:
    command = match.group(0)[1:].rstrip("*")
    definition = state.unsupported_macros[command]
    if state.budget.invocations <= 0:
        _raise_macro_expansion_limit("invocation")
    state.budget.invocations -= 1
    end, argument_source = _consume_unsupported_invocation_arguments(
        text, match.end(), definition, state.evaluation_budget
    )
    if (
        _source_reaches_document_structure(definition.body, state, seen=frozenset({command}))
        or _source_reaches_document_structure(
            definition.argument_spec, state, seen=frozenset({command})
        )
        or _source_reaches_document_structure(argument_source, state)
    ):
        raise LatexParseError(
            "unsupported_structural_macro",
            "an invoked macro with unsupported argument semantics may affect document structure",
        )
    return max(end, match.end())


def _evaluate_macro_invocation(
    text: str,
    match: re.Match[str],
    state: _LatexEvaluationState,
    *,
    source_name: str,
    emit: bool,
    macro_stack: tuple[str, ...],
    capture_frontmatter: bool,
) -> tuple[int, str]:
    command = match.group(0)[1:].rstrip("*")
    definition = state.macros[command]
    end, instantiated = _instantiate_macro_source(
        text,
        match.end(),
        definition,
        state.evaluation_budget,
    )
    end = max(end, match.end())
    if command in macro_stack and _macro_source_reaches_command(
        definition.body,
        command,
        state,
        seen=frozenset({command}),
    ):
        if _is_expandafter_value_splice_reference(text, match):
            # \g@addto@macro 型のリスト蓄積慣用句 ( \xdef\metadatalist{\expandafter{\metadatalist}
            # \sep ...} など ) は、この位置の \foo が実TeXでは前回の値の差し込みに過ぎず、決して
            # \foo 自身を再度実行しない。構造到達判定に関わらずここで安全にドロップしてよい
            # ( 実際に蓄積された内容は #3 以降の残りテキストとして引き続き評価される )。
            return end, ""
        # Class/style size commands commonly pass their own control sequence as an opaque
        # argument (for example ``\@setfontsize\footnotesize``).  Our bounded evaluator does
        # not execute the unknown helper and would otherwise mistake that operand for recursive
        # expansion.  A cycle that can reach document structure must still fail closed so a
        # recursive figure/section macro is never silently discarded.
        if _source_reaches_document_structure(
            instantiated,
            state,
            seen=frozenset((*macro_stack, command)),
        ):
            _raise_macro_expansion_limit("recursion")
        return end, ""
    if len(macro_stack) >= _MAX_DISPLAY_MACRO_DEPTH:
        _raise_macro_expansion_limit("depth")
    if state.budget.invocations <= 0:
        _raise_macro_expansion_limit("invocation")
    state.budget.invocations -= 1
    evaluated = _evaluate_latex_text(
        instantiated,
        source_name=source_name,
        state=state,
        emit=emit,
        macro_stack=(*macro_stack, command),
        capture_frontmatter=capture_frontmatter,
        evaluation_reserved=True,
    )
    growth = max(0, len(evaluated) - (end - match.start()))
    if growth > state.budget.growth_chars:
        _raise_macro_expansion_limit("output growth")
    state.budget.growth_chars -= growth
    return end, evaluated


def _evaluate_fragment_graphics(
    source: str,
    *,
    source_name: str,
    state: _LatexEvaluationState,
    macro_stack: tuple[str, ...],
) -> str:
    evaluated = _evaluate_latex_text(
        source,
        source_name=source_name,
        state=state,
        emit=True,
        macro_stack=macro_stack,
        capture_frontmatter=False,
    )
    graphics = [
        match.group(0)
        for match in _evaluated_includegraphics_matches(
            evaluated,
            state.evaluation_budget,
        )
    ]
    return _join_emittable(graphics, "\n", state.evaluation_budget)


def _evaluate_maketitle_snapshot(
    *,
    source_name: str,
    state: _LatexEvaluationState,
    macro_stack: tuple[str, ...],
) -> str:
    graphics: list[str] = []
    for name in _MAKETITLE_FIELD_ORDER:
        for argument in state.frontmatter[name]:
            rendered = _evaluate_fragment_graphics(
                argument,
                source_name=source_name,
                state=state,
                macro_stack=macro_stack,
            )
            if rendered:
                graphics.append(rendered)

    definition = state.macros.get("maketitle")
    if definition is not None:
        fake_source = r"\maketitle"
        fake_match = _CONTROL_WORD_RE.match(fake_source)
        assert fake_match is not None
        _end, rendered_layout = _evaluate_macro_invocation(
            fake_source,
            fake_match,
            state,
            source_name=source_name,
            emit=True,
            macro_stack=macro_stack,
            capture_frontmatter=False,
        )
        layout_graphics = _join_emittable(
            [
                match.group(0)
                for match in _evaluated_includegraphics_matches(
                    rendered_layout,
                    state.evaluation_budget,
                )
            ],
            "\n",
            state.evaluation_budget,
        )
        if layout_graphics:
            graphics.append(layout_graphics)
    return _join_emittable(graphics, "\n", state.evaluation_budget)


def _evaluate_loaded_source(
    requested: str,
    *,
    suffix: str,
    current_name: str,
    state: _LatexEvaluationState,
) -> None:
    resolved = _resolve_latex_source_name(current_name, requested, state.files, suffix=suffix)
    if resolved is None or resolved in state.loaded_sources:
        return
    state.loaded_sources.add(resolved)
    _evaluate_latex_file(resolved, state=state, emit=False)


def _evaluate_latex_loader(
    command: str,
    text: str,
    pos: int,
    *,
    source_name: str,
    state: _LatexEvaluationState,
    emit: bool,
) -> tuple[int, str] | None:
    """既知 loader を宣言位置で評価し、消費位置と input の出力を返す。"""

    spec = _LATEX_LOADER_SPECS.get(command)
    if spec is None:
        return None
    argument, end = _read_latex_loader_argument(
        text,
        pos,
        allow_unbraced=spec.allow_unbraced,
        budget=state.evaluation_budget,
    )
    if argument is None:
        if not spec.loaded_once:
            raise LatexParseError(
                "missing_included_source",
                "an included latex source could not be resolved",
            )
        return max(end, pos), ""

    if spec.allow_multiple:
        request_count = argument.count(",") + 1
        state.evaluation_budget.reserve_operation(request_count)
        state.evaluation_budget.reserve_ir_object(request_count)
        requests: list[str] | tuple[str, ...] = argument.split(",")
    else:
        state.evaluation_budget.reserve_operation()
        requests = (argument,)
    rendered: list[str] = []
    for requested in requests:
        if spec.loaded_once:
            _evaluate_loaded_source(
                requested,
                suffix=spec.suffix,
                current_name=source_name,
                state=state,
            )
            continue
        resolved = _resolve_latex_source_name(
            source_name,
            requested,
            state.files,
            suffix=spec.suffix,
        )
        if resolved is None:
            raise LatexParseError(
                "missing_included_source",
                "an included latex source could not be resolved",
            )
        rendered.append(_evaluate_latex_file(resolved, state=state, emit=emit))
    return end, rendered[0] if rendered else ""


def _append_evaluated_output(
    out: list[str],
    state: _LatexEvaluationState,
    text: str,
    start: int = 0,
    end: int | None = None,
) -> None:
    """reserve後にのみ評価出力のslice/list要素を生成する。"""

    stop = len(text) if end is None else end
    state.evaluation_budget.reserve_operation()
    if not state.evaluation_budget.reserve_emitted_text(text, start, stop):
        return
    out.append(text if start == 0 and stop == len(text) else text[start:stop])


def _evaluate_latex_file(
    name: str,
    *,
    state: _LatexEvaluationState,
    emit: bool,
) -> str:
    if name in state.active_sources:
        return ""
    source = state.files.get(name)
    if source is None:
        return ""
    state.evaluation_budget.reserve_operation()
    state.evaluation_budget.reserve_source_visit()
    if state.source_depth >= _MAX_LATEX_SOURCE_DEPTH:
        raise LatexParseError(
            "source_expansion_limit",
            "latex source loading exceeded the deterministic depth limit",
        )
    state.source_depth += 1
    state.active_sources.add(name)
    try:
        return _evaluate_latex_text(
            source,
            source_name=name,
            state=state,
            emit=emit,
            macro_stack=(),
            capture_frontmatter=True,
        )
    finally:
        state.active_sources.remove(name)
        state.source_depth -= 1


def _evaluate_latex_text(
    text: str,
    *,
    source_name: str,
    state: _LatexEvaluationState,
    emit: bool,
    macro_stack: tuple[str, ...],
    capture_frontmatter: bool,
    evaluation_reserved: bool = False,
) -> str:
    """1 source を左から右へ評価し、呼び出し時点で user macro を確定する。"""

    state.evaluation_budget.reserve_operation()
    if not evaluation_reserved:
        state.evaluation_budget.reserve_evaluated_text(text)
    out: list[str] = []
    i = 0
    literal = _next_literal_region(text, 0)
    while i < len(text):
        state.evaluation_budget.reserve_operation()
        while literal is not None and literal[1] <= i:
            literal = _next_literal_region(text, literal[1])
        match = _search_tex_command(_CONTROL_WORD_RE, text, i)
        if literal is not None and (match is None or literal[0] <= match.start()):
            start, end = literal
            if emit:
                _append_evaluated_output(out, state, text, i, start)
                _append_evaluated_output(out, state, text, start, end)
            i = end
            literal = _next_literal_region(text, end)
            continue
        if match is None:
            if emit:
                _append_evaluated_output(out, state, text, i)
            break
        state.evaluation_budget.reserve_control_token()
        if emit:
            _append_evaluated_output(out, state, text, i, match.start())
        command = match.group(0)[1:].rstrip("*")

        if command == "newif":
            declaration_end = _parse_newif_declaration(text, match.end(), state)
            if declaration_end is not None:
                i = declaration_end
                continue
        if _apply_newif_setting(command, state):
            i = match.end()
            continue
        if command in state.conditionals:
            end, selected = _read_known_conditional_branch(
                text,
                match.end(),
                state,
                enabled=state.conditionals[command],
            )
            evaluated = _evaluate_latex_text(
                selected,
                source_name=source_name,
                state=state,
                emit=emit,
                macro_stack=macro_stack,
                capture_frontmatter=capture_frontmatter,
                evaluation_reserved=True,
            )
            if emit:
                _append_evaluated_output(out, state, evaluated)
            i = end
            continue

        if command == "let":
            assignment = _parse_let_assignment(
                text,
                match.end(),
                state.evaluation_budget,
            )
            if assignment is not None:
                target, source, end = assignment
                _apply_let_assignment(state, target, source)
                i = end
                continue

        unsupported_definition = _UNSUPPORTED_DEFINITION_RE.match(text, match.start())
        if unsupported_definition is not None and _is_control_word_operand_of_primitive(
            text, match.start()
        ):
            # e.g. ``\ifdefined\NewDocumentCommand\else...\fi`` — the control
            # word is the primitive's token operand, not a definition site.
            if emit:
                _append_evaluated_output(out, state, match.group(0))
            i = match.end()
            continue
        if unsupported_definition is not None:
            parsed_unsupported = _parse_unsupported_macro_definition(
                text,
                unsupported_definition,
                state.evaluation_budget,
            )
            if parsed_unsupported is not None:
                name, unsupported_value, end = parsed_unsupported
                _apply_unsupported_macro_definition(state, name, unsupported_value)
                i = end
                continue
            _end, envelope = _consume_unsupported_definition_envelope(
                text,
                unsupported_definition.end(),
                state.evaluation_budget,
            )
            if _source_reaches_document_structure(envelope, state):
                raise LatexParseError(
                    "unsupported_structural_macro",
                    "an unsupported macro definition may affect document structure",
                )
            raise LatexParseError(
                "parse_error",
                "an unsupported macro definition could not be consumed safely",
            )
        new_definition = _NEWCOMMAND_RE.match(text, match.start())
        if new_definition is not None:
            parsed_new = _parse_newcommand_definition(
                text,
                new_definition,
                state.evaluation_budget,
            )
            if parsed_new is not None:
                name, macro_definition, end = parsed_new
                _apply_macro_definition(
                    state,
                    name,
                    macro_definition,
                    command=new_definition.group(1),
                )
                i = end
                continue
        def_definition = _DEF_RE.match(text, match.start())
        if def_definition is not None and _is_second_control_word_operand_of_primitive(
            text, match.start()
        ):
            if emit:
                _append_evaluated_output(out, state, match.group(0))
            i = match.end()
            continue
        if def_definition is not None:
            parsed_def = _parse_def_definition(
                text,
                def_definition,
                state.evaluation_budget,
            )
            if parsed_def is not None:
                name, macro_definition, end, parameter_spec = parsed_def
                if _def_uses_simple_positional_parameters(parameter_spec):
                    _apply_macro_definition(
                        state,
                        name,
                        macro_definition,
                        command="def",
                    )
                else:
                    _apply_unsupported_macro_definition(
                        state,
                        name,
                        _UnsupportedMacroDefinition(
                            body=macro_definition.body,
                            argument_spec=parameter_spec,
                            family="def",
                        ),
                    )
                i = end
                continue

        loader_result = _evaluate_latex_loader(
            command,
            text,
            match.end(),
            source_name=source_name,
            state=state,
            emit=emit,
        )
        if loader_result is not None:
            end, nested = loader_result
            if emit and nested:
                _append_evaluated_output(out, state, nested)
            i = max(end, match.end())
            continue
        if capture_frontmatter and command in _MAKETITLE_FIELD_ORDER:
            argument, end = _read_bounded_optional_braced(
                text,
                match.end(),
                state.evaluation_budget,
            )
            if argument is not None:
                _assign_frontmatter_field(state, command, argument)
            i = max(end, match.end())
            continue
        if command == "maketitle":
            if command in state.unsupported_macros:
                if any(
                    _source_reaches_document_structure(argument, state)
                    for arguments in state.frontmatter.values()
                    for argument in arguments
                ):
                    raise LatexParseError(
                        "unsupported_structural_macro",
                        "an invoked macro with unsupported argument semantics may affect "
                        "document structure",
                    )
                end = _evaluate_unsupported_macro_invocation(text, match, state)
                i = end
                continue
            if emit:
                rendered = _evaluate_maketitle_snapshot(
                    source_name=source_name,
                    state=state,
                    macro_stack=macro_stack,
                )
                if rendered:
                    _append_evaluated_output(out, state, "\n")
                    _append_evaluated_output(out, state, rendered)
                    _append_evaluated_output(out, state, "\n")
            i = match.end()
            continue
        # Class and style files often redefine visual switches such as ``\scriptsize``
        # through internal helpers (for example ``\@setfontsize``).  Expanding those
        # definitions cannot change document meaning, but it pollutes equation source and
        # can make downstream TeX compilation fail.  Keep these zero-argument switches
        # presentation-only even when a loaded class has supplied a macro definition.
        if command in _PRESENTATION_SWITCH_CMDS:
            i = match.end()
            continue
        if command in state.unsupported_macros:
            end = _evaluate_unsupported_macro_invocation(text, match, state)
            if emit:
                _append_evaluated_output(out, state, text, match.start(), end)
            i = end
            continue
        if command in state.macros:
            end, evaluated = _evaluate_macro_invocation(
                text,
                match,
                state,
                source_name=source_name,
                emit=emit,
                macro_stack=macro_stack,
                capture_frontmatter=capture_frontmatter,
            )
            if emit:
                _append_evaluated_output(out, state, evaluated)
            i = end
            continue
        if command in _SETUP_CMDS:
            i = _consume_setup_command(
                text,
                match.end(),
                command,
                state.evaluation_budget,
            )
            continue

        if emit:
            _append_evaluated_output(out, state, match.group(0))
        i = match.end()
    return "".join(out)


def _includegraphics_blocks(
    raw: str,
    budget: _LatexEvaluationBudget | None = None,
) -> list[Block]:
    blocks: list[Block] = []
    for match in _evaluated_includegraphics_matches(raw, budget):
        if budget is not None:
            budget.reserve_ir_object()
        blocks.append(Block(id="", type="figure", asset_key=match.group(1).strip()))
    return blocks


def _without_includegraphics(
    raw: str,
    budget: _LatexEvaluationBudget | None = None,
) -> str:
    visible_ranges: list[tuple[int, int]] = []
    found = False
    start = 0
    for match in _evaluated_includegraphics_matches(raw, budget):
        found = True
        visible_ranges.append((start, match.start()))
        start = match.end()
    if not found:
        return raw
    visible_ranges.append((start, len(raw)))
    if budget is not None:
        budget.ensure_emittable_parts(
            (raw, range_start, range_end) for range_start, range_end in visible_ranges
        )
    return "".join(raw[range_start:range_end] for range_start, range_end in visible_ranges)


def _build_bibliography_blocks(
    inner: str,
    budget: _LatexEvaluationBudget | None = None,
) -> list[Block]:
    """`thebibliography` を1-match lookaheadでreference block列へ変換する。"""

    matches = _evaluated_matches(_BIBITEM_RE, inner, budget)
    current = next(matches, None)
    blocks: list[Block] = []
    while current is not None:
        following = next(matches, None)
        display_label = _strip_markup(current.group(1) or "", budget)
        label = current.group(2).strip()
        start = current.end()
        end = following.start() if following is not None else len(inner)
        semi_raw = _collapse(inner[start:end])
        if not semi_raw:
            current = following
            continue
        structured = _structure_reference(semi_raw, budget) or {}
        if display_label:
            structured["citation_label"] = display_label
        if budget is not None:
            budget.reserve_ir_object()
        blocks.append(
            Block(
                id="",
                type="reference_entry",
                raw=_strip_markup(semi_raw, budget),
                label=label,
                structured=structured or None,
            )
        )
        current = following
    return blocks


def _extract_bibliography(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> tuple[str, str | None]:
    """`\\begin{thebibliography}...\\end{thebibliography}` を本文から取り出す。

    参考文献は LaTeX 上は現在位置(しばしば `\\appendix` 後)にそのまま出現するが、
    HTML パーサ(`ltx_bibliography` は常に独立したトップレベルセクション)と同様、
    独立した `sec-refs` セクションへ常に昇格させる(plans/05 §4.2 と同方針)。
    """
    m = _THEBIB_BEGIN_RE.search(text)
    if not m:
        return text, None
    inner, end = _read_environment(text, m.end(), "thebibliography", budget)
    if budget is not None:
        budget.ensure_emittable_parts(iter(((text, 0, m.start()), (text, end, len(text)))))
    return text[: m.start()] + text[end:], inner


_PARAGRAPH_BREAK_RE = re.compile(r"\n[^\S\n]*\n+")


def _top_level_paragraph_boundaries(text: str) -> Iterator[tuple[int, int]]:
    """Yield blank-line spans that are outside brace groups and literal regions."""

    depth = 0
    i = 0
    literal = _next_literal_region(text, 0)
    while i < len(text):
        while literal is not None and i >= literal[1]:
            literal = _next_literal_region(text, literal[1])
        if literal is not None and literal[0] <= i < literal[1]:
            i = literal[1]
            continue
        char = text[i]
        if char == "\\":
            control = _read_tex_control_token(text, i)
            i = control[1] if control is not None else i + 1
            continue
        if char == "%":
            newline = text.find("\n", i + 1)
            i = len(text) if newline < 0 else newline
            continue
        if char == "{":
            depth += 1
            i += 1
            continue
        if char == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if char == "\n" and depth == 0:
            boundary = _PARAGRAPH_BREAK_RE.match(text, i)
            if boundary is not None:
                yield boundary.start(), boundary.end()
                i = boundary.end()
                continue
        i += 1


def _top_level_equation_rows(
    text: str,
    budget: _LatexEvaluationBudget | None = None,
) -> Iterator[str]:
    """Split an outer alignment only at row separators outside nested math groups."""

    row_start = 0
    brace_depth = 0
    environments: list[str] = []
    i = 0
    while i < len(text):
        environment = _ENVIRONMENT_BOUNDARY_RE.match(text, i)
        if environment is not None:
            if budget is not None:
                budget.reserve_structure_match()
                budget.reserve_control_token()
            name = environment.group("name")
            if environment.group("kind") == "begin":
                environments.append(name)
            elif environments and environments[-1] == name:
                environments.pop()
            i = environment.end()
            continue

        char = text[i]
        if char == "\\":
            if text.startswith("\\\\", i):
                if brace_depth == 0 and not environments:
                    if budget is not None:
                        budget.reserve_structure_match()
                        budget.ensure_emittable_parts(iter(((text, row_start, i),)))
                    row = text[row_start:i].strip()
                    if row:
                        yield row
                    i += 2
                    row_start = i
                    continue
                i += 2
                continue
            control = _read_tex_control_token(text, i)
            if control is not None:
                if budget is not None:
                    budget.reserve_control_token()
                _raw, i, _is_word = control
                continue
            i += 1
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        i += 1

    if budget is not None:
        budget.ensure_emittable_parts(iter(((text, row_start, len(text)),)))
    row = text[row_start:].strip()
    if row:
        yield row


class _LatexParser:
    """1 回のパースの状態(ラベル解決・脚注・warnings)を保持する。"""

    def __init__(
        self,
        macros: dict[str, _MacroDefinition] | None = None,
        evaluation_budget: _LatexEvaluationBudget | None = None,
    ) -> None:
        self.warnings: list[str] = []
        self._fn_counter = 0
        self._fn_stack: list[list[Block]] = []
        self._label_targets: dict[str, str] = {}
        self._pending_refs: list[Inline] = []
        self._appendix = False
        self._level_counters = [0, 0, 0]
        self._theorem_counters: dict[str, int] = {}
        self._anon_counter = 0
        self._macros = macros or {}
        self._macro_stack: list[str] = []
        self._evaluation_budget = evaluation_budget
        self._parser_depth = 0

    def _reserve_ir_object(self) -> None:
        if self._evaluation_budget is not None:
            self._evaluation_budget.reserve_ir_object()

    def _new_block(self, **values: Any) -> Block:
        self._reserve_ir_object()
        return Block(**values)

    def _new_inline(self, **values: Any) -> Inline:
        self._reserve_ir_object()
        return Inline(**values)

    def _read_parser_braced(self, text: str, open_pos: int) -> tuple[str, int]:
        close_pos = _matching_brace(
            text,
            open_pos,
            self._evaluation_budget,
            base_depth=self._parser_depth,
        )
        if self._evaluation_budget is not None:
            self._evaluation_budget.reserve_structure_match()
            self._evaluation_budget.ensure_emittable_parts(iter(((text, open_pos + 1, close_pos),)))
        return text[open_pos + 1 : close_pos], close_pos + 1

    def _enter_parser_frame(self) -> None:
        self._parser_depth += 1
        if self._evaluation_budget is not None:
            self._evaluation_budget.ensure_parser_depth(self._parser_depth)

    def _leave_parser_frame(self) -> None:
        self._parser_depth -= 1

    # -- 数式番号 -----------------------------------------------------------

    def _next_number(self, level: int) -> str:
        self._level_counters[level - 1] += 1
        for i in range(level, 3):
            self._level_counters[i] = 0
        if self._appendix:
            letters = chr(ord("A") + self._level_counters[0] - 1)
            rest = [str(c) for c in self._level_counters[1:level]]
            return ".".join([letters, *rest])
        return ".".join(str(c) for c in self._level_counters[:level])

    # -- トップレベル(文書木構築) --------------------------------------------

    def resolve_pending_refs(self) -> None:
        """2 パス目: `\\ref`/`\\eqref` の kind を label→kind map から確定する(plans/05 §4.3)。

        未解決は `section` へ縮退+warn(HTML パーサの未知パターン縮退と同方針)。
        """
        for il in self._pending_refs:
            if il.ref and il.ref in self._label_targets:
                il.kind = self._label_targets[il.ref]
            else:
                if il.kind is None:
                    il.kind = "section"
                self.warnings.append(f"未解決の相互参照を section に縮退: \\ref{{{il.ref}}}")

    def parse_top_level(self, body: str) -> list[Section]:
        nodes = _iter_top_level(body, self._evaluation_budget)
        sections: list[Section] = []
        pending: list[Block] = []
        order = 0
        self._fn_stack.append([])
        i = 0
        while i < len(nodes):
            node = nodes[i]
            if node[0] == "appendix":
                self._appendix = True
                self._level_counters = [0, 0, 0]
                i += 1
                continue
            if node[0] == "section" and node[1] == 1:
                if pending or self._fn_stack[-1]:
                    sections.append(self._make_intro_section(order, pending, self._fn_stack.pop()))
                    self._fn_stack.append([])
                    order += 1
                    pending = []
                sec, i = self._make_section(nodes, i)
                sections.append(sec)
                order += 1
                continue
            if node[0] == "section":  # レベル 2/3 が最上位に出現(異常系)→ 平坦化して受理
                sec, i = self._make_section(nodes, i)
                sections.append(sec)
                order += 1
                continue
            pending.extend(self._blocks_for_node(node))
            i += 1
        fns = self._fn_stack.pop()
        if pending or fns:
            sections.append(self._make_intro_section(order, pending, fns))
        return sections

    def _make_intro_section(self, order: int, blocks: list[Block], fns: list[Block]) -> Section:
        self._reserve_ir_object()
        sec = Section(id=f"sec-s{order}", heading=SectionHeading())
        sec.blocks.extend(blocks)
        sec.blocks.extend(fns)
        return sec

    def _make_section(self, nodes: list[tuple[Any, ...]], idx: int) -> tuple[Section, int]:
        _, level, title_raw, label, starred = nodes[idx]
        if starred:
            self._anon_counter += 1
            number = ""
            path = f"s{self._anon_counter}"
        else:
            number = self._next_number(level)
            path = number.replace(".", "-")
        title = _flatten_plain(
            self._parse_inline(_without_includegraphics(title_raw, self._evaluation_budget))
        )
        self._reserve_ir_object()
        sec = Section(id=f"sec-{path}", heading=SectionHeading(number=number, title=title))
        sec.blocks.append(
            self._new_block(
                id="",
                type="heading",
                level=level,
                number=number or None,
                title=title or None,
                label=label,
            )
        )
        sec.blocks.extend(_includegraphics_blocks(title_raw, self._evaluation_budget))
        if label:
            self._label_targets[label] = "section"
        self._fn_stack.append([])
        i = idx + 1
        while i < len(nodes):
            nx = nodes[i]
            if nx[0] == "appendix":
                self._appendix = True
                self._level_counters = [0, 0, 0]
                i += 1
                continue
            if nx[0] == "section" and nx[1] <= level:
                break
            if nx[0] == "section":  # 子セクション(レベルが深い)
                child, i = self._make_section(nodes, i)
                sec.sections.append(child)
                continue
            sec.blocks.extend(self._blocks_for_node(nx))
            i += 1
        sec.blocks.extend(self._fn_stack.pop())
        return sec, i

    def _blocks_for_node(self, node: tuple[Any, ...]) -> list[Block]:
        if node[0] == "text":
            return self._paragraphs(node[1])
        if node[0] == "env":
            return self._env_block(node[1], node[2])
        return []

    def _flatten_env(self, inner: str) -> list[Block]:
        """透過コンテナ(center 等)の内容を再帰的にブロック化する。"""

        self._enter_parser_frame()
        try:
            return self._flatten_env_inner(inner)
        finally:
            self._leave_parser_frame()

    def _flatten_env_inner(self, inner: str) -> list[Block]:
        out: list[Block] = []
        for node in _iter_top_level(inner, self._evaluation_budget):
            if node[0] in ("section", "appendix"):
                continue
            out.extend(self._blocks_for_node(node))
        return out

    # -- ブロック種別ディスパッチ ---------------------------------------------

    def _env_block(self, name: str, inner: str) -> list[Block]:
        base = name.rstrip("*")
        if base in ("equation", "align", "gather", "multline", "eqnarray"):
            return [
                *self._equation_env(
                    _without_includegraphics(inner, self._evaluation_budget),
                    grouped=base != "equation",
                ),
                *_includegraphics_blocks(inner, self._evaluation_budget),
            ]
        if base in ("figure", "wrapfigure"):
            return self._figure_env(inner)
        if base in ("table", "wraptable"):
            table = self._table_env(inner)
            graphics = _includegraphics_blocks(inner, self._evaluation_budget)
            if table.raw is None and graphics:
                # Some papers ship an entire table as PDF/PNG inside a table
                # environment.  Keep those assets attached to table blocks so
                # the viewer renders the image with its table caption/label.
                table.asset_key = graphics[0].asset_key
                for graphic in graphics[1:]:
                    graphic.type = "table"
                return [table, *graphics[1:]]
            return [table, *graphics]
        if base in ("itemize", "enumerate"):
            return [
                self._list_env(
                    _without_includegraphics(inner, self._evaluation_budget),
                    ordered=base == "enumerate",
                ),
                *_includegraphics_blocks(inner, self._evaluation_budget),
            ]
        if base in ("quote", "quotation"):
            return [
                self._new_block(
                    id="",
                    type="quote",
                    inlines=self._parse_inline(
                        _without_includegraphics(inner, self._evaluation_budget)
                    ),
                ),
                *_includegraphics_blocks(inner, self._evaluation_budget),
            ]
        if base in _THEOREM_ENVS:
            return [
                self._theorem_env(
                    base,
                    _without_includegraphics(inner, self._evaluation_budget),
                ),
                *_includegraphics_blocks(inner, self._evaluation_budget),
            ]
        if base in ("algorithm", "algorithmic"):
            return [
                self._algorithm_env(_without_includegraphics(inner, self._evaluation_budget)),
                *_includegraphics_blocks(inner, self._evaluation_budget),
            ]
        if base in ("verbatim", "lstlisting", "minted"):
            return [self._new_block(id="", type="code", code=inner.strip("\n"), language=None)]
        if base == "thebibliography":
            return [
                *_build_bibliography_blocks(
                    _without_includegraphics(inner, self._evaluation_budget),
                    self._evaluation_budget,
                ),
                *_includegraphics_blocks(inner, self._evaluation_budget),
            ]
        if base == "abstract":
            # Abstract prose is stored on papers, but display assets are still evaluated
            # declarations and must pass the same completeness gate as body figures.
            return _includegraphics_blocks(inner, self._evaluation_budget)
        if base == "tcolorbox":
            options, _arguments, content = _environment_prefix(
                inner,
                budget=self._evaluation_budget,
                base_depth=self._parser_depth,
            )
            blocks = self._flatten_env(content)
            title = _environment_option(
                options,
                "title",
                self._evaluation_budget,
                base_depth=self._parser_depth,
            )
            if title:
                title_text = _flatten_plain(self._parse_inline(title))
                if title_text:
                    blocks.insert(
                        0,
                        self._new_block(
                            id="",
                            type="paragraph",
                            inlines=[self._new_inline(t="emphasis", v=title_text)],
                        ),
                    )
            return blocks
        if base in ("center", "flushleft", "flushright", "minipage", "small", "footnotesize"):
            required_args = 1 if base == "minipage" else 0
            _options, _arguments, content = _environment_prefix(
                inner,
                required_args=required_args,
                budget=self._evaluation_budget,
                base_depth=self._parser_depth,
            )
            return self._flatten_env(content)
        if base == "multicols":
            _options, _arguments, content = _environment_prefix(
                inner,
                required_args=1,
                budget=self._evaluation_budget,
                base_depth=self._parser_depth,
            )
            return self._flatten_env(content)
        # 未知 env は設定オプションを表示しない。段落境界はソース対応(PDF 差し替え)のため
        # 維持し、入れ子の begin/end はインライン側で不可視化する。
        _options, _arguments, content = _environment_prefix(
            inner,
            budget=self._evaluation_budget,
            base_depth=self._parser_depth,
        )
        return self._paragraphs(content)

    def _paragraphs(self, raw: str) -> list[Block]:
        out: list[Block] = []
        start = 0
        found = False
        for match in _evaluated_includegraphics_matches(
            raw,
            self._evaluation_budget,
        ):
            found = True
            out.extend(self._plain_paragraphs(raw[start : match.start()]))
            out.append(self._new_block(id="", type="figure", asset_key=match.group(1).strip()))
            start = match.end()
        if not found:
            return self._plain_paragraphs(raw)
        out.extend(self._plain_paragraphs(raw[start:]))
        return out

    def _plain_paragraphs(self, raw: str) -> list[Block]:
        out: list[Block] = []
        start = 0
        for boundary_start, boundary_end in _top_level_paragraph_boundaries(raw):
            if self._evaluation_budget is not None:
                self._evaluation_budget.reserve_structure_match()
            chunk = raw[start:boundary_start]
            start = boundary_end
            if not chunk.strip():
                continue
            inl = self._parse_inline(chunk)
            if inl:
                out.append(self._new_block(id="", type="paragraph", inlines=inl))
        chunk = raw[start:]
        if chunk.strip():
            inl = self._parse_inline(chunk)
            if inl:
                out.append(self._new_block(id="", type="paragraph", inlines=inl))
        return out

    def _equation_env(self, inner: str, *, grouped: bool) -> list[Block]:
        label: str | None = None
        text_parts: list[str] = []
        copied_until = 0
        for match in re.finditer(r"\\label\{([^}]*)\}", inner):
            if self._evaluation_budget is not None:
                self._evaluation_budget.reserve_structure_match()
                self._evaluation_budget.reserve_control_token()
            if label is None:
                label = match.group(1).strip()
            text_parts.append(inner[copied_until : match.start()])
            copied_until = match.end()
        if text_parts:
            text_parts.append(inner[copied_until:])
            if self._evaluation_budget is not None:
                self._evaluation_budget.ensure_emittable_parts(_iter_join_ranges(text_parts, ""))
            text = "".join(text_parts).strip()
        else:
            text = inner.strip()
        if not grouped:
            blk = self._new_block(id="", type="equation", latex=text, label=label)
            if label:
                self._label_targets[label] = "equation"
            return [blk]
        blocks: list[Block] = []
        for row in _top_level_equation_rows(text, self._evaluation_budget):
            blocks.append(self._new_block(id="", type="equation", latex=row))
        if label and blocks:
            blocks[0].label = label
            self._label_targets[label] = "equation"
        return blocks

    def _figure_env(self, inner: str) -> list[Block]:
        label = None
        m_label = re.search(r"\\label\{([^}]*)\}", inner)
        if m_label:
            label = m_label.group(1).strip()
        caption_inlines: list[Inline] = []
        m_cap = re.search(r"\\caption\{", inner)
        if m_cap:
            raw_caption, _end = self._read_parser_braced(inner, m_cap.end() - 1)
            caption_inlines = self._parse_inline(
                _without_includegraphics(raw_caption, self._evaluation_budget)
            )
        blocks: list[Block] = []
        for match in _evaluated_includegraphics_matches(
            inner,
            self._evaluation_budget,
        ):
            first_asset = not blocks
            blocks.append(
                self._new_block(
                    id="",
                    type="figure",
                    asset_key=match.group(1).strip(),
                    caption=caption_inlines if first_asset else [],
                    label=label if first_asset else None,
                )
            )
        if not blocks:
            blocks.append(
                self._new_block(
                    id="",
                    type="figure",
                    asset_key=None,
                    caption=caption_inlines,
                    label=label,
                )
            )
        if label:
            self._label_targets[label] = "figure"
        return blocks

    def _table_env(self, inner: str) -> Block:
        display_inner = _without_includegraphics(inner, self._evaluation_budget)
        label = None
        m_label = re.search(r"\\label\{([^}]*)\}", display_inner)
        if m_label:
            label = m_label.group(1).strip()
        raw = None
        m_tab = re.search(r"\\begin\{(tabular[xX*]?)\}", display_inner)
        if m_tab:
            try:
                _inner, end = _read_environment(
                    display_inner,
                    m_tab.end(),
                    m_tab.group(1),
                    self._evaluation_budget,
                )
                raw = _without_includegraphics(
                    self._expand_macros_in_raw(display_inner[m_tab.start() : end]),
                    self._evaluation_budget,
                )
            except LatexParseError:
                raw = None
        if raw is None:
            raw = self._tabularray_table_raw(display_inner)
        caption_inlines: list[Inline] = []
        m_cap = re.search(r"\\caption\{", display_inner)
        if m_cap:
            raw_caption, _end = self._read_parser_braced(
                display_inner,
                m_cap.end() - 1,
            )
            caption_inlines = self._parse_inline(raw_caption)
        blk = self._new_block(id="", type="table", raw=raw, caption=caption_inlines, label=label)
        if label:
            self._label_targets[label] = "table"
        return blk

    def _tabularray_table_raw(self, display_inner: str) -> str | None:
        """tabularray (`tblr`/`longtblr`/`talltblr`) を classic `tabular` と同じ grid 表現へ落とす。

        タイトル/hline 等の見た目オプション ``{...}`` は入れ子波括弧
        (``column{1}={bg=white}`` 等)を含むため正規表現では切り出せない。
        既存の balanced-brace ヘルパで安全にスキップし、行/セル本体だけを
        classic tabular の raw 文字列へ包み直す(列指定の内容自体は下流の
        grid 抽出で使われないため空でよい)。壊れている・未対応の場合は
        raw=None のまま caption-only に留め、パース全体は継続する。
        """

        m_tblr = re.search(r"\\begin\{(tblr|longtblr|talltblr)\}", display_inner)
        if not m_tblr:
            return None
        try:
            env_inner, _end = _read_environment(
                display_inner,
                m_tblr.end(),
                m_tblr.group(1),
                self._evaluation_budget,
            )
            body = self._skip_tblr_options(env_inner)
            body = self._strip_tblr_cell_prefixes(body)
            synthetic = "\\begin{tabular}{}" + body + "\\end{tabular}"
            return _without_includegraphics(
                self._expand_macros_in_raw(synthetic),
                self._evaluation_budget,
            )
        except LatexParseError:
            return None

    def _skip_tblr_options(self, env_inner: str) -> str:
        """`\\begin{tblr}` 直後の任意 `[outer-spec]` と必須 `{inner-spec}` を読み飛ばす。"""

        pos = _skip_space(env_inner, 0)
        if pos < len(env_inner) and env_inner[pos] == "[":
            close_pos = _matching_square(
                env_inner,
                pos,
                self._evaluation_budget,
                base_depth=self._parser_depth,
            )
            if close_pos is None:
                raise LatexParseError("unbalanced_braces", "tblr の outer spec が不正")
            if self._evaluation_budget is not None:
                self._evaluation_budget.reserve_structure_match()
            pos = _skip_space(env_inner, close_pos + 1)
        if pos >= len(env_inner) or env_inner[pos] != "{":
            raise LatexParseError("unbalanced_braces", "tblr の inner spec が見つからない")
        _options, body_start = self._read_parser_braced(env_inner, pos)
        return env_inner[body_start:]

    def _tblr_cell_boundaries(self, body: str) -> list[int]:
        """トップレベルの `&` / `\\\\` 直後をセル先頭位置として返す(SetCell 検出専用の簡易走査)。

        最終的な行/セル分割は下流(table_cells)の厳密な実装に委ねるため、
        ここでは `\\SetCell` 等の先頭コマンドを見つけるための目安の境界で十分。
        """

        starts = [0]
        depth = 0
        i = 0
        n = len(body)
        while i < n:
            ch = body[i]
            if ch == "\\" and i + 1 < n:
                if body[i + 1] == "\\":
                    i += 2
                    starts.append(i)
                    continue
                i += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
            elif ch == "&" and depth == 0:
                starts.append(i + 1)
            i += 1
        return starts

    def _strip_tblr_cell_prefixes(self, body: str) -> str:
        """セル/行先頭の `\\SetCell{...}` `\\SetCell[...]{...}` `\\SetRow{...}` を除去する。

        これらの書式コマンドはグリッド抽出に不要なだけでなく、汎用マクロ除去
        (未知マクロは名前だけ捨てて `{...}` の中身を残す)に巻き込まれると
        `bg=red` のような残骸がセル本文の先頭へ混入してしまう。境界が明確な
        単純形だけを対象とし、判定できない場合は本文を失わないよう何もしない。
        """

        removals: list[tuple[int, int]] = []
        for pos in sorted(set(self._tblr_cell_boundaries(body))):
            j = _skip_space(body, pos)
            match = _TBLR_CELL_PREFIX_RE.match(body, j)
            if match is None:
                continue
            k = _skip_space(body, match.end())
            try:
                if k < len(body) and body[k] == "[":
                    close_pos = _matching_square(
                        body,
                        k,
                        self._evaluation_budget,
                        base_depth=self._parser_depth,
                    )
                    if close_pos is None:
                        continue
                    k = _skip_space(body, close_pos + 1)
                if k >= len(body) or body[k] != "{":
                    continue
                _content, close_brace = self._read_parser_braced(body, k)
            except LatexParseError:
                continue
            if self._evaluation_budget is not None:
                self._evaluation_budget.reserve_structure_match()
            removals.append((pos, close_brace))
        if not removals:
            return body
        out: list[str] = []
        cursor = 0
        for start, end in removals:
            out.append(body[cursor:start])
            cursor = end
        out.append(body[cursor:])
        if self._evaluation_budget is not None:
            self._evaluation_budget.ensure_emittable_parts(_iter_join_ranges(out, ""))
        return "".join(out)

    def _list_env(self, inner: str, *, ordered: bool) -> Block:
        items: list[list[Inline]] = []
        item_start: int | None = None
        for match in re.finditer(r"\\item\b\s*(?:\[[^\]]*\])?", inner):
            if self._evaluation_budget is not None:
                self._evaluation_budget.reserve_structure_match()
            if item_start is not None:
                inl = self._parse_inline(inner[item_start : match.start()])
                if inl:
                    items.append(inl)
            item_start = match.end()
        if item_start is not None:
            inl = self._parse_inline(inner[item_start:])
            if inl:
                items.append(inl)
        return self._new_block(id="", type="list", ordered=ordered, items=items)

    def _theorem_env(self, base: str, inner: str) -> Block:
        label = None
        text = inner
        m_label = re.search(r"\\label\{([^}]*)\}", inner)
        if m_label:
            label = m_label.group(1).strip()
            text = text[: m_label.start()] + text[m_label.end() :]
        display = _THEOREM_ENVS.get(base, base.capitalize())
        if base == "proof":
            title = display
        else:
            self._theorem_counters[base] = self._theorem_counters.get(base, 0) + 1
            title = f"{display} {self._theorem_counters[base]}"
        blk = self._new_block(
            id="", type="theorem", title=title, label=label, inlines=self._parse_inline(text)
        )
        if label:
            self._label_targets[label] = "theorem"
        return blk

    def _algorithm_env(self, inner: str) -> Block:
        label = None
        text = inner
        m_label = re.search(r"\\label\{([^}]*)\}", inner)
        if m_label:
            label = m_label.group(1).strip()
            text = text[: m_label.start()] + text[m_label.end() :]
        caption_inlines: list[Inline] = []
        m_cap = re.search(r"\\caption\{", text)
        if m_cap:
            raw_caption, end = self._read_parser_braced(text, m_cap.end() - 1)
            caption_inlines = self._parse_inline(raw_caption)
            text = text[: m_cap.start()] + text[end:]
        body_inlines = self._parse_inline(text)
        blk = self._new_block(
            id="",
            type="algorithm",
            inlines=body_inlines,
            caption=caption_inlines,
            label=label,
        )
        if label:
            self._label_targets[label] = "algorithm"
        return blk

    # -- インライン -----------------------------------------------------------

    def _parse_inline(self, text: str) -> list[Inline]:
        self._enter_parser_frame()
        try:
            return self._parse_inline_inner(text)
        finally:
            self._leave_parser_frame()

    def _parse_inline_inner(self, text: str) -> list[Inline]:
        out: list[Inline] = []
        i = 0
        n = len(text)
        while i < n:
            m = _SPECIAL_RE.search(text, i)
            if m is None:
                _append_text(out, text[i:], self._evaluation_budget)
                break
            if self._evaluation_budget is not None:
                self._evaluation_budget.reserve_structure_match()
            if m.start() > i:
                _append_text(out, text[i : m.start()], self._evaluation_budget)
            tok = m.group(0)
            if tok == "$":
                double_dollar = text.startswith("$$", m.start())
                content_start = m.end() + 1 if double_dollar else m.end()
                delimiter = "$$" if double_dollar else "$"
                end = text.find(delimiter, content_start)
                if end == -1:
                    _append_text(out, text[m.start() :], self._evaluation_budget)
                    i = n
                    continue
                value = text[content_start:end].strip()
                if value:
                    out.append(self._new_inline(t="math_inline", v=value))
                i = end + len(delimiter)
                continue
            if tok == "\\(":
                end = text.find("\\)", m.end())
                if end == -1:
                    _append_text(out, text[m.start() :], self._evaluation_budget)
                    i = n
                    continue
                value = text[m.end() : end].strip()
                if value:
                    out.append(self._new_inline(t="math_inline", v=value))
                i = end + 2
                continue
            if tok == "\\[":
                end = text.find("\\]", m.end())
                if end == -1:
                    _append_text(out, text[m.start() :], self._evaluation_budget)
                    i = n
                    continue
                value = text[m.end() : end].strip()
                if value:
                    out.append(self._new_inline(t="math_inline", v=value))
                i = end + 2
                continue
            if tok == "\\)":
                i = m.end()
                continue
            if tok == "\\]":
                i = m.end()
                continue
            if tok == "~":
                _append_text(out, " ", self._evaluation_budget)
                i = m.end()
                continue
            if tok == "\\\\":
                _append_text(out, " ", self._evaluation_budget)
                i = m.end()
                option_pos = _skip_space(text, i)
                if option_pos < n and text[option_pos] == "[":
                    close_pos = _matching_square(
                        text,
                        option_pos,
                        self._evaluation_budget,
                        base_depth=self._parser_depth,
                    )
                    if close_pos is not None:
                        if self._evaluation_budget is not None:
                            self._evaluation_budget.reserve_structure_match()
                        i = close_pos + 1
                continue
            if re.match(r"\\\s", tok):
                _append_text(out, " ", self._evaluation_budget)
                i = m.end()
                continue
            if tok == "{":
                try:
                    grouped, i = self._read_parser_braced(text, m.start())
                except LatexParseError as error:
                    if error.kind == "source_evaluation_limit":
                        raise
                    i = m.end()
                    continue
                out.extend(self._parse_inline(grouped))
                continue
            if tok == "}":
                i = m.end()
                continue
            if len(tok) == 2 and tok[0] == "\\" and tok[1] in "%&_#{}$~^":
                _append_text(out, tok[1], self._evaluation_budget)
                i = m.end()
                continue
            cmd = tok[1:].rstrip("*")
            i, produced = self._dispatch_command(text, m.end(), cmd)
            out.extend(produced)
        return _merge_text(out)

    @staticmethod
    def _read_macro_argument(text: str, pos: int) -> tuple[str | None, int]:
        """TeX の必須引数を 1 個読む。通常の波括弧に加え単一トークンにも縮退対応する。"""

        return _read_macro_argument(text, pos)

    def _expand_macro(self, text: str, pos: int, cmd: str) -> tuple[int, list[Inline]]:
        definition = self._macros[cmd]
        if cmd in self._macro_stack or len(self._macro_stack) >= 32:
            return pos, []
        budget = self._evaluation_budget or _LatexEvaluationBudget.from_limits()
        i, expanded = _instantiate_macro_source(text, pos, definition, budget)

        self._macro_stack.append(cmd)
        try:
            return i, self._parse_inline(expanded)
        finally:
            self._macro_stack.pop()

    def _expand_macros_in_raw(self, text: str) -> str:
        """表の構造 LaTeX は保ちつつ、文書固有の表示語だけを平文へ展開する。"""

        visible_wrappers = {
            "code",
            "emph",
            "fbox",
            "framebox",
            "makebox",
            "mbox",
            "sout",
            "textbf",
            "textit",
            "textrm",
            "textsc",
            "textsf",
            "texttt",
            "textnormal",
            "uline",
            "underline",
        }
        out: list[str] = []
        i = 0
        while True:
            match = _search_tex_command(_CONTROL_WORD_RE, text, i)
            if match is None:
                out.append(text[i:])
                break
            if self._evaluation_budget is not None:
                self._evaluation_budget.reserve_structure_match()
                self._evaluation_budget.reserve_control_token()
            out.append(text[i : match.start()])
            cmd = match.group(0)[1:].rstrip("*")
            if cmd in visible_wrappers:
                argument, end = _read_bounded_optional_braced(
                    text,
                    match.end(),
                    self._evaluation_budget,
                    base_depth=self._parser_depth,
                )
                if argument is not None:
                    out.append(_flatten_plain(self._parse_inline(argument)))
                    i = end
                    continue
            if cmd not in self._macros:
                out.append(match.group(0))
                i = match.end()
                continue
            end, inlines = self._expand_macro(text, match.end(), cmd)
            for inline in inlines:
                if inline.t in ("text", "emphasis", "code_inline"):
                    out.append(inline.v)
                elif inline.t == "math_inline":
                    out.append(f"${inline.v}$")
                elif inline.t == "url":
                    out.append(inline.v or inline.href or "")
            i = max(end, match.end())
        if self._evaluation_budget is not None:
            self._evaluation_budget.ensure_emittable_parts(_iter_join_ranges(out, ""))
        return "".join(out)

    def _consume_braced_arguments(self, text: str, pos: int, count: int) -> int:
        i = pos
        for _ in range(count):
            _arg, end = _read_bounded_optional_braced(
                text,
                i,
                self._evaluation_budget,
                base_depth=self._parser_depth,
            )
            if _arg is None:
                return i
            i = end
        return i

    def _consume_dimension(self, text: str, pos: int) -> int:
        i = _skip_space(text, pos)
        if i < len(text) and text[i] == "{":
            try:
                _value, return_pos = _read_bounded_optional_braced(
                    text,
                    i,
                    self._evaluation_budget,
                    base_depth=self._parser_depth,
                )
                return return_pos
            except LatexParseError:
                return pos
        match = _DIMENSION_RE.match(text, i)
        return match.end() if match is not None else pos

    def _dispatch_command(self, text: str, pos: int, cmd: str) -> tuple[int, list[Inline]]:
        def read_optional(position: int) -> tuple[str | None, int]:
            return _read_bounded_optional_braced(
                text,
                position,
                self._evaluation_budget,
                base_depth=self._parser_depth,
            )

        if cmd == "includegraphics":
            # ブロック抽出境界を外れた場合も、ファイル名を可視テキストへ落とさない。
            _asset, end = read_optional(pos)
            return max(pos, end), []
        if cmd in _SYMBOL_CMDS:
            arg, end = read_optional(pos)
            return (end if arg == "" else pos), [self._new_inline(t="text", v=_SYMBOL_CMDS[cmd])]
        if cmd in _CITE_CMDS:
            arg, end = read_optional(pos)
            citations: list[Inline] = []
            for key in _iter_comma_separated(arg or "", self._evaluation_budget):
                clean = key.strip()
                if clean:
                    citations.append(self._new_inline(t="citation", ref=clean))
            return end, citations
        if cmd in _REF_CMDS:
            arg, end = read_optional(pos)
            label = (arg or "").strip()
            kind_hint = "equation" if cmd == "eqref" else None
            il = self._new_inline(t="ref", ref=label, kind=kind_hint)
            self._pending_refs.append(il)
            return end, [il]
        if cmd == "footnote":
            arg, end = read_optional(pos)
            self._fn_counter += 1
            fn_no = self._fn_counter
            fn_block = self._new_block(
                id="",
                type="footnote",
                label=f"footnote{fn_no}",
                inlines=self._parse_inline(arg or ""),
            )
            if self._fn_stack:
                self._fn_stack[-1].append(fn_block)
            return end, [self._new_inline(t="footnote_ref", ref=f"footnote{fn_no}")]
        if cmd == "url":
            arg, end = read_optional(pos)
            href = (arg or "").strip()
            return end, [self._new_inline(t="url", v=href, href=href)]
        if cmd == "href":
            arg1, mid = read_optional(pos)
            arg2, end = read_optional(mid)
            href = (arg1 or "").strip()
            label_txt = _flatten_plain(self._parse_inline(arg2 or "")) or href
            return end, [self._new_inline(t="url", v=label_txt, href=href)]
        if cmd in ("emph", "textit", "textsc", "textbf"):
            arg, end = read_optional(pos)
            children = self._parse_inline(arg or "")
            if all(child.t == "text" for child in children):
                txt = _flatten_plain(children)
                return end, ([self._new_inline(t="emphasis", v=txt)] if txt else [])
            # IR の emphasis は v 形なので、数式・引用等は同じ列へ戻して構造を保つ。
            # テキスト片だけを emphasis にすることで ``$...$`` が表示文字へ漏れない。
            produced = [
                self._new_inline(t="emphasis", v=child.v) if child.t == "text" else child
                for child in children
                if child.t != "text" or child.v
            ]
            return end, produced
        if cmd == "verb":
            delimited = _read_inline_verb_argument(text, pos)
            if delimited is not None:
                value, end = delimited
                return end, [self._new_inline(t="code_inline", v=value)]
            arg, end = read_optional(pos)
            return end, ([self._new_inline(t="code_inline", v=arg)] if arg else [])
        if cmd in ("texttt", "code"):
            arg, end = read_optional(pos)
            return end, ([self._new_inline(t="code_inline", v=arg)] if arg else [])
        if cmd in (
            "underline",
            "uline",
            "sout",
            "textrm",
            "textsf",
            "textnormal",
            "mbox",
            "makebox",
            "fbox",
            "framebox",
            "ensuremath",
            "operatorname",
            "mathrm",
            "mathbf",
            "mathit",
            "text",
        ):
            arg, end = read_optional(pos)
            return end, self._parse_inline(arg or "")
        if cmd in ("textcolor", "colorbox", "foreignlanguage", "rotatebox"):
            _setting, mid = read_optional(pos)
            visible, end = read_optional(mid)
            return end, self._parse_inline(visible or "")
        if cmd == "fcolorbox":
            _frame, mid = read_optional(pos)
            _background, mid = read_optional(mid)
            visible, end = read_optional(mid)
            return end, self._parse_inline(visible or "")
        if cmd in self._macros:
            return self._expand_macro(text, pos, cmd)
        if cmd in _NO_OUTPUT_CMDS:
            if cmd == "label":
                _arg, end = read_optional(pos)
                return end, []
            return pos, []
        if cmd in _DISCARD_ARGUMENT_CMDS:
            return self._consume_braced_arguments(text, pos, _DISCARD_ARGUMENT_CMDS[cmd]), []
        if cmd in _DISCARD_DIMENSION_CMDS:
            return self._consume_dimension(text, pos), []
        if cmd in _SETUP_CMDS:
            return _consume_setup_command(
                text,
                pos,
                cmd,
                self._evaluation_budget,
            ), []
        if cmd in _SPACE_CMDS:
            return pos, [self._new_inline(t="text", v=" ")]
        # 未知コマンド: 設定値らしき先行引数を見せず、最後の可視引数だけ透過する。
        args: list[str] = []
        end = pos
        while True:
            try:
                arg, next_end = read_optional(end)
            except LatexParseError as exc:
                if exc.kind != "unbalanced_braces":
                    raise
                # Environment/table tokenization can hand the inline layer a bounded fragment
                # ending inside an unknown presentation macro (multirow/rotatebox etc.).  The
                # missing suffix is outside this fragment, so discard the incomplete command
                # rather than rejecting the complete paper.
                return len(text), []
            if arg is None:
                break
            if len(args) >= _MAX_UNSUPPORTED_MACRO_GROUPS:
                if self._evaluation_budget is not None:
                    self._evaluation_budget._raise_limit()
                _raise_unsupported_macro_group_limit()
            args.append(arg)
            end = next_end
        if args:
            return end, self._parse_inline(args[-1])
        return pos, []


# ============================================================================
# 公開エントリポイント
# ============================================================================


def parse_latex_source(main_name: str, files: dict[str, str]) -> ParsedDocument:
    """展開済みファイル群 + メインファイル名 → 構造化ドキュメント(plans/05 §5)。"""
    state = _LatexEvaluationState(files=files)
    expanded = _evaluate_latex_file(main_name, state=state, emit=True)
    expanded = _resolve_bibliography(
        expanded,
        files,
        budget=state.evaluation_budget,
    )
    state.evaluation_budget.ensure_final_output(expanded)
    body = _extract_document_body(expanded, state.evaluation_budget)
    body = _strip_setup_commands(body, state.evaluation_budget)
    body = _strip_frontmatter_commands(body, state.evaluation_budget)
    body, bib_inner = _extract_bibliography(body, state.evaluation_budget)

    # User macros have already been expanded against their call-site snapshot. Passing the
    # final dictionary here would let a later renewcommand reinterpret earlier output.
    parser = _LatexParser(evaluation_budget=state.evaluation_budget)
    sections = parser.parse_top_level(body)
    parser.resolve_pending_refs()

    if bib_inner is not None:
        ref_blocks = _build_bibliography_blocks(bib_inner, state.evaluation_budget)
        if ref_blocks:
            state.evaluation_budget.reserve_ir_object()
            refs_section = Section(
                id="sec-refs", heading=SectionHeading(number="", title="References")
            )
            refs_section.blocks.append(
                parser._new_block(id="", type="heading", level=1, title="References")
            )
            refs_section.blocks.extend(ref_blocks)
            sections.append(refs_section)

    assign_block_ids(sections)
    return ParsedDocument(
        quality_level="A",
        source_format="latex",
        parser_version=PARSER_VERSION,
        sections=sections,
        warnings=parser.warnings,
    )


def parse_arxiv_latex(archive: bytes) -> ParsedDocument:
    """arXiv e-print バイト列(tar.gz / 単一ファイル gzip)→ 構造化ドキュメント(plans/05 §5)。"""
    extracted = extract_latex_archive(archive)
    name, _content = select_main_tex(extracted.text_files)
    return parse_latex_source(name, extracted.text_files)
