"""PY-PARSE-02: LaTeX パーサ(M2-01。plans/05 §5・§1.3・§12.3、docs/02 §3)。

arXiv e-print(tar.gz / 単一ファイル gzip)→ 品質 A 構造化。PY-PARSE-01(HTML パーサ)と同水準の
検証(11+ ブロック型 + インライン 8 種、安定 ID、セクション木、リビジョン間 carryover)に加えて、
相互参照解決(`\\ref`/`\\eqref`/`\\cite` → ref/citation インライン)を検証する。

外部ネットワーク通信は行わない(ローカルフィクスチャのみ)。フィクスチャは Rectified Flow
構造を模した縮約版を自作した(`fixtures/latex_rectified_flow_main.tex` + `_appendix.tex` を
`latex_rectified_flow.tar.gz` に同梱)。
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import tarfile
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, Literal, SupportsIndex, overload

import pytest
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.parsing import latex_parser
from alinea_core.parsing.carryover import carry_over_ids, flatten_blocks
from alinea_core.parsing.latex_parser import (
    PARSER_VERSION,
    LatexArchive,
    LatexParseError,
    ParsedDocument,
    extract_latex_archive,
    parse_arxiv_latex,
    parse_latex_source,
    select_main_tex,
)
from alinea_core.translation.table_cells import parse_table_grid

_FIXTURES = Path(__file__).parent / "fixtures"
_TAR_GZ = _FIXTURES / "latex_rectified_flow.tar.gz"
_SINGLE_GZ = _FIXTURES / "latex_single_paper.tex.gz"
_BBL_TAR_GZ = _FIXTURES / "latex_bbl_paper.tar.gz"


def _doc() -> ParsedDocument:
    return parse_arxiv_latex(_TAR_GZ.read_bytes())


# ============================ アーカイブ展開 ============================


def test_extracts_multi_file_tar_gz() -> None:
    archive = extract_latex_archive(_TAR_GZ.read_bytes())
    assert "latex_rectified_flow_main.tex" in archive.text_files
    assert "latex_rectified_flow_appendix.tex" in archive.text_files


def test_select_main_tex_prefers_main_tex_name() -> None:
    archive = extract_latex_archive(_TAR_GZ.read_bytes())
    name, content = select_main_tex(archive.text_files)
    assert name == "latex_rectified_flow_main.tex"
    assert "\\documentclass" in content


def test_extracts_single_file_gzip_without_tar() -> None:
    archive = extract_latex_archive(_SINGLE_GZ.read_bytes())
    assert list(archive.text_files) == ["main.tex"]
    assert "\\documentclass" in archive.text_files["main.tex"]


def test_extract_keeps_raw_comments_for_later_latex_rebuild() -> None:
    source = (
        "% keep this layout comment\n\\documentclass{article}\n\\begin{document}x\\end{document}"
    )
    archive = extract_latex_archive(gzip.compress(source.encode()))

    assert "% keep this layout comment" not in archive.text_files["main.tex"]
    assert "% keep this layout comment" in archive.raw_text_files["main.tex"]


def test_parse_arxiv_latex_handles_single_file_gzip() -> None:
    doc = parse_arxiv_latex(_SINGLE_GZ.read_bytes())
    assert doc.quality_level == "A"
    assert doc.source_format == "latex"
    kinds = [b.type for b in doc.blocks]
    assert "heading" in kinds and "paragraph" in kinds
    assert doc.sections[0].heading.title == "Solo"


def test_empty_archive_raises_latex_parse_error() -> None:
    with pytest.raises(LatexParseError) as exc:
        extract_latex_archive(b"")
    assert exc.value.kind == "empty_archive"


def test_garbage_bytes_raise_latex_parse_error() -> None:
    with pytest.raises(LatexParseError):
        parse_arxiv_latex(b"not a valid latex archive at all \x00\x01\x02")


def test_no_documentclass_raises_no_main_tex() -> None:
    with pytest.raises(LatexParseError) as exc:
        parse_arxiv_latex(b"plain text without any latex markup whatsoever")
    assert exc.value.kind == "no_main_tex"


def _raw_tar_archive(
    members: list[tuple[str, bytes]],
    *,
    archive_format: int = tarfile.PAX_FORMAT,
    pax_headers: dict[str, str] | None = None,
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=archive_format) as archive:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            if pax_headers is not None:
                info.pax_headers = pax_headers
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _tar_archive(members: list[tuple[str, bytes]]) -> bytes:
    return gzip.compress(_raw_tar_archive(members))


def _compress_tar(raw: bytes, compression: str) -> bytes:
    if compression == "raw":
        return raw
    if compression == "gzip":
        return gzip.compress(raw)
    if compression == "bzip2":
        return bz2.compress(raw)
    if compression == "xz":
        return lzma.compress(raw)
    raise AssertionError(f"unsupported test compression: {compression}")


def _pax_record(key: bytes, value: bytes) -> bytes:
    body = key + b"=" + value + b"\n"
    length = len(body) + 2
    while True:
        framed = str(length).encode() + b" " + body
        if len(framed) == length:
            return framed
        length = len(framed)


def _manual_tar_entry(
    name: str,
    data: bytes,
    *,
    typeflag: bytes = tarfile.REGTYPE,
    linkname: str = "",
    declared_size: int | None = None,
) -> bytes:
    info = tarfile.TarInfo(name)
    info.type = typeflag
    info.linkname = linkname
    info.size = len(data) if declared_size is None else declared_size
    header = info.tobuf(format=tarfile.PAX_FORMAT)
    return header + data + b"\0" * (-len(data) % tarfile.BLOCKSIZE)


def _manual_tar_archive(entries: list[bytes]) -> bytes:
    raw = b"".join(entries) + b"\0" * (2 * tarfile.BLOCKSIZE)
    return raw + b"\0" * (-len(raw) % tarfile.RECORDSIZE)


def _pax_extension(key: bytes, value: bytes, *, global_header: bool = False) -> bytes:
    payload = _pax_record(key, value)
    typeflag = tarfile.XGLTYPE if global_header else tarfile.XHDTYPE
    return _manual_tar_entry("pax-header", payload, typeflag=typeflag)


def _negative_size_tar_header(
    name: str,
    size: int,
    *,
    typeflag: bytes = tarfile.REGTYPE,
) -> bytes:
    info = tarfile.TarInfo(name)
    info.type = typeflag
    info.size = size
    return info.tobuf(format=tarfile.GNU_FORMAT)


def _xz_with_lzma2_dictionary(data: bytes, property_value: int) -> bytes:
    archive = bytearray(lzma.compress(data))
    block_header_size = (archive[12] + 1) * 4
    block_header = archive[12 : 12 + block_header_size]
    assert block_header[2:4] == b"\x21\x01"
    block_header[4] = property_value
    block_header[-4:] = zlib.crc32(block_header[:-4]).to_bytes(4, "little")
    archive[12 : 12 + block_header_size] = block_header
    return bytes(archive)


def test_latex_archive_caps_member_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_MEMBERS", 2)
    archive = _tar_archive([("a.tex", b"a"), ("b.tex", b"b"), ("c.tex", b"c")])

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "archive_member_limit"


def test_latex_archive_caps_each_expanded_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_MEMBER_BYTES", 16)
    archive = _tar_archive([("main.tex", b"x" * 17)])

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "archive_member_too_large"


def test_latex_archive_caps_aggregate_expanded_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_EXPANDED_BYTES", 20)
    archive = _tar_archive([("main.tex", b"x" * 12), ("figure.bin", b"y" * 12)])

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "archive_expanded_too_large"


def test_single_gzip_latex_caps_expansion_without_gzip_decompress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"\\documentclass{article}\\begin{document}" + b"x" * 128
    compressed = gzip.compress(source)
    monkeypatch.setattr(latex_parser, "MAX_LATEX_SINGLE_GZIP_EXPANDED_BYTES", 64)
    monkeypatch.setattr(
        gzip,
        "decompress",
        lambda _data: (_ for _ in ()).throw(AssertionError("unbounded gzip.decompress used")),
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(compressed)

    assert caught.value.kind == "archive_expanded_too_large"


def test_tar_extraction_streams_without_getmembers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tarfile.TarFile,
        "getmembers",
        lambda _tar: (_ for _ in ()).throw(AssertionError("getmembers used")),
    )

    archive = extract_latex_archive(_TAR_GZ.read_bytes())

    assert "latex_rectified_flow_main.tex" in archive.text_files


@pytest.mark.parametrize("compression", ["raw", "gzip"])
@pytest.mark.parametrize("metadata_format", ["pax", "gnu"])
def test_tar_stream_limit_counts_extension_metadata_before_member_yield(
    monkeypatch: pytest.MonkeyPatch,
    compression: str,
    metadata_format: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    if metadata_format == "pax":
        raw = _raw_tar_archive(
            [("main.tex", source)],
            archive_format=tarfile.PAX_FORMAT,
            pax_headers={"comment": "x" * 100_000},
        )
    else:
        raw = _raw_tar_archive(
            [("x" * 100_000 + ".tex", source)],
            archive_format=tarfile.GNU_FORMAT,
        )
    archive = _compress_tar(raw, compression)
    monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_EXPANDED_BYTES", 1_024)

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "archive_expanded_too_large"
    assert "main.tex" not in str(caught.value)


@pytest.mark.parametrize("compression", ["raw", "gzip"])
@pytest.mark.parametrize(("limit_delta", "raises_limit"), [(0, False), (-1, True)])
def test_tar_stream_limit_has_exact_eof_boundary(
    monkeypatch: pytest.MonkeyPatch,
    compression: str,
    limit_delta: int,
    raises_limit: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    monkeypatch.setattr(
        latex_parser,
        "MAX_LATEX_ARCHIVE_EXPANDED_BYTES",
        len(raw) + limit_delta,
    )
    archive = _compress_tar(raw, compression)

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(archive)
        assert caught.value.kind == "archive_expanded_too_large"
    else:
        extracted = extract_latex_archive(archive)
        assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("compression", ["raw", "gzip"])
def test_tar_stream_limit_rejects_trailing_byte_after_valid_end_markers(
    monkeypatch: pytest.MonkeyPatch,
    compression: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    monkeypatch.setattr(
        latex_parser,
        "MAX_LATEX_ARCHIVE_EXPANDED_BYTES",
        len(raw),
    )
    archive = _compress_tar(raw + b"x", compression)

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "archive_expanded_too_large"


@pytest.mark.parametrize("compression", ["raw", "gzip"])
def test_tar_limit_failure_never_constructs_partial_archive(
    monkeypatch: pytest.MonkeyPatch,
    compression: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        main = tarfile.TarInfo("main.tex")
        main.size = len(source)
        archive.addfile(main, io.BytesIO(source))
        metadata = tarfile.TarInfo("metadata.sty")
        metadata.size = 1
        metadata.pax_headers = {"comment": "x" * 100_000}
        archive.addfile(metadata, io.BytesIO(b"x"))
    raw_bytes = raw.getvalue()
    monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_EXPANDED_BYTES", 20_000)
    constructed: list[bool] = []
    original_init = LatexArchive.__init__

    def recording_init(self: LatexArchive, *args: object, **kwargs: object) -> None:
        constructed.append(True)
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(LatexArchive, "__init__", recording_init)

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(_compress_tar(raw_bytes, compression))

    assert caught.value.kind == "archive_expanded_too_large"
    assert constructed == []


@pytest.mark.parametrize("archive_format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
@pytest.mark.parametrize("compression", ["raw", "gzip", "bzip2", "xz"])
def test_legal_long_tar_names_remain_compatible(
    archive_format: int,
    compression: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    long_name = "nested/" + ("long-name-" * 20) + "main.tex"
    raw = _raw_tar_archive(
        [(long_name, source)],
        archive_format=archive_format,
    )

    extracted = extract_latex_archive(_compress_tar(raw, compression))

    assert extracted.raw_text_files[long_name].encode() == source


def test_single_file_gzip_fallback_survives_bounded_tar_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    monkeypatch.setattr(
        latex_parser,
        "MAX_LATEX_ARCHIVE_EXPANDED_BYTES",
        len(source),
    )
    monkeypatch.setattr(
        latex_parser,
        "MAX_LATEX_SINGLE_GZIP_EXPANDED_BYTES",
        len(source),
    )

    extracted = extract_latex_archive(gzip.compress(source))

    assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("compression", ["raw", "gzip"])
@pytest.mark.parametrize("encoding", ["utf-8", "latin-1"])
def test_plausible_single_file_tex_preserves_text_encodings_and_whitespace_controls(
    compression: str,
    encoding: str,
) -> None:
    marker = "日本語" if encoding == "utf-8" else "café"
    source = (
        f"\\documentclass{{article}}\r\n\\begin{{document}}\t{marker}\f\\end{{document}}\n"
    ).encode(encoding)

    extracted = extract_latex_archive(_compress_tar(source, compression))

    assert marker in extracted.raw_text_files["main.tex"]


@pytest.mark.parametrize("compression", ["raw", "gzip"])
def test_single_file_tex_may_contain_incidental_ustar_text_at_tar_magic_offset(
    compression: str,
) -> None:
    source = b"x" * 257 + b"ustar \n" + b"\\documentclass{article}\\begin{document}x\\end{document}"

    extracted = extract_latex_archive(_compress_tar(source, compression))

    assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("compression", ["raw", "gzip"])
@pytest.mark.parametrize("binary_control", [0x00, 0x01, 0x08, 0x0B, 0x1F, 0x7F])
def test_single_file_fallback_rejects_binary_control_bytes(
    compression: str,
    binary_control: int,
) -> None:
    source = (
        b"\\documentclass{article}\\begin{document}x" + bytes([binary_control]) + b"\\end{document}"
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(_compress_tar(source, compression))

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("compression", ["gzip", "xz"])
def test_corrupt_compressed_tar_keeps_invalid_archive_contract(compression: str) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = _compress_tar(raw, compression)[:-8]

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"


def test_gzip_deflate_corruption_keeps_invalid_archive_contract() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = bytearray(_compress_tar(raw, "gzip"))
    archive[34] ^= 2

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(bytes(archive))

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("compression", ["raw", "gzip", "bzip2", "xz"])
@pytest.mark.parametrize("corrupt_magic", [False, True], ids=["checksum", "checksum-and-magic"])
def test_bad_tar_header_checksum_never_falls_back_to_embedded_tex(
    compression: str,
    corrupt_magic: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = bytearray(_raw_tar_archive([("main.tex", source)]))
    raw[148] ^= 1  # POSIX tar checksum field.
    if corrupt_magic:
        raw[257] ^= 1

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(_compress_tar(bytes(raw), compression))

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("compression", ["gzip", "bzip2", "xz"])
@pytest.mark.parametrize("kept_bytes", [8, 16, 32, 64])
def test_recognized_compression_truncated_before_tar_header_is_invalid(
    compression: str,
    kept_bytes: int,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = _compress_tar(raw, compression)[:kept_bytes]

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("compression", ["bzip2", "xz"])
@pytest.mark.parametrize("failure", ["read", "close"])
def test_recognized_decompressor_read_and_close_errors_are_invalid(
    monkeypatch: pytest.MonkeyPatch,
    compression: str,
    failure: str,
) -> None:
    class FailingCompressedStream:
        def __init__(self) -> None:
            self._offset = 0
            self._payload = b"not a tar archive"

        def __enter__(self) -> FailingCompressedStream:
            return self

        def __exit__(
            self,
            _exc_type: object,
            _exc: object,
            _traceback: object,
        ) -> Literal[False]:
            if failure == "close":
                raise OSError("corrupt compressed stream while closing")
            return False

        def read(self, size: int = -1) -> bytes:
            if failure == "read":
                raise OSError("corrupt compressed stream while reading")
            if size < 0:
                size = len(self._payload) - self._offset
            start = self._offset
            self._offset = min(len(self._payload), start + size)
            return self._payload[start : self._offset]

    if compression == "bzip2":
        monkeypatch.setattr(
            latex_parser,
            "_BoundedBzip2Reader",
            lambda *_args, **_kwargs: FailingCompressedStream(),
        )
    else:
        monkeypatch.setattr(
            latex_parser,
            "_MemoryLimitedLzmaReader",
            lambda *_args, **_kwargs: FailingCompressedStream(),
        )
    raw = _raw_tar_archive(
        [("main.tex", b"\\documentclass{article}\\begin{document}x\\end{document}")]
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(_compress_tar(raw, compression))

    assert caught.value.kind == "invalid_archive"


def test_decompressor_close_error_never_constructs_partial_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])

    class CloseFailingTarStream(io.BytesIO):
        def __enter__(self) -> CloseFailingTarStream:
            return self

        def __exit__(
            self,
            _exc_type: object,
            _exc: object,
            _traceback: object,
        ) -> None:
            raise OSError("corrupt compressed stream while closing")

    monkeypatch.setattr(
        latex_parser,
        "_BoundedBzip2Reader",
        lambda *_args, **_kwargs: CloseFailingTarStream(raw),
    )
    constructed: list[bool] = []
    original_init = LatexArchive.__init__

    def recording_init(self: LatexArchive, *args: object, **kwargs: object) -> None:
        constructed.append(True)
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(LatexArchive, "__init__", recording_init)
    archive = _compress_tar(raw, "bzip2")

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"
    assert constructed == []


def test_deep_pax_extension_chain_is_rejected_before_python_recursion_limit() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    entries = [_pax_extension(b"comment", str(index).encode()) for index in range(500)]
    entries.append(_manual_tar_entry("main.tex", source))

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(gzip.compress(_manual_tar_archive(entries)))

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (b"GNU.sparse.size", b"abc"),
        (b"GNU.sparse.map", b"0,0"),
        (b"GNU.sparse.realsize", b"1"),
    ],
)
def test_pax_sparse_metadata_is_rejected_before_stdlib_conversion(
    key: bytes,
    value: bytes,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _manual_tar_archive([_pax_extension(key, value), _manual_tar_entry("main.tex", source)])

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(raw)

    assert caught.value.kind == "invalid_archive"


def test_large_pax_sparse_map_is_rejected_before_object_amplification() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    sparse_map = b",".join([b"0", b"0"] * 250_000)
    raw = _manual_tar_archive(
        [
            _pax_extension(b"GNU.sparse.map", sparse_map),
            _manual_tar_entry("main.tex", source),
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(gzip.compress(raw))

    assert caught.value.kind == "invalid_archive"


def test_old_gnu_sparse_type_is_rejected_before_sparse_processing() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _manual_tar_archive(
        [_manual_tar_entry("main.tex", source, typeflag=tarfile.GNUTYPE_SPARSE)]
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(raw)

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("size", [-1, -2])
def test_negative_raw_tar_member_size_is_invalid(size: int) -> None:
    raw = _manual_tar_archive([_negative_size_tar_header("main.tex", size)])

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(raw)

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("global_header", [False, True], ids=["local", "global"])
def test_negative_pax_member_size_is_invalid(global_header: bool) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _manual_tar_archive(
        [
            _pax_extension(b"size", b"-1", global_header=global_header),
            _manual_tar_entry("main.tex", source),
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(raw)

    assert caught.value.kind == "invalid_archive"


def test_negative_non_file_tar_header_size_is_invalid() -> None:
    raw = _manual_tar_archive(
        [_negative_size_tar_header("directory/", -1, typeflag=tarfile.DIRTYPE)]
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(raw)

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("pax_override", [False, True], ids=["raw", "local-pax"])
@pytest.mark.parametrize(("size_delta", "raises_limit"), [(0, False), (1, True)])
def test_unknown_tar_type_size_has_exact_early_boundary(
    monkeypatch: pytest.MonkeyPatch,
    pax_override: bool,
    size_delta: int,
    raises_limit: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    size_limit = 128
    unknown_size = size_limit + size_delta
    monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_MEMBER_BYTES", size_limit)
    unknown = _manual_tar_entry(
        "metadata.bin",
        b"x" * unknown_size,
        typeflag=b"Z",
        declared_size=0 if pax_override else unknown_size,
    )
    entries = [unknown, _manual_tar_entry("main.tex", source)]
    if pax_override:
        entries.insert(0, _pax_extension(b"size", str(unknown_size).encode()))
    raw = _manual_tar_archive(entries)

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(raw)
        assert caught.value.kind == "invalid_archive"
    else:
        extracted = extract_latex_archive(raw)
        assert extracted.raw_text_files["main.tex"].encode() == source


def test_huge_base256_unknown_size_is_rejected_before_stream_seek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _manual_tar_archive([_negative_size_tar_header("metadata.bin", 1 << 40, typeflag=b"Z")])
    seek_calls: list[int] = []

    def forbidden_seek(_stream: object, position: int) -> int:
        seek_calls.append(position)
        raise AssertionError("tar stream seek reached attacker-controlled huge offset")

    monkeypatch.setattr(tarfile._Stream, "seek", forbidden_seek)  # type: ignore[attr-defined]

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(raw)

    assert caught.value.kind == "invalid_archive"
    assert seek_calls == []


@pytest.mark.parametrize(
    "case",
    [
        "pax_header_bytes",
        "pax_total_bytes",
        "pax_header_records",
        "pax_total_records",
        "extension_headers",
        "raw_headers",
        "extension_depth",
    ],
)
def test_tar_metadata_caps_apply_before_real_member_yield(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    one = _pax_record(b"comment", b"one")
    two = _pax_record(b"comment", b"two")
    entries: list[bytes]

    if case == "pax_header_bytes":
        monkeypatch.setattr(latex_parser, "MAX_LATEX_PAX_HEADER_BYTES", len(one) - 1, raising=False)
        entries = [
            _manual_tar_entry("pax", one, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("main.tex", source),
        ]
    elif case == "pax_total_bytes":
        monkeypatch.setattr(
            latex_parser, "MAX_LATEX_PAX_TOTAL_BYTES", len(one) + len(two) - 1, raising=False
        )
        entries = [
            _manual_tar_entry("pax-1", one, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("pax-2", two, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("main.tex", source),
        ]
    elif case == "pax_header_records":
        monkeypatch.setattr(latex_parser, "MAX_LATEX_PAX_RECORDS_PER_HEADER", 1, raising=False)
        entries = [
            _manual_tar_entry("pax", one + two, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("main.tex", source),
        ]
    elif case == "pax_total_records":
        monkeypatch.setattr(latex_parser, "MAX_LATEX_PAX_TOTAL_RECORDS", 1, raising=False)
        entries = [
            _manual_tar_entry("pax-1", one, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("pax-2", two, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("main.tex", source),
        ]
    elif case == "extension_headers":
        monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_EXTENSION_HEADERS", 1, raising=False)
        entries = [
            _manual_tar_entry("pax-1", one, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("pax-2", two, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("main.tex", source),
        ]
    elif case == "raw_headers":
        monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_HEADERS", 2, raising=False)
        entries = [
            _manual_tar_entry("a.tex", b"a"),
            _manual_tar_entry("b.tex", b"b"),
            _manual_tar_entry("main.tex", source),
        ]
    else:
        monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_EXTENSION_DEPTH", 2, raising=False)
        entries = [
            _manual_tar_entry(f"pax-{index}", one, typeflag=tarfile.XHDTYPE) for index in range(3)
        ]
        entries.append(_manual_tar_entry("main.tex", source))

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(_manual_tar_archive(entries))

    assert caught.value.kind == "invalid_archive"


def test_raw_tar_header_count_has_exact_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    monkeypatch.setattr(latex_parser, "MAX_LATEX_ARCHIVE_HEADERS", 2)
    raw = _manual_tar_archive(
        [
            _manual_tar_entry("appendix.tex", b"appendix"),
            _manual_tar_entry("main.tex", source),
        ]
    )

    extracted = extract_latex_archive(raw)

    assert extracted.raw_text_files["main.tex"].encode() == source


def test_global_and_local_pax_headers_preserve_paths_and_following_members() -> None:
    main = b"\\documentclass{article}\\begin{document}main\\end{document}"
    appendix = b"appendix"
    raw = _manual_tar_archive(
        [
            _pax_extension(b"comment", b"global", global_header=True),
            _pax_extension(b"path", b"nested/main.tex"),
            _manual_tar_entry("placeholder.tex", main),
            _manual_tar_entry("appendix.tex", appendix),
        ]
    )

    extracted = extract_latex_archive(raw)

    assert extracted.raw_text_files == {
        "nested/main.tex": main.decode(),
        "appendix.tex": appendix.decode(),
    }


def test_pax_payload_at_exact_tar_block_boundary_preserves_next_header() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    payload = next(
        record
        for size in range(1_000)
        if len(record := _pax_record(b"comment", b"x" * size)) == tarfile.BLOCKSIZE
    )
    raw = _manual_tar_archive(
        [
            _manual_tar_entry("pax", payload, typeflag=tarfile.XHDTYPE),
            _manual_tar_entry("main.tex", source),
        ]
    )

    extracted = extract_latex_archive(raw)

    assert extracted.raw_text_files["main.tex"].encode() == source


def test_gnu_long_name_and_link_extensions_preserve_following_members() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    long_name = "nested/" + "long-name-" * 20 + "main.tex"
    long_link = "nested/" + "long-target-" * 20 + "asset.bin"
    raw = _manual_tar_archive(
        [
            _manual_tar_entry(
                "././@LongLink",
                long_name.encode() + b"\0",
                typeflag=tarfile.GNUTYPE_LONGNAME,
            ),
            _manual_tar_entry("placeholder.tex", source),
            _manual_tar_entry(
                "././@LongLink",
                long_link.encode() + b"\0",
                typeflag=tarfile.GNUTYPE_LONGLINK,
            ),
            _manual_tar_entry("asset-link", b"", typeflag=tarfile.SYMTYPE, linkname="placeholder"),
            _manual_tar_entry("after.tex", b"after"),
        ]
    )

    extracted = extract_latex_archive(raw)

    assert extracted.raw_text_files[long_name].encode() == source
    assert extracted.raw_text_files["after.tex"] == "after"


@pytest.mark.parametrize(
    "error",
    [
        tarfile.StreamError("bad stream metadata"),
        ValueError("bad numeric metadata"),
        OverflowError("oversized numeric metadata"),
        RecursionError("recursive metadata"),
    ],
    ids=["tar-error", "value-error", "overflow-error", "recursion-error"],
)
def test_tar_metadata_internal_errors_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    raw = _raw_tar_archive([("main.tex", b"x")])

    def exploding_frombuf(
        _cls: type[tarfile.TarInfo],
        _buffer: bytes,
        _encoding: str,
        _errors: str,
    ) -> tarfile.TarInfo:
        raise error

    monkeypatch.setattr(tarfile.TarInfo, "frombuf", classmethod(exploding_frombuf))

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(raw)

    assert caught.value.kind == "invalid_archive"


def test_tar_metadata_memory_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_tar_archive([("main.tex", b"x")])

    def memory_error_frombuf(
        _cls: type[tarfile.TarInfo],
        _buffer: bytes,
        _encoding: str,
        _errors: str,
    ) -> tarfile.TarInfo:
        raise MemoryError("out of memory")

    monkeypatch.setattr(tarfile.TarInfo, "frombuf", classmethod(memory_error_frombuf))

    with pytest.raises(MemoryError):
        extract_latex_archive(raw)


def test_xz_decoder_rejects_oversized_dictionary_with_explicit_memlimit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = _xz_with_lzma2_dictionary(raw, 40)
    real_decompressor = lzma.LZMADecompressor
    seen_memlimits: list[int] = []

    def guarded_decompressor(*args: object, **kwargs: object) -> lzma.LZMADecompressor:
        memlimit = kwargs.get("memlimit")
        assert isinstance(memlimit, int)
        assert 0 < memlimit <= 256 * 1024 * 1024
        seen_memlimits.append(memlimit)
        return real_decompressor(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(lzma, "LZMADecompressor", guarded_decompressor)
    monkeypatch.setattr(
        lzma,
        "LZMAFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unbounded LZMAFile used")),
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"
    assert seen_memlimits


@pytest.mark.parametrize("padding_bytes", [0, 4, 8, 12])
def test_concatenated_xz_streams_form_one_tar_stream(padding_bytes: int) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    split = 777
    archive = lzma.compress(raw[:split]) + b"\0" * padding_bytes + lzma.compress(raw[split:])

    extracted = extract_latex_archive(archive)

    assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize(
    "suffix",
    [b"trailing-data", b"\0" * 4 + b"trailing-data", b"\0" * 8 + b"trailing-data"],
)
def test_xz_trailing_non_stream_data_remains_compatible(suffix: bytes) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])

    extracted = extract_latex_archive(lzma.compress(raw) + suffix)

    assert extracted.raw_text_files["main.tex"].encode() == source


def test_truncated_second_xz_stream_is_invalid() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    split = 777
    archive = lzma.compress(raw[:split]) + lzma.compress(raw[split:])[:-8]

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("padding_bytes", [0, 4])
def test_oversized_dictionary_in_concatenated_xz_stream_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    padding_bytes: int,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = lzma.compress(raw) + b"\0" * padding_bytes + _xz_with_lzma2_dictionary(b"x", 40)
    real_decompressor = lzma.LZMADecompressor

    def guarded_decompressor(*args: object, **kwargs: object) -> lzma.LZMADecompressor:
        assert kwargs.get("memlimit") == 128 * 1024 * 1024
        return real_decompressor(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(lzma, "LZMADecompressor", guarded_decompressor)
    monkeypatch.setattr(
        lzma,
        "LZMAFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unbounded LZMAFile used")),
    )

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("padding_bytes", [0, 4, 8, 12])
def test_xz_stream_padding_at_eof_is_valid(padding_bytes: int) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])

    extracted = extract_latex_archive(lzma.compress(raw) + b"\0" * padding_bytes)

    assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("padding_bytes", [1, 2, 3, 5])
@pytest.mark.parametrize("following", ["eof", "next-stream", "trailing-junk"])
def test_xz_stream_padding_must_be_a_multiple_of_four(
    padding_bytes: int,
    following: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    if following == "next-stream":
        split = 777
        archive = lzma.compress(raw[:split]) + b"\0" * padding_bytes + lzma.compress(raw[split:])
    else:
        suffix = b"" if following == "eof" else b"trailing-data"
        archive = lzma.compress(raw) + b"\0" * padding_bytes + suffix

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("magic_bytes", [1, 2, 3, 4, 5])
def test_xz_partial_magic_after_valid_padding_is_invalid(magic_bytes: int) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = lzma.compress(raw) + b"\0" * 4 + b"\xfd7zXZ\0"[:magic_bytes]

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(archive)

    assert caught.value.kind == "invalid_archive"


def test_xz_padding_and_magic_may_cross_compressed_input_chunk_boundary() -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    split = 777
    first = lzma.compress(raw[:split])
    second = lzma.compress(raw[split:])
    chunk_bytes = 64 * 1024
    magic_prefix_bytes = next(
        size for size in range(1, 6) if (chunk_bytes - len(first) - size) % 4 == 0
    )
    padding_bytes = chunk_bytes - len(first) - magic_prefix_bytes
    assert padding_bytes > 0 and padding_bytes % 4 == 0
    archive = first + b"\0" * padding_bytes + second

    extracted = extract_latex_archive(archive)

    assert extracted.raw_text_files["main.tex"].encode() == source


def test_xz_padding_scan_does_not_loop_over_individual_bytes() -> None:
    class CountingBytes(bytes):
        item_reads = 0

        @overload
        def __getitem__(self, key: SupportsIndex) -> int: ...
        @overload
        def __getitem__(self, key: slice) -> bytes: ...
        def __getitem__(self, key: SupportsIndex | slice) -> int | bytes:
            type(self).item_reads += 1
            return super().__getitem__(key)

    padding = CountingBytes(b"\0" * (1024 * 1024))

    result = latex_parser._scan_xz_stream_padding(io.BytesIO(), padding)

    assert result is None
    assert CountingBytes.item_reads < 100


@pytest.mark.parametrize(("stream_count", "raises_limit"), [(1_024, False), (1_025, True)])
def test_xz_padded_stream_count_has_default_exact_boundary(
    stream_count: int,
    raises_limit: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    streams = [lzma.compress(b"")] * (stream_count - 1) + [lzma.compress(raw)]
    archive = (b"\0" * 4).join(streams)

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(archive)
        assert caught.value.kind == "invalid_archive"
    else:
        extracted = extract_latex_archive(archive)
        assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize(("extra_streams", "raises_limit"), [(0, False), (1, True)])
def test_concatenated_xz_stream_count_has_exact_boundary(
    monkeypatch: pytest.MonkeyPatch,
    extra_streams: int,
    raises_limit: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    stream_limit = 3
    monkeypatch.setattr(
        latex_parser,
        "MAX_LATEX_COMPRESSED_STREAMS",
        stream_limit,
        raising=False,
    )
    archive = lzma.compress(b"") * (stream_limit - 1 + extra_streams) + lzma.compress(raw)

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(archive)
        assert caught.value.kind == "invalid_archive"
    else:
        extracted = extract_latex_archive(archive)
        assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("compression", ["gzip", "bzip2"])
@pytest.mark.parametrize(("extra_streams", "raises_limit"), [(0, False), (1, True)])
def test_gzip_and_bzip2_stream_counts_have_exact_boundary(
    monkeypatch: pytest.MonkeyPatch,
    compression: str,
    extra_streams: int,
    raises_limit: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    stream_limit = 3
    monkeypatch.setattr(
        latex_parser,
        "MAX_LATEX_COMPRESSED_STREAMS",
        stream_limit,
        raising=False,
    )
    archive = _compress_tar(b"", compression) * (stream_limit - 1 + extra_streams) + _compress_tar(
        raw, compression
    )

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(archive)
        assert caught.value.kind == "invalid_archive"
    else:
        extracted = extract_latex_archive(archive)
        assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize(("extra_streams", "raises_limit"), [(0, False), (1, True)])
def test_single_tex_gzip_fallback_uses_same_stream_count_limit(
    monkeypatch: pytest.MonkeyPatch,
    extra_streams: int,
    raises_limit: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    stream_limit = 3
    monkeypatch.setattr(
        latex_parser,
        "MAX_LATEX_COMPRESSED_STREAMS",
        stream_limit,
        raising=False,
    )
    archive = gzip.compress(b"") * (stream_limit - 1 + extra_streams) + gzip.compress(source)

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(archive)
        assert caught.value.kind == "invalid_archive"
    else:
        extracted = extract_latex_archive(archive)
        assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("compression", ["gzip", "bzip2"])
def test_concatenated_gzip_and_bzip2_streams_form_one_tar_stream(
    compression: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    split = 777
    archive = _compress_tar(raw[:split], compression) + _compress_tar(raw[split:], compression)

    extracted = extract_latex_archive(archive)

    assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize(
    ("compression", "suffix", "raises_invalid"),
    [
        ("gzip", b"\0" * 8, False),
        ("gzip", b"trailing-data", True),
        ("bzip2", b"\0" * 8, False),
        ("bzip2", b"trailing-data", False),
    ],
)
def test_gzip_and_bzip2_trailing_data_compatibility(
    compression: str,
    suffix: bytes,
    raises_invalid: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = _compress_tar(raw, compression) + suffix

    if raises_invalid:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(archive)
        assert caught.value.kind == "invalid_archive"
    else:
        extracted = extract_latex_archive(archive)
        assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("compression", ["bzip2", "xz"])
@pytest.mark.parametrize("suffix", [b"\0" * 8, b"trailing-data"])
def test_ignored_trailing_data_does_not_consume_stream_budget(
    monkeypatch: pytest.MonkeyPatch,
    compression: str,
    suffix: bytes,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    monkeypatch.setattr(latex_parser, "MAX_LATEX_COMPRESSED_STREAMS", 1)

    extracted = extract_latex_archive(_compress_tar(raw, compression) + suffix)

    assert extracted.raw_text_files["main.tex"].encode() == source


@pytest.mark.parametrize("compression", ["gzip", "bzip2"])
@pytest.mark.parametrize("corruption", ["checksum", "truncated"])
def test_gzip_and_bzip2_integrity_errors_are_invalid(
    compression: str,
    corruption: str,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = _raw_tar_archive([("main.tex", source)])
    archive = bytearray(_compress_tar(raw, compression))
    if corruption == "checksum":
        archive[-1] ^= 1
    else:
        del archive[-1]

    with pytest.raises(LatexParseError) as caught:
        extract_latex_archive(bytes(archive))

    assert caught.value.kind == "invalid_archive"


@pytest.mark.parametrize("encoding", ["utf-8", "latin-1"])
def test_raw_single_tex_may_start_with_incidental_bzip2_magic(encoding: str) -> None:
    marker = "日本語" if encoding == "utf-8" else "café"
    source = (
        "BZh is ordinary source text\n"
        "\\documentclass{article}\n"
        f"\\begin{{document}}{marker}\\end{{document}}"
    ).encode(encoding)

    extracted = extract_latex_archive(source)

    assert marker in extracted.raw_text_files["main.tex"]


@pytest.mark.parametrize("corrupt_checksum", [False, True], ids=["valid", "corrupt"])
def test_raw_tar_may_start_with_incidental_bzip2_magic(
    corrupt_checksum: bool,
) -> None:
    source = b"\\documentclass{article}\\begin{document}x\\end{document}"
    raw = bytearray(_raw_tar_archive([("BZh-main.tex", source)]))
    if corrupt_checksum:
        raw[148] ^= 1

    if corrupt_checksum:
        with pytest.raises(LatexParseError) as caught:
            extract_latex_archive(bytes(raw))
        assert caught.value.kind == "invalid_archive"
    else:
        extracted = extract_latex_archive(bytes(raw))
        assert extracted.raw_text_files["BZh-main.tex"].encode() == source


# ============================ ブロック型・IR(PY-PARSE-01 相当) ============================


def test_parser_version_and_quality() -> None:
    doc = _doc()
    assert doc.parser_version == PARSER_VERSION == "latex-1.3.7"
    assert doc.quality_level == "A"
    assert doc.source_format == "latex"


def test_parses_all_twelve_block_types() -> None:
    doc = _doc()
    kinds = {b.type for b in doc.blocks}
    expected = {
        "heading",
        "paragraph",
        "figure",
        "table",
        "equation",
        "code",
        "list",
        "quote",
        "theorem",
        "algorithm",
        "footnote",
        "reference_entry",
    }
    assert expected <= kinds, f"missing block types: {expected - kinds}"


def test_assumption_environment_is_parsed_as_a_theorem_block() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\begin{assumption}[Quadratic growth]
There exists $\mu > 0$ such that the objective grows quadratically.
\end{assumption}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})

    assumptions = [block for block in document.blocks if block.type == "theorem"]
    assert len(assumptions) == 1
    assert "There exists" in block_to_plain(assumptions[0])


def test_all_block_ids_are_prefixed_pathsafe_and_unique() -> None:
    doc = _doc()
    ids = [b.id for b in doc.blocks]
    assert ids, "no blocks parsed"
    assert all(bid.startswith("blk-") for bid in ids)
    assert all(" " not in bid for bid in ids)
    assert len(ids) == len(set(ids))


def test_block_ids_are_deterministic() -> None:
    a = [b.id for b in _doc().blocks]
    b = [b.id for b in _doc().blocks]
    assert a == b


def test_section_tree_nesting_and_paths() -> None:
    doc = _doc()
    top_ids = [s.id for s in doc.sections]
    assert "sec-1" in top_ids
    assert "sec-2" in top_ids
    assert "sec-A" in top_ids  # 付録は番号 A に正規化(plans/05 §4.2 と同方針)
    assert "sec-refs" in top_ids  # 参考文献は独立したトップレベルセクションへ昇格
    sec1 = next(s for s in doc.sections if s.id == "sec-1")
    assert [sub.id for sub in sec1.sections] == ["sec-1-1"]
    assert sec1.heading.number == "1"
    assert sec1.heading.title == "Introduction"
    sub = sec1.sections[0]
    assert sub.heading.number == "1.1"
    assert sub.heading.title == "Reflow"


def test_appendix_number_normalized() -> None:
    doc = _doc()
    sec_a = next(s for s in doc.sections if s.id == "sec-A")
    assert sec_a.heading.number == "A"
    assert sec_a.heading.title == "Proofs"


def test_references_section_is_last_and_independent_of_appendix() -> None:
    doc = _doc()
    order = [s.id for s in doc.sections]
    assert order.index("sec-refs") > order.index("sec-A")
    refs_sec = next(s for s in doc.sections if s.id == "sec-refs")
    assert refs_sec.sections == []
    assert all(b.type in ("heading", "reference_entry") for b in refs_sec.blocks)


def test_metadata_sections_skipped() -> None:
    doc = _doc()
    paras = [b for b in doc.blocks if b.type == "paragraph"]
    joined = " ".join(block_to_plain(b) for b in paras)
    assert "We present rectified flow" not in joined  # abstract
    assert "Xingchao Liu" not in joined  # author (title/author 除去)


def test_latex_setup_commands_do_not_leak_into_body() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                r"\documentclass{article}"
                r"\begin{document}"
                r"\affiliation{ Microsoft Redmond USA }"
                r"\definecolor{ForestGreen}{RGB}{34,139,34}"
                r"\definecolor{Gray}{gray}{0.9}"
                r"\newcommand{\CoverageEval}{{\textsc{CoverageEval}}}"
                r"\newcommand{\hl}[2]{ #1 {▶#2◀} }"
                r"\newcommand{\MICHELE}[1]{\textcolor{blue}{MICHELE{#1}}}"
                r"\section{Intro}"
                r"This paragraph is real body text and should remain."
                r"\end{document}"
            )
        },
    )
    joined = " ".join(block_to_plain(b) for b in doc.blocks if b.type == "paragraph")
    assert "This paragraph is real body text" in joined
    assert "iation{" not in joined
    assert "Microsoft Redmond" not in joined
    assert "ForestGreen" not in joined
    assert "CoverageEval" not in joined
    assert "MICHELE" not in joined
    assert "▶" not in joined


def test_custom_macros_expand_to_visible_prose_in_body_caption_and_heading() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "paper.cls": r"\newcommand{\benchprefix}{IdeaGene}",
            "main.tex": (
                r"\documentclass{paper}"
                r"\newcommand{\benchfull}{\benchprefix-Bench}"
                r"\newcommand{\genome}{\textit{Idea Genome}}"
                r"\newcommand{\relation}[2]{#1 / #2}"
                r"\begin{document}"
                r"\section{\benchfull{} Results}"
                r"\benchfull{} compares each \genome{} with \relation{parent}{child}."
                r"\begin{figure}\caption{Overview of \benchfull{} and \genome{}.}\end{figure}"
                r"\begin{table}\begin{tabular}{ll}"
                r"\benchfull{} & \genome{} \\"
                r"\end{tabular}\end{table}"
                r"\end{document}"
            ),
        },
    )

    heading = next(block for block in doc.blocks if block.type == "heading")
    paragraph = next(block for block in doc.blocks if block.type == "paragraph")
    figure = next(block for block in doc.blocks if block.type == "figure")
    table = next(block for block in doc.blocks if block.type == "table")
    assert heading.title == "IdeaGene-Bench Results"
    assert block_to_plain(paragraph) == (
        "IdeaGene-Bench compares each Idea Genome with parent / child."
    )
    assert block_to_plain(figure).replace(" .", ".") == (
        "Overview of IdeaGene-Bench and Idea Genome."
    )
    assert "IdeaGene-Bench & Idea Genome" in (table.raw or "")
    assert "\\benchfull" not in (table.raw or "")
    assert "\\genome" not in (table.raw or "")


def test_layout_commands_and_container_options_never_become_body_text() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                r"\documentclass{article}\begin{document}"
                r"\vspace{-6mm}\enlargethispage{1.5cm}"
                r"\begin{center}\begin{tcolorbox}["
                r"colback=blue!3,colframe=blue!25,title={Visible box title}]"
                r"\itshape ``Descent with modification.'' \\[1pt]"
                r"\textbf{\textcolor{CaseOrange}{Question.}} Read this."
                r"\begin{quote}\small A nested quotation.\end{quote}"
                r"\end{tcolorbox}\end{center}\vspace{-2pt}"
                r"\section{Body}Real body.\end{document}"
            )
        },
    )

    prose = " ".join(
        block_to_plain(block)
        for block in doc.blocks
        if block.type not in {"heading", "equation", "code"}
    )
    assert "Visible box title" in prose
    assert "Descent with modification." in prose
    assert "Question. Read this." in prose
    assert "A nested quotation." in prose
    for leaked in ("-6mm", "1.5cm", "-2pt", "colback", "colframe", "CaseOrange", "quote"):
        assert leaked not in prose
    assert any(block.type == "quote" for block in doc.blocks)


def test_custom_beginappendix_switches_numbering_to_letters() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "paper.cls": r"\newcommand{\beginappendix}{\appendix}",
            "main.tex": (
                r"\documentclass{paper}\begin{document}"
                r"\section{Main}Text."
                r"\beginappendix\section{Details}Appendix text."
                r"\end{document}"
            ),
        },
    )

    assert [(section.heading.number, section.heading.title) for section in doc.sections] == [
        ("1", "Main"),
        ("A", "Details"),
    ]


def test_input_command_is_expanded_into_appendix_section() -> None:
    doc = _doc()
    sec_a = next(s for s in doc.sections if s.id == "sec-A")
    joined = " ".join(block_to_plain(b) for b in sec_a.blocks)
    assert "Appendix proof text" in joined


# ============================ インライン(8 種) ============================


def test_all_eight_inline_types_extracted() -> None:
    doc = _doc()
    seen: set[str] = set()
    for b in doc.blocks:
        for il in b.inlines + b.caption:
            seen.add(il.t)
        for item in b.items:
            for il in item:
                seen.add(il.t)
    expected = {
        "text",
        "math_inline",
        "citation",
        "ref",
        "footnote_ref",
        "url",
        "emphasis",
        "code_inline",
    }
    assert expected <= seen, f"missing inline types: {expected - seen}"


def test_code_inline_from_texttt() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{M}"
                "Run \\texttt{pip install} now.\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    code = next(il for il in para.inlines if il.t == "code_inline")
    assert code.v == "pip install"


def test_inline_parser_preserves_display_math_and_symbol_macros() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{M}"
                "We use \\LaTeX{} notation, \\eg{} display math \\[ x^2 + y^2 \\]."
                "\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    text = block_to_plain(para)
    assert "LaTeX" in text
    assert "e.g." in text
    math = next(il for il in para.inlines if il.t == "math_inline")
    assert math.v == "x^2 + y^2"


# ============================ 相互参照解決(PY-PARSE-02 の追加要件) ============================


def test_cite_resolves_to_citation_inline_matching_bibitem_label() -> None:
    doc = _doc()
    para = next(b for b in doc.blocks if any(il.t == "citation" for il in b.inlines))
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "liu2022flow"
    ref_labels = {b.label for b in doc.references}
    assert citation.ref in ref_labels


def test_eqref_resolves_kind_equation_via_label_map() -> None:
    doc = _doc()
    para = next(
        b for b in doc.blocks if any(il.t == "ref" and il.ref == "eq:ode" for il in b.inlines)
    )
    ref = next(il for il in para.inlines if il.ref == "eq:ode")
    assert ref.kind == "equation"
    eq = next(b for b in doc.blocks if b.type == "equation" and b.label == "eq:ode")
    assert eq is not None


def test_ref_resolves_kind_figure_and_table_via_label_map() -> None:
    doc = _doc()
    para = next(b for b in doc.blocks if any(il.ref == "fig:overview" for il in b.inlines))
    fig_ref = next(il for il in para.inlines if il.ref == "fig:overview")
    assert fig_ref.kind == "figure"
    tbl_ref = next(il for il in para.inlines if il.ref == "tab:results")
    assert tbl_ref.kind == "table"


def test_ref_resolves_kind_section_across_files() -> None:
    """付録(\\input 展開後)からメイン文書のセクションラベルを参照解決できる。"""
    doc = _doc()
    sec_a = next(s for s in doc.sections if s.id == "sec-A")
    ref = next(il for b in sec_a.blocks for il in b.inlines if il.ref == "sec:method")
    assert ref.kind == "section"


def test_unresolved_ref_degrades_to_section_kind_with_warning() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}"
                "\\section{X}\\label{sec:x}See~\\ref{sec:unknown-target}."
                "\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    ref = next(il for il in para.inlines if il.t == "ref")
    assert ref.kind == "section"
    assert any("sec:unknown-target" in w for w in doc.warnings)


# ============================ 数式(equation/align 分割・ソース保持) ============================


def test_equation_latex_source_preserved_verbatim() -> None:
    doc = _doc()
    eq = next(b for b in doc.blocks if b.type == "equation" and b.label == "eq:ode")
    assert eq.latex == "\\mathrm{d}Z_t = v(Z_t, t)\\,\\mathrm{d}t"


def test_align_environment_splits_into_multiple_equation_blocks() -> None:
    doc = _doc()
    sec2 = next(s for s in doc.sections if s.id == "sec-2")
    eqs = [b for b in sec2.blocks if b.type == "equation"]
    assert len(eqs) == 2
    assert eqs[0].label == "eq:group"
    assert "\\arg\\min" in (eqs[0].latex or "")
    assert "X_0" in (eqs[1].latex or "")


def test_align_row_splitter_preserves_nested_matrix_rows() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Matrices}
\begin{align}
V &= \begin{pmatrix}
  0 & I \\
  0 & 0
\end{pmatrix}, \\
W &= 1.
\end{align}
\end{document}
"""

    parsed = parse_latex_source("main.tex", {"main.tex": source}).to_document_content()
    equations = [
        block.latex or "" for _section, block in parsed.iter_blocks() if block.type == "equation"
    ]

    assert len(equations) == 2
    assert r"\begin{pmatrix}" in equations[0]
    assert r"0 & I \\ 0 & 0" in " ".join(equations[0].split())
    assert r"\end{pmatrix}" in equations[0]
    assert equations[1] == "W &= 1."


def test_top_level_double_dollar_array_is_one_display_equation() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Arrays}
Before the array.
$$
\begin{array}{cc}
1 & 2 \\
3 & 4
\end{array}.
$$
After the array with $x$ inline.
\end{document}
"""

    content = parse_latex_source("main.tex", {"main.tex": source}).to_document_content()
    blocks = [block for _section, block in content.iter_blocks()]
    equations = [block for block in blocks if block.type == "equation"]
    paragraphs = [block for block in blocks if block.type == "paragraph"]

    assert len(equations) == 1
    assert r"\begin{array}{cc}" in (equations[0].latex or "")
    assert r"1 & 2 \\ 3 & 4" in " ".join((equations[0].latex or "").split())
    assert [block_to_plain(block) for block in paragraphs] == [
        "Before the array.",
        "After the array with x inline.",
    ]
    assert all("$$" not in block_to_plain(block) for block in blocks)


def test_inline_math_dollar_delimiter_preserved() -> None:
    doc = _doc()
    sec1 = next(s for s in doc.sections if s.id == "sec-1")
    sub = next(s for s in sec1.sections if s.id == "sec-1-1")
    para = next(b for b in sub.blocks if b.type == "paragraph")
    math = next(il for il in para.inlines if il.t == "math_inline")
    assert math.v == "y = f(x)"


_MATH_CORPUS: list[tuple[str, str]] = [
    ("$x+y$", "x+y"),
    ("$\\frac{1}{2}$", "\\frac{1}{2}"),
    ("$\\sum_{i=1}^{n} x_i$", "\\sum_{i=1}^{n} x_i"),
    ("$\\mathbb{E}_{x\\sim\\pi_0}[f(x)]$", "\\mathbb{E}_{x\\sim\\pi_0}[f(x)]"),
    ("\\(a^2+b^2=c^2\\)", "a^2+b^2=c^2"),
]


@pytest.mark.parametrize("math_src,expected_latex", _MATH_CORPUS)
def test_math_corpus_sources_preserved(math_src: str, expected_latex: str) -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                f"\\documentclass{{article}}\\begin{{document}}\\section{{M}}"
                f"Given {math_src} here.\\end{{document}}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    maths = [il for il in para.inlines if il.t == "math_inline"]
    assert len(maths) == 1
    assert maths[0].v == expected_latex


# ============================ 図表・参考文献 ============================


def test_figure_asset_caption_and_label() -> None:
    doc = _doc()
    fig = next(b for b in doc.blocks if b.type == "figure")
    assert fig.asset_key == "x1.png"
    assert fig.label == "fig:overview"
    cap = " ".join(il.v for il in fig.caption if il.t == "text")
    assert "Overview" in cap


def test_figure_environment_emits_every_includegraphics_in_order() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Panels}
\begin{figure}
  \includegraphics [width=.48\textwidth] {panel-a.pdf}
  \includegraphics {panel-b.png}
  \caption{A shared two-panel caption.}
  \label{fig:panels}
\end{figure}
\end{document}
"""

    first = parse_latex_source("main.tex", {"main.tex": source})
    second = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in first.blocks if block.type == "figure"]
    repeated = [block for block in second.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["panel-a.pdf", "panel-b.png"]
    assert figures[0].label == "fig:panels"
    assert figures[1].label is None
    assert "shared two-panel" in block_to_plain(figures[0]).lower()
    assert block_to_plain(figures[1]) == ""
    assert [block.id for block in figures] == [block.id for block in repeated]
    assert len({block.id for block in figures}) == 2


def test_standalone_includegraphics_are_figures_in_document_order_without_path_text() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Standalone panels}
Before the panels.
\includegraphics {general-panel.pdf}
\begin{center}
  \includegraphics[width=.5\textwidth] {center-panel.png}
\end{center}
\begin{minipage}{.5\textwidth}
  \includegraphics* {minipage-panel.jpg}
\end{minipage}
After the panels.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]
    visible_text = " ".join(
        inline.v
        for block in document.blocks
        if block.type == "paragraph"
        for inline in block.inlines
        if inline.t == "text"
    )

    assert [block.asset_key for block in figures] == [
        "general-panel.pdf",
        "center-panel.png",
        "minipage-panel.jpg",
    ]
    assert "Before the panels" in visible_text
    assert "After the panels" in visible_text
    assert "includegraphics" not in visible_text
    assert all((block.asset_key or "") not in visible_text for block in figures)


def test_image_backed_table_retains_table_semantics_and_emits_each_asset_once() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Image table}
\begin{table}
  \caption{Image-backed results.}
  \label{tab:image-results}
  \begin{tabular}{cc}
    Baseline & \includegraphics{table-a.pdf} \\
    Ours & \includegraphics [width=2cm] {table-b.png}
  \end{tabular}
\end{table}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    tables = [block for block in document.blocks if block.type == "table"]
    figures = [block for block in document.blocks if block.type == "figure"]

    assert len(tables) == 1
    assert tables[0].label == "tab:image-results"
    assert "Image-backed results" in block_to_plain(tables[0])
    assert tables[0].raw is not None and "\\begin{tabular}" in tables[0].raw
    assert "includegraphics" not in tables[0].raw
    assert "table-a.pdf" not in tables[0].raw
    assert "table-b.png" not in tables[0].raw
    assert [block.asset_key for block in figures] == ["table-a.pdf", "table-b.png"]


def test_whole_image_backed_table_attaches_asset_to_table_block() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Image table}
\begin{table}
  \caption{Runtime comparison.}
  \label{tab:runtime}
  \centering
  \includegraphics[width=\columnwidth]{runtime-table.pdf}
\end{table}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    tables = [block for block in document.blocks if block.type == "table"]
    figures = [block for block in document.blocks if block.type == "figure"]

    assert len(tables) == 1
    assert tables[0].label == "tab:runtime"
    assert tables[0].asset_key == "runtime-table.pdf"
    assert tables[0].raw is None
    assert "Runtime comparison" in block_to_plain(tables[0])
    assert figures == []


def test_whole_image_backed_table_keeps_additional_panels_as_tables() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Image table}
\begin{table}
  \caption{Multi-page results.}
  \label{tab:multi-page}
  \includegraphics{results-page-1.pdf}
  \includegraphics{results-page-2.pdf}
\end{table}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    tables = [block for block in document.blocks if block.type == "table"]

    assert [block.asset_key for block in tables] == [
        "results-page-1.pdf",
        "results-page-2.pdf",
    ]
    assert tables[0].label == "tab:multi-page"
    assert tables[1].label is None
    assert [block for block in document.blocks if block.type == "figure"] == []


def test_abstract_text_is_excluded_but_evaluated_graphics_are_retained() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\begin{abstract}
This abstract prose is stored separately and must not enter article blocks.
\includegraphics{abstract-overview.png}
\end{abstract}
\section{Body}
Visible body prose.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]
    visible_text = " ".join(block_to_plain(block) for block in document.blocks)

    assert [block.asset_key for block in figures] == ["abstract-overview.png"]
    assert "abstract prose" not in visible_text
    assert "abstract-overview.png" not in visible_text
    assert "Visible body prose" in visible_text


def test_evaluated_zero_and_one_argument_graphics_macros_emit_once_without_path_leak() -> None:
    source = r"""
\documentclass{article}
\newcommand{\logo}{\includegraphics{logo.png}}
\newcommand{\panel}[1]{\includegraphics[width=.5\textwidth]{#1}}
\begin{document}
\section{Body}
Before \logo between \panel{panel.png} after.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]
    visible = " ".join(block_to_plain(block) for block in document.blocks)

    assert [block.asset_key for block in figures] == ["logo.png", "panel.png"]
    assert len(figures) == 2
    assert "Before" in visible and "after" in visible
    assert "includegraphics" not in visible
    assert "logo.png" not in visible
    assert "panel.png" not in visible


def test_section_title_graphics_emit_once_and_preserve_semantic_heading_text() -> None:
    source = r"""
\documentclass{article}
\newcommand{\sectionlogo}{\includegraphics{macro-section-icon.png}}
\begin{document}
\section{Literal \includegraphics{literal-section-icon.png} Results}
Literal body.
\section{Macro \sectionlogo Results}
Macro body.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    headings = [block for block in document.blocks if block.type == "heading"]
    figures = [block for block in document.blocks if block.type == "figure"]
    visible = " ".join(block_to_plain(block) for block in document.blocks)

    assert [heading.title for heading in headings] == ["Literal Results", "Macro Results"]
    assert [block.asset_key for block in figures] == [
        "literal-section-icon.png",
        "macro-section-icon.png",
    ]
    assert len(figures) == 2
    assert "includegraphics" not in visible
    assert "section-icon.png" not in visible


def test_maketitle_evaluates_preamble_title_graphic_once_without_filename_text() -> None:
    source = r"""
\documentclass{article}
\title{Semantic Paper \includegraphics{title-icon.png}}
\begin{document}
\maketitle
\section{Body title}
Visible body.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]
    headings = [block for block in document.blocks if block.type == "heading"]
    visible = " ".join(block_to_plain(block) for block in document.blocks)

    assert [block.asset_key for block in figures] == ["title-icon.png"]
    assert len(figures) == 1
    assert [heading.title for heading in headings] == ["Body title"]
    assert "includegraphics" not in visible
    assert "title-icon.png" not in visible


def test_uninvoked_graphics_macro_and_title_without_maketitle_are_not_evaluated() -> None:
    source = r"""
\documentclass{article}
\newcommand{\unusedlogo}{\includegraphics{unused-logo.png}}
\title{Unused title \includegraphics{unused-title.png}}
\begin{document}
\section{Body}
Visible body.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})

    assert [block for block in document.blocks if block.type == "figure"] == []


def test_nested_graphics_macros_are_evaluated_and_code_is_not_evaluated() -> None:
    source = r"""
\documentclass{article}
\newcommand{\asset}[1]{\includegraphics{#1}}
\newcommand{\wrappedasset}[1]{\asset{#1}}
\begin{document}
\section{Body}
\wrappedasset{nested-panel.png}
\begin{verbatim}
\wrappedasset{literal-code.png}
\end{verbatim}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]
    code = next(block for block in document.blocks if block.type == "code")

    assert [block.asset_key for block in figures] == ["nested-panel.png"]
    assert code.code is not None and "literal-code.png" in code.code


def test_deeply_nested_graphics_macro_fails_instead_of_silently_losing_figure() -> None:
    names = ["macro" + "a" * length for length in range(1, 36)]
    definitions = [
        rf"\newcommand{{\{name}}}{{\{names[index + 1]}}}" for index, name in enumerate(names[:-1])
    ]
    definitions.append(rf"\newcommand{{\{names[-1]}}}{{\includegraphics{{deep-panel.png}}}}")
    source = "\n".join(
        [
            r"\documentclass{article}",
            *definitions,
            r"\begin{document}",
            rf"\{names[0]}",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "macro_expansion_limit"


def test_recursive_graphics_macro_fails_instead_of_silently_losing_figure() -> None:
    source = r"""
\documentclass{article}
\newcommand{\recursiveasset}{\recursiveasset\includegraphics{cycle-panel.png}}
\begin{document}
\recursiveasset
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "macro_expansion_limit"


def test_expandafter_list_accumulator_self_reference_is_not_treated_as_recursion() -> None:
    r"""``\g@addto@macro`` 型のリスト蓄積慣用句は再帰ではなく、fail-closed の対象外であるべき。

    実際の arXiv 論文(``robbyant.cls``)の ``\metadata``/``\addtolist``/``\metadatalist`` は
    ``\xdef\metadatalist{\expandafter{\metadatalist}...}`` という古典的な蓄積パターンを使う。
    実TeXでは ``\expandafter`` が確定前の値を一度だけ展開して差し込むため必ず有限回で終了するが、
    この評価器は ``\expandafter`` の一発展開を再現できず、差し込まれた ``\metadatalist`` を見かけの
    再帰呼び出しと誤認し、論文全体のLaTeX解析を失敗させていた(quality A 失われ PDF fallback)。
    このテストはその慣用句を最小再現し、解析が成功し文書構造(節・本文)が生成されることを確認する。
    """

    source = r"""
\documentclass{article}
\newcommand{\metadatalist}{}
\newcommand{\metadataformat}[1]{#1}
\newcommand{\addtolist}[2]{\xdef#1{\expandafter{#1}#2}}
\newcommand{\metadata}[1]{\addtolist{\metadatalist}{\metadataformat{#1}}}
\begin{document}
\metadata{Alpha}
\metadata{Beta}
\section{Body}
\metadatalist
Visible paragraph text.
\end{document}
"""

    parsed = parse_latex_source("main.tex", {"main.tex": source}).to_document_content()

    visible = "\n".join(block_to_plain(block) for _section, block in parsed.iter_blocks())
    assert "Body" in visible
    assert "Visible paragraph text" in visible
    assert "Beta" in visible


def test_expandafter_wrapped_structural_recursion_still_fails_closed() -> None:
    r"""``\expandafter{...}`` に包まれていても、蓄積慣用句の形と一致しなければ安全側に倒す。

    ``\expandafter{\reclist ...}`` のように、自己参照の直後に ``}`` 以外の内容(ここでは
    ``\includegraphics``)が続く場合は、値の差し込み(splice)ではなく本物の無限再帰の可能性がある
    ため、この判別ロジックの対象外とし、既存の fail-closed 経路(構造到達時は例外)を維持しなければ
    ならない。
    """

    source = r"""
\documentclass{article}
\newcommand{\reclist}{\expandafter{\reclist\includegraphics{cycle-panel.png}}}
\begin{document}
\reclist
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "macro_expansion_limit"


def test_nonstructural_self_reference_used_as_helper_argument_is_safely_bounded() -> None:
    r"""Class size commands pass their own control sequence to ``\@setfontsize``.

    This is not recursive execution and must not make an otherwise parseable paper fail.  A
    recursive macro that reaches figures/sections remains covered by the preceding fail-closed
    test.
    """
    files = {
        "main.tex": r"""
\documentclass{article}
\usepackage{paperstyle}
\begin{document}
\footnotesize Visible body text.
\end{document}
""",
        "paperstyle.sty": r"""
\renewcommand{\footnotesize}{\@setfontsize\footnotesize\@ixpt\@xpt}
""",
    }

    parsed = parse_latex_source("main.tex", files).to_document_content()

    visible = "\n".join(block_to_plain(block) for _section, block in parsed.iter_blocks())
    assert "Visible body text" in visible


def test_class_redefined_size_switches_do_not_leak_into_equation_latex() -> None:
    """Visual size switches must stay presentation-only after class-file evaluation."""
    files = {
        "main.tex": r"""
\documentclass{article}
\usepackage{paperstyle}
\begin{document}
\begin{align}
\scriptsize
  \notag & g_{[n-1]} = \\
\normalsize
  A &= \overline{\Gamma}_{[n-1]}.
\end{align}
\end{document}
""",
        "paperstyle.sty": r"""
\renewcommand{\scriptsize}{\@setfontsize\scriptsize\@viipt\@viiipt}
\renewcommand{\normalsize}{\@setfontsize\normalsize\@xpt\@xiipt}
""",
    }

    parsed = parse_latex_source("main.tex", files).to_document_content()
    latex = "\n".join(
        block.latex or "" for _section, block in parsed.iter_blocks() if block.type == "equation"
    )

    assert "g_{[n-1]}" in latex
    assert "\\overline{\\Gamma}_{[n-1]}" in latex
    assert "@setfontsize" not in latex
    assert "@viipt" not in latex
    assert "@xpt" not in latex


def test_same_macro_nested_in_its_argument_is_not_treated_as_definition_recursion() -> None:
    r"""A macro argument may contain another finite invocation of the same wrapper."""

    source = r"""
\documentclass{article}
\newif\ifhighlightchanges
\highlightchangesfalse
\newcommand{\revised}[1]{\ifhighlightchanges\textcolor{blue}{#1}\else#1\fi}
\begin{document}
\revised{Outer text. \revised{\section{Nested heading} Nested body text.}}
\end{document}
"""

    parsed = parse_latex_source("main.tex", {"main.tex": source}).to_document_content()

    visible = "\n".join(block_to_plain(block) for _section, block in parsed.iter_blocks())
    assert "Nested heading" in visible
    assert "Nested body text" in visible


def test_incomplete_unknown_presentation_macro_fragment_is_discarded() -> None:
    parser = latex_parser._LatexParser()

    inlines = parser._parse_inline(r"\multirow[c]{6}{*}{\rotatebox[origin=c]{90}{")

    assert inlines == []


def test_graphics_macro_output_growth_limit_is_a_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_budget = latex_parser._MacroExpansionBudget
    monkeypatch.setattr(
        latex_parser,
        "_MacroExpansionBudget",
        lambda: original_budget(growth_chars=0),
    )
    source = r"""
\documentclass{article}
\newcommand{\asset}{\includegraphics{growth-panel.png}}
\begin{document}
\asset
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "macro_expansion_limit"


def test_maketitle_uses_latest_fields_and_nested_frontmatter_graphics_in_display_order() -> None:
    source = r"""
\documentclass{article}
\newcommand{\markasset}[1]{\includegraphics{#1}}
\title{Superseded \markasset{superseded-title.png}}
\title{Current \markasset{title-mark.png}\thanks{Thanks \markasset{thanks-mark.png}}}
\author{Authors \markasset{author-mark.png}}
\date{\markasset{date-mark.png}}
\begin{document}
\maketitle
\section{Body}
Visible.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "title-mark.png",
        "thanks-mark.png",
        "author-mark.png",
        "date-mark.png",
    ]


def test_class_defined_maketitle_keeps_frontmatter_and_class_macro_graphics() -> None:
    class_source = r"""
\newcommand{\classbanner}{\includegraphics{class-banner.png}}
\renewcommand{\maketitle}{\classbanner}
"""
    source = r"""
\documentclass{custompaper}
\newcommand{\titleasset}{\includegraphics{title-mark.png}}
\title{Semantic title \titleasset}
\begin{document}
\maketitle
\section{Body}
Visible.
\end{document}
"""

    document = parse_latex_source(
        "main.tex",
        {"custompaper.cls": class_source, "main.tex": source},
    )
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "title-mark.png",
        "class-banner.png",
    ]


def test_source_order_macro_call_renew_call_uses_each_definition_snapshot() -> None:
    source = r"""
\documentclass{article}
\newcommand{\panel}{\includegraphics{first-panel.png}}
\begin{document}
\panel
\renewcommand{\panel}{\includegraphics{second-panel.png}}
\panel
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "first-panel.png",
        "second-panel.png",
    ]


def test_source_order_graphic_to_text_renewal_keeps_only_earlier_figure() -> None:
    source = r"""
\documentclass{article}
\newcommand{\marker}{\includegraphics{early-marker.png}}
\begin{document}
\marker
\renewcommand{\marker}{Text-only marker}
\marker
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]
    visible = " ".join(block_to_plain(block) for block in document.blocks)

    assert [block.asset_key for block in figures] == ["early-marker.png"]
    assert visible.count("Text-only marker") == 1


def test_source_order_maketitle_snapshot_does_not_use_later_renewal() -> None:
    source = r"""
\documentclass{article}
\newcommand{\maketitle}{\includegraphics{first-title-layout.png}}
\title{Title field \includegraphics{title-field.png}}
\begin{document}
\maketitle
\renewcommand{\maketitle}{\includegraphics{second-title-layout.png}}
\maketitle
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "title-field.png",
        "first-title-layout.png",
        "title-field.png",
        "second-title-layout.png",
    ]


def test_source_order_class_preamble_and_body_overrides_apply_at_call_time() -> None:
    class_source = r"\newcommand{\stageasset}{\includegraphics{class-stage.png}}"
    source = r"""
\documentclass{orderedclass}
\renewcommand{\stageasset}{\includegraphics{preamble-stage.png}}
\begin{document}
\stageasset
\renewcommand{\stageasset}{\includegraphics{body-stage.png}}
\stageasset
\end{document}
"""

    document = parse_latex_source(
        "main.tex",
        {"orderedclass.cls": class_source, "main.tex": source},
    )
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "preamble-stage.png",
        "body-stage.png",
    ]


def test_load_order_unloaded_style_cannot_override_loaded_class() -> None:
    source = r"""
\documentclass{loadedclass}
\begin{document}
\loadedasset
\end{document}
"""
    files = {
        "main.tex": source,
        "loadedclass.cls": r"\newcommand{\loadedasset}{\includegraphics{loaded-class.png}}",
        "unused.sty": r"\renewcommand{\loadedasset}{\includegraphics{unused-style.png}}",
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["loaded-class.png"]


def test_load_order_documentclass_and_packages_follow_declaration_not_filename_sort() -> None:
    source = r"""
\documentclass{zbase}
\usepackage{zeta,alpha}
\begin{document}
\orderedasset
\end{document}
"""
    files = {
        "main.tex": source,
        "zbase.cls": r"\newcommand{\orderedasset}{\includegraphics{base-order.png}}",
        "zeta.sty": r"\renewcommand{\orderedasset}{\includegraphics{zeta-order.png}}",
        "alpha.sty": r"\renewcommand{\orderedasset}{\includegraphics{alpha-order.png}}",
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["alpha-order.png"]


def test_load_order_recursive_package_cycle_is_safe_and_preserves_return_order() -> None:
    source = r"""
\documentclass{cyclebase}
\usepackage{cyclea}
\begin{document}
\cycleasset
\end{document}
"""
    files = {
        "main.tex": source,
        "cyclebase.cls": r"\newcommand{\cycleasset}{\includegraphics{cycle-base.png}}",
        "cyclea.sty": (
            r"\RequirePackage{cycleb}"
            r"\renewcommand{\cycleasset}{\includegraphics{cycle-a.png}}"
        ),
        "cycleb.sty": (
            r"\RequirePackage{cyclea}"
            r"\renewcommand{\cycleasset}{\includegraphics{cycle-b.png}}"
        ),
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["cycle-a.png"]


def test_source_order_input_definitions_and_calls_are_evaluated_in_place() -> None:
    source = r"""
\documentclass{article}
\newcommand{\inputasset}{\includegraphics{main-input.png}}
\begin{document}
\input{first-part}
\input{second-part}
\end{document}
"""
    files = {
        "main.tex": source,
        "first-part.tex": (
            r"\renewcommand{\inputasset}{\includegraphics{first-input.png}}\inputasset"
        ),
        "second-part.tex": (
            r"\renewcommand{\inputasset}{\includegraphics{second-input.png}}\inputasset"
        ),
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "first-input.png",
        "second-input.png",
    ]


@pytest.mark.parametrize("loader_command", ["input", "include"])
def test_source_order_unbraced_input_loader_resolves_relative_file_without_leak(
    loader_command: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"\{loader_command} parts/loader-defs\loadedasset",
            r"Visible body text.",
            r"\end{document}",
        ]
    )
    files = {
        "paper/main.tex": source,
        "paper/parts/loader-defs.tex": (
            r"\newcommand{\loadedasset}{\includegraphics{unbraced-loaded.png}}"
        ),
    }

    document = parse_latex_source("paper/main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]
    visible = " ".join(block_to_plain(block) for block in document.blocks)

    assert [block.asset_key for block in figures] == ["unbraced-loaded.png"]
    assert "parts/loader-defs" not in visible


@pytest.mark.parametrize("loader_command", ["input", "include"])
def test_control_symbol_before_loader_text_does_not_execute_loader(
    loader_command: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"\\{loader_command} parts/control-symbol-defs",
            r"\end{document}",
        ]
    )
    files = {
        "main.tex": source,
        "parts/control-symbol-defs.tex": r"\includegraphics{must-not-load.png}",
    }

    document = parse_latex_source("main.tex", files)

    assert [block for block in document.blocks if block.type == "figure"] == []


@pytest.mark.parametrize("source_name", ["parts/defs(1)", "parts/defs~v1=stable"])
def test_unbraced_input_filename_uses_delimiter_boundaries_not_narrow_whitelist(
    source_name: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"\input {source_name}",
            r"\end{document}",
        ]
    )
    files = {
        "main.tex": source,
        f"{source_name}.tex": r"\includegraphics{delimiter-filename.png}",
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["delimiter-filename.png"]


def test_loader_reads_star_as_filename_token_not_part_of_control_word() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\input*star-target
\end{document}
"""
    files = {
        "main.tex": source,
        "*star-target.tex": r"\includegraphics{correct-star-target.png}",
        "star-target.tex": r"\includegraphics{wrong-star-target.png}",
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["correct-star-target.png"]


def test_acyclic_input_depth_limit_raises_stable_filename_free_error() -> None:
    depth = 1_200
    files = {
        "main.tex": (
            r"\documentclass{article}\begin{document}"
            r"\input{deep-source-0000}\end{document}"
        )
    }
    for index in range(depth):
        next_source = (
            rf"\input{{deep-source-{index + 1:04d}}}"
            if index + 1 < depth
            else r"\includegraphics{too-deep.png}"
        )
        files[f"deep-source-{index:04d}.tex"] = next_source

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", files)

    assert caught.value.kind == "source_expansion_limit"
    assert "deep-source" not in str(caught.value)


def test_same_input_source_is_repeatable_after_each_completed_evaluation() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\input{repeatable-part.tex}
\input{repeatable-part.tex}
\end{document}
"""
    files = {
        "main.tex": source,
        "repeatable-part.tex": r"\includegraphics{repeatable-input.png}",
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "repeatable-input.png",
        "repeatable-input.png",
    ]


def test_style_source_is_loaded_once_across_repeated_package_declarations() -> None:
    source = r"""
\documentclass{article}
\usepackage{loadedonce}
\usepackage{loadedonce}
\begin{document}
\maketitle
\end{document}
"""
    files = {
        "main.tex": source,
        "loadedonce.sty": r"\thanks{\includegraphics{loaded-once.png}}",
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["loaded-once.png"]


def test_unresolved_system_class_and_package_remain_nonfatal() -> None:
    source = r"""
\documentclass{systemprovided}
\usepackage{systemprovided}
\begin{document}
Visible body text.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    visible = " ".join(block_to_plain(block) for block in document.blocks)

    assert "Visible body text." in visible
    assert "systemprovided" not in visible


_LATEX_EVALUATION_LIMITS = {
    "_MAX_LATEX_SOURCE_VISITS": 1_000_000,
    "_MAX_LATEX_EVALUATED_CHARS": 100_000_000,
    "_MAX_LATEX_EVALUATED_BYTES": 400_000_000,
    "_MAX_LATEX_EMITTED_CHARS": 100_000_000,
    "_MAX_LATEX_EMITTED_BYTES": 400_000_000,
    "_MAX_LATEX_EVALUATION_OPERATIONS": 10_000_000,
    "_MAX_LATEX_OUTPUT_CHUNKS": 10_000_000,
    "_MAX_LATEX_CONTROL_TOKENS": 10_000_000,
    "_MAX_LATEX_STRUCTURE_MATCHES": 10_000_000,
    "_MAX_LATEX_IR_OBJECTS": 10_000_000,
    "_MAX_LATEX_PARSER_DEPTH": 1_000,
}


def _patch_latex_evaluation_limits(monkeypatch: pytest.MonkeyPatch, **overrides: int) -> None:
    limits = _LATEX_EVALUATION_LIMITS | overrides
    for name, value in limits.items():
        monkeypatch.setattr(latex_parser, name, value, raising=False)


@pytest.mark.parametrize(
    ("input_count", "raises_limit"),
    [(1, False), (10, True), (100, True)],
)
def test_repeated_input_charges_aggregate_evaluated_source_chars(
    monkeypatch: pytest.MonkeyPatch,
    input_count: int,
    raises_limit: bool,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_EVALUATED_CHARS=250_000)
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            *([r"\input{large-part}"] * input_count),
            r"\end{document}",
        ]
    )
    files = {"main.tex": source, "large-part.tex": "x" * 100_000}

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", files)
        assert caught.value.kind == "source_evaluation_limit"
        assert "large-part" not in str(caught.value)
    else:
        document = parse_latex_source("main.tex", files)
        assert document.blocks


@pytest.mark.parametrize(("visit_limit", "raises_limit"), [(5, False), (4, True)])
def test_branching_repeated_input_charges_each_actual_source_visit(
    monkeypatch: pytest.MonkeyPatch,
    visit_limit: int,
    raises_limit: bool,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_SOURCE_VISITS=visit_limit)
    source = r"""
\documentclass{article}
\begin{document}
\input{left-branch}
\input{right-branch}
\end{document}
"""
    files = {
        "main.tex": source,
        "left-branch.tex": r"\input{shared-leaf}",
        "right-branch.tex": r"\input{shared-leaf}",
        "shared-leaf.tex": r"\includegraphics{shared-visit.png}",
    }

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", files)
        assert caught.value.kind == "source_evaluation_limit"
    else:
        document = parse_latex_source("main.tex", files)
        figures = [block for block in document.blocks if block.type == "figure"]
        assert [block.asset_key for block in figures] == [
            "shared-visit.png",
            "shared-visit.png",
        ]


@pytest.mark.parametrize(("emit_limit", "raises_limit"), [(210_000, False), (150_000, True)])
def test_nested_returned_output_is_charged_at_each_emitting_frame(
    monkeypatch: pytest.MonkeyPatch,
    emit_limit: int,
    raises_limit: bool,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_EMITTED_CHARS=emit_limit)
    source = r"""
\documentclass{article}
\begin{document}
\input{emitted-part}
\end{document}
"""
    files = {"main.tex": source, "emitted-part.tex": "y" * 100_000}

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", files)
        assert caught.value.kind == "source_evaluation_limit"
    else:
        assert parse_latex_source("main.tex", files).blocks


@pytest.mark.parametrize(("byte_limit", "raises_limit"), [(7_000, False), (5_000, True)])
def test_emitted_output_budget_counts_utf8_bytes_without_encoding_allocation(
    monkeypatch: pytest.MonkeyPatch,
    byte_limit: int,
    raises_limit: bool,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_EMITTED_CHARS=10_000,
        _MAX_LATEX_EMITTED_BYTES=byte_limit,
    )
    source = r"""
\documentclass{article}
\begin{document}
\input{unicode-part}
\end{document}
"""
    files = {"main.tex": source, "unicode-part.tex": "界" * 1_000}

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", files)
        assert caught.value.kind == "source_evaluation_limit"
    else:
        assert parse_latex_source("main.tex", files).blocks


@pytest.mark.parametrize(
    ("loader", "dependency_name"),
    [
        (r"\documentclass{largeclass}", "largeclass.cls"),
        (r"\usepackage{largepackage}", "largepackage.sty"),
    ],
    ids=["class", "package"],
)
def test_emit_false_class_and_package_sources_charge_evaluated_bytes(
    monkeypatch: pytest.MonkeyPatch,
    loader: str,
    dependency_name: str,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_EVALUATED_CHARS=10_000,
        _MAX_LATEX_EVALUATED_BYTES=2_500,
    )
    source = "\n".join([loader, r"\begin{document}", "Visible body.", r"\end{document}"])
    files = {"main.tex": source, dependency_name: "界" * 1_000}

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", files)

    assert caught.value.kind == "source_evaluation_limit"
    assert dependency_name not in str(caught.value)


@pytest.mark.parametrize(
    "limited_counter",
    [
        "_MAX_LATEX_EVALUATION_OPERATIONS",
        "_MAX_LATEX_OUTPUT_CHUNKS",
        "_MAX_LATEX_CONTROL_TOKENS",
    ],
)
def test_many_small_control_chunks_hit_deterministic_evaluation_limits(
    monkeypatch: pytest.MonkeyPatch,
    limited_counter: str,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, **{limited_counter: 8})
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            *([r"\unknown{}"] * 20),
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


def test_maketitle_frontmatter_cartesian_work_charges_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_EVALUATION_OPERATIONS=1_000,
    )
    source = "\n".join(
        [
            r"\documentclass{article}",
            *([r"\thanks{}"] * 100),
            r"\begin{document}",
            *([r"\maketitle"] * 100),
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


def test_macro_placeholder_expansion_charges_operations_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_EVALUATION_OPERATIONS=50,
    )
    body = "#1" * 100
    source = "\n".join(
        [
            r"\documentclass{article}",
            rf"\newcommand{{\repeatvalue}}[1]{{{body}}}",
            r"\begin{document}",
            r"\repeatvalue{x}",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


def test_evaluation_budget_factory_is_resolved_at_parse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_factory = latex_parser._LatexEvaluationBudget.from_limits
    calls: list[bool] = []

    def replacement_factory(cls: type[object]) -> object:
        calls.append(True)
        return original_factory()

    monkeypatch.setattr(
        latex_parser._LatexEvaluationBudget,
        "from_limits",
        classmethod(replacement_factory),
    )
    source = r"\documentclass{article}\begin{document}Body.\end{document}"

    parse_latex_source("main.tex", {"main.tex": source})

    assert calls == [True]


def test_bibliography_resolution_receives_budget_before_replacement_concat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_resolver = latex_parser._resolve_bibliography
    calls: list[bool] = []

    def guarded_resolver(
        text: str,
        files: dict[str, str],
        *,
        budget: latex_parser._LatexEvaluationBudget | None = None,
    ) -> str:
        calls.append(budget is not None)
        assert budget is not None
        return original_resolver(text, files, budget=budget)

    monkeypatch.setattr(latex_parser, "_resolve_bibliography", guarded_resolver)
    source = r"""
\documentclass{article}
\begin{document}
\bibliography{refs}
\end{document}
"""
    bibliography = r"\begin{thebibliography}{1}\bibitem{x} Entry.\end{thebibliography}"

    parse_latex_source(
        "main.tex",
        {"main.tex": source, "refs.bbl": bibliography},
    )

    assert calls == [True]


@pytest.mark.parametrize("case", ["definition-group", "preserved-invocation"])
def test_large_unsupported_slices_are_reserved_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    class GuardedSource(str):
        reserved_ranges: ClassVar[set[tuple[int, int]]] = set()

        def __getitem__(self, key: object) -> str:
            if isinstance(key, slice):
                start, stop, step = key.indices(len(self))
                if step == 1 and stop - start >= 900:
                    assert (start, stop) in self.reserved_ranges
            return str.__getitem__(self, key)  # type: ignore[index]

    original_evaluated = latex_parser._LatexEvaluationBudget.reserve_evaluated_text
    original_emitted = latex_parser._LatexEvaluationBudget.reserve_emitted_text

    def allow_evaluated(
        budget: latex_parser._LatexEvaluationBudget,
        text: str,
        start: int = 0,
        end: int | None = None,
    ) -> None:
        stop = len(text) if end is None else end
        if isinstance(text, GuardedSource):
            text.reserved_ranges.add((start, stop))
        original_evaluated(budget, text, start, end)

    def allow_emitted(
        budget: latex_parser._LatexEvaluationBudget,
        text: str,
        start: int = 0,
        end: int | None = None,
    ) -> bool:
        stop = len(text) if end is None else end
        if isinstance(text, GuardedSource):
            text.reserved_ranges.add((start, stop))
        return original_emitted(budget, text, start, end)

    monkeypatch.setattr(
        latex_parser._LatexEvaluationBudget,
        "reserve_evaluated_text",
        allow_evaluated,
    )
    monkeypatch.setattr(
        latex_parser._LatexEvaluationBudget,
        "reserve_emitted_text",
        allow_emitted,
    )

    if case == "definition-group":
        definition = rf"\NewDocumentCommand{{\route}}{{m}}{{{'z' * 1_000}}}"
        body = "Visible body."
    else:
        definition = r"\def\route#1,#2{}"
        body = r"\route" + ("{" + "z" * 40 + "}") * 25 + "safe"
    source = GuardedSource(
        "\n".join(
            [
                r"\documentclass{article}",
                definition,
                r"\begin{document}",
                body,
                r"\end{document}",
            ]
        )
    )

    document = parse_latex_source("main.tex", {"main.tex": source})

    assert document.blocks


def test_unsupported_semantic_scan_charges_each_control_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_CONTROL_TOKENS=20,
    )
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\NewDocumentCommand{\route}{m}{" + (r"\noop" * 50) + "}",
            r"\begin{document}",
            r"\route{x}",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


def test_unsupported_semantic_macro_dag_is_memoized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch)
    state = latex_parser._LatexEvaluationState(files={})
    state.macros["leaf"] = latex_parser._MacroDefinition(0, "plain")
    branch_names = [f"branch{letter}" for letter in "abcdefghij"]
    for index in range(len(branch_names) - 1, -1, -1):
        child = "leaf" if index == len(branch_names) - 1 else branch_names[index + 1]
        state.macros[branch_names[index]] = latex_parser._MacroDefinition(
            0,
            rf"\{child}\{child}",
        )
    calls = 0
    original_matches = latex_parser._evaluated_matches

    def counted_matches(
        pattern: re.Pattern[str],
        text: str,
        budget: latex_parser._LatexEvaluationBudget | None = None,
    ) -> Iterator[re.Match[str]]:
        nonlocal calls
        calls += 1
        return original_matches(pattern, text, budget)

    monkeypatch.setattr(latex_parser, "_evaluated_matches", counted_matches)

    assert not latex_parser._source_reaches_document_structure(
        rf"\{branch_names[0]}",
        state,
    )
    assert calls < 100


def test_injected_bibliography_entries_share_structure_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_STRUCTURE_MATCHES=20,
    )
    source = r"\documentclass{article}\begin{document}\bibliography{refs}\end{document}"
    bibliography = (
        r"\begin{thebibliography}{100}"
        + "".join(rf"\bibitem{{ref{index}}} Entry {index}." for index in range(100))
        + r"\end{thebibliography}"
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source(
            "main.tex",
            {"main.tex": source, "refs.bbl": bibliography},
        )

    assert caught.value.kind == "source_evaluation_limit"


@pytest.mark.parametrize(
    "body",
    [
        "".join(rf"\includegraphics{{figure-{index}.png}}" for index in range(10)),
        "\n\n".join(f"paragraph {index}" for index in range(10)),
    ],
    ids=["figures", "paragraphs"],
)
def test_post_evaluation_blocks_share_ir_object_budget(
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_IR_OBJECTS=5)
    source = "\n".join([r"\documentclass{article}", r"\begin{document}", body, r"\end{document}"])

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


@pytest.mark.parametrize(
    "body",
    [
        (r"\begin{center}" * 10) + "text" + (r"\end{center}" * 10),
        ("{" * 10) + "text" + ("}" * 10),
        (r"\textbf{" * 10) + "text" + ("}" * 10),
    ],
    ids=["transparent-environments", "inline-groups", "inline-wrappers"],
)
def test_post_evaluation_recursion_uses_parser_depth_budget(
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_PARSER_DEPTH=8)
    source = "\n".join([r"\documentclass{article}", r"\begin{document}", body, r"\end{document}"])

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


def test_unsupported_argument_frontmatter_is_structural() -> None:
    source = r"""
\documentclass{article}
\def\route#1,#2{}
\begin{document}
\route{\title{x}\title{y}}safe
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"


@pytest.mark.parametrize(("modifier_count", "raises_limit"), [(7, False), (8, True)])
def test_unsupported_argument_spec_token_limit_is_exact(
    monkeypatch: pytest.MonkeyPatch,
    modifier_count: int,
    raises_limit: bool,
) -> None:
    monkeypatch.setattr(latex_parser, "_MAX_UNSUPPORTED_ARGUMENT_SPEC_TOKENS", 8)
    source = "\n".join(
        [
            r"\documentclass{article}",
            rf"\NewDocumentCommand{{\route}}{{{'+' * modifier_count}m}}{{}}",
            r"\begin{document}",
            r"\route{x}safe",
            r"\end{document}",
        ]
    )

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", {"main.tex": source})
        assert caught.value.kind == "unsupported_structural_macro"
    else:
        assert parse_latex_source("main.tex", {"main.tex": source}).blocks


def test_unsupported_argument_layout_is_parsed_once_per_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = latex_parser._unsupported_invocation_argument_layout
    calls = 0

    def counted(
        definition: latex_parser._UnsupportedMacroDefinition,
        budget: latex_parser._LatexEvaluationBudget | None = None,
    ) -> tuple[str, ...] | None:
        nonlocal calls
        calls += 1
        return original(definition, budget)

    monkeypatch.setattr(
        latex_parser,
        "_unsupported_invocation_argument_layout",
        counted,
    )
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\NewDocumentCommand{\route}{m}{}",
            r"\begin{document}",
            *([r"\route{x}"] * 20),
            r"safe",
            r"\end{document}",
        ]
    )

    parse_latex_source("main.tex", {"main.tex": source})

    assert calls == 1


def test_bib_intermediate_entries_and_fields_share_ir_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_IR_OBJECTS=8)
    source = r"""
\documentclass{article}
\begin{document}
\nocite{*}
\bibliography{refs}
\end{document}
"""
    bibliography = "\n".join(
        rf"@article{{ref{index}, author={{Author}}, title={{Title {index}}}, year={{2026}}}}"
        for index in range(20)
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source(
            "main.tex",
            {"main.tex": source, "refs.bib": bibliography},
        )

    assert caught.value.kind == "source_evaluation_limit"


@pytest.mark.parametrize("source_kind", ["bib", "bbl"])
def test_deep_bibliography_markup_is_processed_without_fixed_point_recursion(
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_PARSER_DEPTH=8)
    source = r"""
\documentclass{article}
\begin{document}
\nocite{*}
\bibliography{refs}
\end{document}
"""
    if source_kind == "bib":
        wrapped = ("{" * 300) + "Deep title" + ("}" * 300)
        files = {
            "main.tex": source,
            "refs.bib": rf"@article{{deep, title={{{wrapped}}}, year={{2026}}}}",
        }
    else:
        wrapped = (r"\emph{" * 300) + "Deep entry" + ("}" * 300)
        files = {
            "main.tex": source,
            "refs.bbl": (
                r"\begin{thebibliography}{1}"
                + rf"\bibitem{{deep}} {wrapped}"
                + r"\end{thebibliography}"
            ),
        }

    document = parse_latex_source("main.tex", files)
    references = [block for block in document.blocks if block.type == "reference_entry"]

    assert references
    assert "Deep" in (references[0].raw or "")


def test_equation_labels_share_post_evaluation_structure_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_STRUCTURE_MATCHES=20,
    )
    labels = "".join(rf"\label{{equation-{index}}}" for index in range(50))
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"\begin{{equation}}x{labels}\end{{equation}}",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


@pytest.mark.parametrize(("group_count", "raises_limit"), [(3, False), (4, True)])
def test_post_parser_unknown_command_group_limit_is_exact(
    monkeypatch: pytest.MonkeyPatch,
    group_count: int,
    raises_limit: bool,
) -> None:
    monkeypatch.setattr(latex_parser, "_MAX_UNSUPPORTED_MACRO_GROUPS", 3)
    groups = "".join(rf"{{value-{index}}}" for index in range(group_count))
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"\unknown{groups}",
            r"\end{document}",
        ]
    )

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", {"main.tex": source})
        assert caught.value.kind == "source_evaluation_limit"
    else:
        assert parse_latex_source("main.tex", {"main.tex": source}).blocks


def test_single_citation_key_list_shares_ir_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_IR_OBJECTS=5)
    keys = ",".join(f"key-{index}" for index in range(20))
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"See \cite{{{keys}}}.",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


def test_figure_environment_reserves_ir_before_collecting_all_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_IR_OBJECTS=5)
    original = latex_parser._evaluated_includegraphics_matches
    yielded = 0

    def counted(
        text: str,
        budget: latex_parser._LatexEvaluationBudget | None = None,
    ) -> Iterator[re.Match[str]]:
        matches = original(text, budget)

        def iterator() -> Iterator[re.Match[str]]:
            nonlocal yielded
            for match in matches:
                yielded += 1
                yield match

        return iterator()

    monkeypatch.setattr(latex_parser, "_evaluated_includegraphics_matches", counted)
    figures = "".join(rf"\includegraphics{{asset-{index}.png}}" for index in range(100))
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"\begin{{figure}}{figures}\end{{figure}}",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"
    assert yielded <= 6


def test_environment_depth_budget_stops_boundary_stream_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_PARSER_DEPTH=8)
    original = latex_parser._ENVIRONMENT_BOUNDARY_RE
    yielded = 0

    class CountingPattern:
        @staticmethod
        def finditer(string: str, pos: int = 0, endpos: int = 2147483647) -> Iterator[re.Match[str]]:
            nonlocal yielded
            for match in original.finditer(string, pos, endpos):
                yielded += 1
                yield match

    monkeypatch.setattr(latex_parser, "_ENVIRONMENT_BOUNDARY_RE", CountingPattern())
    names = ["center", "flushleft"] * 500
    opening = "".join(rf"\begin{{{name}}}" for name in names)
    closing = "".join(rf"\end{{{name}}}" for name in reversed(names))
    text = r"\begin{center}" + opening + ("x" * 100_000) + closing + r"\end{center}"
    budget = latex_parser._LatexEvaluationBudget.from_limits()

    with pytest.raises(LatexParseError) as caught:
        latex_parser._read_environment(text, len(r"\begin{center}"), "center", budget)

    assert caught.value.kind == "source_evaluation_limit"
    assert yielded <= 8


def test_inline_brace_depth_budget_stops_before_large_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_PARSER_DEPTH=8)

    class CountingSource(str):
        character_reads: ClassVar[int] = 0

        def __getitem__(self, key: object) -> str:
            if isinstance(key, int):
                type(self).character_reads += 1
            return str.__getitem__(self, key)  # type: ignore[index]

    source = CountingSource(("{" * 130) + ("x" * 100_000) + ("}" * 130))
    parser = latex_parser._LatexParser(
        evaluation_budget=latex_parser._LatexEvaluationBudget.from_limits()
    )

    with pytest.raises(LatexParseError) as caught:
        parser._parse_inline(source)

    assert caught.value.kind == "source_evaluation_limit"
    assert CountingSource.character_reads < 1_000


def test_top_level_and_literal_scanners_do_not_restart_command_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_search(*args: object, **kwargs: object) -> object:
        raise AssertionError("forward scanners must not restart command searches")

    monkeypatch.setattr(latex_parser, "_search_tex_command", forbidden_search)
    body = "\n".join(rf"\section{{Section {index}}} text" for index in range(100))
    nodes = latex_parser._iter_top_level(body)
    literal_source = " ".join([r"\verb|x|"] * 100)
    cursor = 0
    literal_count = 0
    while (region := latex_parser._next_literal_region(literal_source, cursor)) is not None:
        literal_count += 1
        cursor = region[1]

    assert len([node for node in nodes if node[0] == "section"]) == 100
    assert literal_count == 100


def test_environment_prefix_options_share_ir_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_IR_OBJECTS=5)
    options = "[x]" * 20
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            rf"\begin{{tcolorbox}}{options}body\end{{tcolorbox}}",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


@pytest.mark.parametrize(
    ("command", "group_count", "raises_limit"),
    [
        ("setup", 3, False),
        ("setup", 4, True),
        ("loader", 3, False),
        ("loader", 4, True),
        ("frontmatter", 3, False),
        ("frontmatter", 4, True),
    ],
)
def test_evaluator_option_and_setup_group_limits_are_exact(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    group_count: int,
    raises_limit: bool,
) -> None:
    monkeypatch.setattr(latex_parser, "_MAX_UNSUPPORTED_MACRO_GROUPS", 3)
    options = "[x]" * group_count
    if command == "setup":
        preamble = r"\definecolor" + ("{x}" * group_count)
    elif command == "loader":
        preamble = rf"\documentclass{options}{{article}}"
    else:
        preamble = rf"\title{options}{{Visible title}}"
    source = "\n".join(
        [
            preamble,
            r"\begin{document}",
            "Visible body.",
            r"\end{document}",
        ]
    )

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", {"main.tex": source})
        assert caught.value.kind == "source_evaluation_limit"
    else:
        assert parse_latex_source("main.tex", {"main.tex": source}).blocks


def test_bib_value_concatenation_pieces_share_ir_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(monkeypatch, _MAX_LATEX_IR_OBJECTS=100)
    source = r"""
\documentclass{article}
\begin{document}
\nocite{*}
\bibliography{refs}
\end{document}
    """
    title = " # ".join(["{piece}"] * 1_000)
    bibliography = rf"@article{{many, title={title}, year={{2026}}}}"

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source(
            "main.tex",
            {"main.tex": source, "refs.bib": bibliography},
        )

    assert caught.value.kind == "source_evaluation_limit"


def test_bibliography_filename_matching_charges_linear_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_EVALUATION_OPERATIONS=200,
    )
    budget = latex_parser._LatexEvaluationBudget.from_limits()
    names = [f"requested/path-{index}" for index in range(50)]
    files = {f"different/file-{index}.bib": "" for index in range(50)}

    with pytest.raises(LatexParseError) as caught:
        latex_parser._matching_bib_files(names, files, budget)

    assert caught.value.kind == "source_evaluation_limit"


@pytest.mark.parametrize(
    "loader",
    [r"\input missing-loader-source", r"\include{missing-loader-source}"],
    ids=["unbraced-input", "braced-include"],
)
def test_unresolved_author_include_fails_without_filename_leak(loader: str) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            loader,
            r"Visible body text.",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "missing_included_source"
    assert "missing-loader-source" not in str(caught.value)


@pytest.mark.parametrize(
    "class_loader",
    [
        r"\LoadClass{baseclass}",
        r"\LoadClass[draft,twocolumn]{baseclass}",
        r"\LoadClassWithOptions{baseclass}",
    ],
    ids=["load-class", "load-class-options", "load-class-with-options"],
)
def test_loadclass_family_recursively_loads_base_class_at_declaration_position(
    class_loader: str,
) -> None:
    source = r"""
\documentclass{derivedclass}
\begin{document}
\classasset
\end{document}
"""
    files = {
        "main.tex": source,
        "derivedclass.cls": (
            r"\newcommand{\classasset}{\includegraphics{before-base-class.png}}" + class_loader
        ),
        "baseclass.cls": (r"\renewcommand{\classasset}{\includegraphics{base-class-asset.png}}"),
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["base-class-asset.png"]


def test_requirepackagewithoptions_recursively_loads_dependency_at_declaration_position() -> None:
    source = r"""
\documentclass{article}
\usepackage{wrapperpackage}
\begin{document}
\packageasset
\end{document}
"""
    files = {
        "main.tex": source,
        "wrapperpackage.sty": (
            r"\newcommand{\packageasset}{\includegraphics{before-dependency.png}}"
            r"\RequirePackageWithOptions{dependency}"
        ),
        "dependency.sty": (
            r"\renewcommand{\packageasset}{\includegraphics{package-dependency.png}}"
        ),
    }

    document = parse_latex_source("main.tex", files)
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["package-dependency.png"]


def test_unsupported_single_token_mandatory_argument_fails_closed() -> None:
    source = r"""
\documentclass{article}
\newcommand{\asset}{\includegraphics{single-token-asset.png}}
\NewDocumentCommand{\route}{m}{}
\begin{document}
\route\asset
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "single-token-asset.png" not in str(caught.value)


def test_unsupported_multiple_mandatory_single_token_arguments_fail_closed() -> None:
    source = r"""
\documentclass{article}
\newcommand{\asset}{\includegraphics{second-token-asset.png}}
\NewDocumentCommand{\route}{mm}{}
\begin{document}
\route x\asset
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "second-token-asset.png" not in str(caught.value)


def test_unsupported_control_symbol_is_one_mandatory_tex_token() -> None:
    source = r"""
\documentclass{article}
\newcommand{\asset}{\includegraphics{control-symbol-asset.png}}
\NewDocumentCommand{\route}{mm}{}
\begin{document}
\route\#\asset
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "control-symbol-asset.png" not in str(caught.value)


def test_plain_tex_repeat_closes_conditional_in_skipped_branch() -> None:
    # `acl.sty` guards its line-number setup behind a disabled `\newif`
    # branch whose body defines `\fillzeros` with a plain-TeX
    # `\loop ... \ifnum ... \repeat`.  `\repeat` acts as `\fi` for the
    # loop's conditional, so the branch is balanced even though it holds
    # more `\if` than literal `\fi` tokens.
    source = r"""
\documentclass{article}
\newif\ifshowlines
\showlinesfalse
\ifshowlines
  \def\fillzeros#1{\loop\ifnum#1<10 \advance#1 by 1 \repeat}
\fi
\begin{document}
Body text here.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})

    body = "".join(
        inline.v or ""
        for block in document.blocks
        for inline in block.inlines
        if inline.t == "text"
    )
    assert "Body text here." in body


def test_plain_tex_repeat_closes_conditional_in_selected_branch() -> None:
    source = r"""
\documentclass{article}
\newif\ifshowlines
\showlinestrue
\ifshowlines
  \def\fillzeros#1{\loop\ifnum#1<10 \advance#1 by 1 \repeat}
\fi
\begin{document}
Body text here.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})

    body = "".join(
        inline.v or ""
        for block in document.blocks
        for inline in block.inlines
        if inline.t == "text"
    )
    assert "Body text here." in body


def test_control_word_star_is_a_separate_mandatory_tex_token() -> None:
    source = r"""
\documentclass{article}
\newcommand{\asset}{\includegraphics{after-star-asset.png}}
\NewDocumentCommand{\route}{mm}{}
\begin{document}
\route\safe*\asset
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["after-star-asset.png"]


@pytest.mark.parametrize(
    ("definition", "invocation"),
    [
        (r"\newrobustcmd{\route}[1]{}", r"\route\asset"),
        (r"\newcommandx{\route}[2][1=unused]{}", r"\route\asset"),
        (r"\newcommandx{\route}[2][1=unused]{}", r"\route[selected]\asset"),
    ],
    ids=["robust-required", "commandx-default-optional", "commandx-present-optional"],
)
def test_unsupported_family_layout_detects_structural_mandatory_token(
    definition: str,
    invocation: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\newcommand{\asset}{\includegraphics{family-layout-asset.png}}",
            definition,
            r"\begin{document}",
            invocation,
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "family-layout-asset.png" not in str(caught.value)


@pytest.mark.parametrize(
    "definition",
    [
        r"\NewDocumentCommand{\route}{}{}",
        r"\newrobustcmd{\route}{}",
        r"\newcommandx{\route}[0]{}",
    ],
    ids=["xparse", "robust", "commandx"],
)
def test_unsupported_family_zero_arity_never_consumes_following_token(
    definition: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\newcommand{\asset}{\includegraphics{family-zero-asset.png}}",
            definition,
            r"\begin{document}",
            r"\route\asset",
            r"\end{document}",
        ]
    )

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["family-zero-asset.png"]


@pytest.mark.parametrize(
    "invocation",
    [r"\route\asset", r"\route[selected]\asset"],
    ids=["default-optional", "present-optional"],
)
def test_unsupported_optional_then_mandatory_single_token_argument_fails_closed(
    invocation: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\newcommand{\asset}{\includegraphics{optional-route-asset.png}}",
            r"\NewDocumentCommand{\route}{O{fallback}m}{}",
            r"\begin{document}",
            invocation,
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "optional-route-asset.png" not in str(caught.value)


def test_unsupported_zero_argument_does_not_consume_following_structural_macro() -> None:
    source = r"""
\documentclass{article}
\newcommand{\asset}{\includegraphics{zero-argument-following.png}}
\NewDocumentCommand{\route}{}{}
\begin{document}
\route\asset
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == ["zero-argument-following.png"]


@pytest.mark.parametrize(
    "invocation",
    [r"\route\end{document}", r"\route{\end{figure}}"],
    ids=["document-end-token", "figure-end-group"],
)
def test_unsupported_argument_reaching_end_command_fails_closed(
    invocation: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\NewDocumentCommand{\route}{m}{#1}",
            r"\begin{document}",
            invocation,
            r"\includegraphics{must-not-be-truncated.png}",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "must-not-be-truncated.png" not in str(caught.value)


def test_unsupported_bibliography_token_argument_cannot_create_ghost_references() -> None:
    source = r"""
\documentclass{article}
\NewDocumentCommand{\route}{m}{}
\begin{document}
\route\bibliography{refs}
\end{document}
"""
    bibliography = r"""
@article{generic,
  author = {Example Author},
  title = {Synthetic Reference},
  year = {2025}
}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source, "refs.bib": bibliography})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "refs" not in str(caught.value)


@pytest.mark.parametrize(
    "loader_body",
    [
        r"\input{figure-part}",
        r"\include{figure-part}",
        r"\LoadClass{figure-base}",
        r"\RequirePackageWithOptions{figure-package}",
    ],
    ids=["input", "include", "load-class", "require-package-with-options"],
)
def test_unsupported_body_reaching_loader_fails_closed(loader_body: str) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            rf"\NewDocumentCommand{{\route}}{{}}{{{loader_body}}}",
            r"\begin{document}",
            r"\route",
            r"\end{document}",
        ]
    )
    files = {
        "main.tex": source,
        "figure-part.tex": r"\includegraphics{loaded-part.png}",
        "figure-base.cls": r"\includegraphics{loaded-class.png}",
        "figure-package.sty": r"\includegraphics{loaded-package.png}",
    }

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", files)

    assert caught.value.kind == "unsupported_structural_macro"
    assert "loaded-" not in str(caught.value)


@pytest.mark.parametrize(
    ("definition", "invocation"),
    [
        (r"\NewDocumentCommand{\route}{O{\asset}}{#1}", r"\route"),
        (r"\NewDocumentCommand{\route}{>{\asset}m}{}", r"\route{safe}"),
        (r"\newrobustcmd{\route}[1][\asset]{#1}", r"\route"),
        (r"\newcommandx{\route}[1][1=\asset]{#1}", r"\route"),
    ],
    ids=["xparse-default", "xparse-processor", "robust-default", "commandx-default"],
)
def test_unsupported_argument_spec_reaching_structure_fails_closed(
    definition: str,
    invocation: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\newcommand{\asset}{\includegraphics{spec-default-asset.png}}",
            definition,
            r"\begin{document}",
            invocation,
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "spec-default-asset.png" not in str(caught.value)


@pytest.mark.parametrize(
    "inner_definition",
    [
        r"\NewDocumentCommand{\inner}{O{\asset}}{#1}",
        r"\newcommand{\inner}[1][\asset]{#1}",
    ],
    ids=["nested-unsupported-spec", "nested-supported-default"],
)
def test_nested_macro_default_or_spec_reachability_fails_closed(
    inner_definition: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\newcommand{\asset}{\includegraphics{nested-default-asset.png}}",
            inner_definition,
            r"\NewDocumentCommand{\outer}{}{\inner}",
            r"\begin{document}",
            r"\outer",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "nested-default-asset.png" not in str(caught.value)


@pytest.mark.parametrize(
    ("prefix", "invocation"),
    [
        (r"\begin{itemize}", r"\route\item Ghost item.\end{itemize}"),
        (
            r"\begin{figure}\includegraphics{real-figure.png}",
            r"\route\caption{Ghost caption.}\end{figure}",
        ),
        ("", r"\route\[x\]"),
        ("", r"\route$x$"),
    ],
    ids=["item", "caption", "math-control-symbol", "math-shift"],
)
def test_unsupported_semantic_argument_token_fails_closed(
    prefix: str,
    invocation: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\NewDocumentCommand{\route}{m}{}",
            r"\begin{document}",
            prefix,
            invocation,
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "Ghost" not in str(caught.value)


@pytest.mark.parametrize(
    "definition",
    [
        rf"\newrobustcmd{{\route}}[{'9' * 5_000}]{{}}",
        rf"\newcommandx{{\route}}[1][{'9' * 5_000}=\asset]{{}}",
    ],
    ids=["robust-arity", "commandx-optional-index"],
)
def test_unsupported_huge_numeric_spec_raises_stable_latex_error(
    definition: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\newcommand{\asset}{\includegraphics{huge-spec-asset.png}}",
            definition,
            r"\begin{document}",
            r"\route\asset",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "huge-spec-asset.png" not in str(caught.value)


def test_supported_newcommand_huge_arity_raises_stable_latex_error() -> None:
    huge_arity = "9" * 5_000
    source = "\n".join(
        [
            r"\documentclass{article}",
            rf"\newcommand{{\route}}[{huge_arity}]{{}}",
            r"\begin{document}",
            r"Visible body.",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "parse_error"
    assert huge_arity not in str(caught.value)


def test_unknown_unsupported_layout_caps_consecutive_group_consumption() -> None:
    groups = "{}" * 33
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\def\route#1,#2{}",
            r"\begin{document}",
            rf"\route{groups}safe",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"


def test_selectfont_does_not_consume_following_colored_lstinline_tokens() -> None:
    tokens = "".join(r"{\color{depth20}\lstinline{token}}" for _ in range(33))
    source = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            r"\fontfamily{phv}\selectfont " + tokens,
            r"\end{document}",
        ]
    )

    document = parse_latex_source("main.tex", {"main.tex": source}).to_document_content()

    assert "token" in "\n".join(block_to_plain(block) for block in flatten_blocks(document.sections))


@pytest.mark.parametrize(("group_count", "raises_limit"), [(3, False), (4, True)])
def test_unsupported_definition_group_cap_applies_before_collecting_groups(
    monkeypatch: pytest.MonkeyPatch,
    group_count: int,
    raises_limit: bool,
) -> None:
    monkeypatch.setattr(latex_parser, "_MAX_UNSUPPORTED_MACRO_GROUPS", 3, raising=False)
    definition = r"\NewDocumentCommand{\route}" + "[]" * (group_count - 1) + "{}"
    source = "\n".join(
        [
            r"\documentclass{article}",
            definition,
            r"\begin{document}",
            "Visible body.",
            r"\end{document}",
        ]
    )

    if raises_limit:
        with pytest.raises(LatexParseError) as caught:
            parse_latex_source("main.tex", {"main.tex": source})
        assert caught.value.kind == "unsupported_structural_macro"
    else:
        assert parse_latex_source("main.tex", {"main.tex": source}).blocks


@pytest.mark.parametrize(
    ("group_count", "expected_kind"),
    [(3, "parse_error"), (4, "unsupported_structural_macro")],
)
def test_malformed_unsupported_definition_envelope_uses_same_group_cap(
    monkeypatch: pytest.MonkeyPatch,
    group_count: int,
    expected_kind: str,
) -> None:
    monkeypatch.setattr(latex_parser, "_MAX_UNSUPPORTED_MACRO_GROUPS", 3, raising=False)
    definition = r"\NewDocumentCommand{}" + "[]" * (group_count - 1) + "{}"
    source = "\n".join(
        [
            r"\documentclass{article}",
            definition,
            r"\begin{document}",
            "Visible body.",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == expected_kind


def test_unsupported_definition_stops_before_collecting_ten_thousand_groups() -> None:
    definition = r"\NewDocumentCommand{\route}" + "[]" * 9_999 + "{}"
    source = "\n".join(
        [
            r"\documentclass{article}",
            definition,
            r"\begin{document}",
            "Visible body.",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"


@pytest.mark.parametrize("malformed", [False, True], ids=["parsed", "envelope"])
def test_unsupported_definition_group_content_charges_evaluation_budget(
    monkeypatch: pytest.MonkeyPatch,
    malformed: bool,
) -> None:
    group_content = "z" * 1_000
    definition = (
        rf"\NewDocumentCommand{{}}{{{group_content}}}"
        if malformed
        else rf"\NewDocumentCommand{{\route}}{{m}}{{{group_content}}}"
    )
    source = "\n".join(
        [
            r"\documentclass{article}",
            definition,
            r"\begin{document}",
            "Visible body.",
            r"\end{document}",
        ]
    )
    _patch_latex_evaluation_limits(
        monkeypatch,
        _MAX_LATEX_EVALUATED_CHARS=len(source) + 500,
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "source_evaluation_limit"


def test_unsupported_invocation_group_cap_uses_shared_macro_group_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(latex_parser, "_MAX_UNSUPPORTED_MACRO_GROUPS", 3, raising=False)
    source = r"""
\documentclass{article}
\def\route#1,#2{}
\begin{document}
\route{}{}{}{}safe
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"


@pytest.mark.parametrize(
    ("definition", "invocation", "filename"),
    [
        (
            r"\NewDocumentCommand{\panel}{m}{\includegraphics{#1}}",
            r"\panel{xparse-panel.png}",
            "xparse-panel.png",
        ),
        (
            r"\newrobustcmd{\panel}[1]{\includegraphics{#1}}",
            r"\panel{robust-panel.png}",
            "robust-panel.png",
        ),
        (
            r"\newcommandx{\panel}[2][1=unused]{\includegraphics{#2}}",
            r"\panel{ignored}{commandx-panel.png}",
            "commandx-panel.png",
        ),
    ],
    ids=["xparse", "etoolbox", "newcommandx"],
)
def test_unsupported_structural_macro_invocation_fails_closed(
    definition: str,
    invocation: str,
    filename: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            definition,
            r"\begin{document}",
            invocation,
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert filename not in str(caught.value)


def test_ifx_can_compare_against_def_without_creating_a_fake_ifx_macro() -> None:
    source = r"""
\documentclass{article}
\makeatletter
\let\@biblabel\def
\ifx\@biblabel\def
  \ifx\@citess\cite
    \def\@biblabel#1{\@citess{#1}\kern-\labelsep\,}
  \else
    \def\@biblabel#1{[#1]}
  \fi
\fi
\ifx\@citess\cite
  \DeclareRobustCommand{\cite}[1]{\@citess{#1}}
\fi
\makeatother
\begin{document}
\section{Introduction}
Visible body text.
\end{document}
"""

    parsed = parse_latex_source("main.tex", {"main.tex": source})

    content = parsed.to_document_content()
    assert any(
        block.type == "paragraph" and "Visible body text" in block_to_plain(block)
        for _section, block in content.iter_blocks()
    )


def test_ifdefined_guard_does_not_misread_operand_as_a_definition() -> None:
    # A very common preamble guard loads `xparse` on old LaTeX kernels:
    #   \ifdefined\NewDocumentCommand\else\RequirePackage{xparse}\fi
    # `\NewDocumentCommand` here is the definedness operand of `\ifdefined`,
    # not a document command being defined, so it must not be treated as an
    # unsupported structural definition.
    source = r"""
\documentclass{article}
\ifdefined\NewDocumentCommand\else\RequirePackage{xparse}\fi
\begin{document}
\section{Introduction}
Visible body text.
\end{document}
"""

    parsed = parse_latex_source("main.tex", {"main.tex": source})

    content = parsed.to_document_content()
    assert any(
        block.type == "paragraph" and "Visible body text" in block_to_plain(block)
        for _section, block in content.iter_blocks()
    )


def test_let_operands_are_not_invoked_as_recursive_macros() -> None:
    source = r"""
\documentclass{article}
\makeatletter
\def\@walk#1{\ifx\delimiter#1\else\expandafter\@walk\fi}
\let\@walk\relax
\makeatother
\begin{document}
Visible body text.
\end{document}
"""

    parsed = parse_latex_source("main.tex", {"main.tex": source})

    assert any(
        block.type == "paragraph" and "Visible body text" in block_to_plain(block)
        for block in parsed.blocks
    )


def test_structural_citation_redefinition_preserves_citation_ir() -> None:
    package = r"""
\def\@formattedcite#1{\mbox{$^{\hbox{#1}}$}}
\DeclareRobustCommand{\cite}[1]{%
  \@ifnextchar[{\@formattedcite{#1}}{\@formattedcite{#1}}}
"""
    source = r"""
\documentclass{article}
\usepackage{customcite}
\begin{document}
Visible body text \cite{reference-key}.
\end{document}
"""

    parsed = parse_latex_source(
        "main.tex",
        {"main.tex": source, "customcite.sty": package},
    )

    citations = [
        inline for block in parsed.blocks for inline in block.inlines if inline.t == "citation"
    ]
    assert [citation.ref for citation in citations] == ["reference-key"]


def test_unsupported_structural_macro_loaded_from_package_fails_only_when_called() -> None:
    package = r"\newrobustcmd{\packageasset}[1]{\includegraphics{#1}}"
    called_source = r"""
\documentclass{article}
\usepackage{structuralpackage}
\begin{document}
\packageasset{package-panel.png}
\end{document}
"""
    uncalled_source = called_source.replace(
        r"\packageasset{package-panel.png}", "Visible body text."
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source(
            "called.tex",
            {"called.tex": called_source, "structuralpackage.sty": package},
        )
    uncalled = parse_latex_source(
        "uncalled.tex",
        {"uncalled.tex": uncalled_source, "structuralpackage.sty": package},
    )

    assert caught.value.kind == "unsupported_structural_macro"
    assert [block for block in uncalled.blocks if block.type == "figure"] == []
    assert "package-panel.png" not in " ".join(block_to_plain(block) for block in uncalled.blocks)


def test_unsupported_structural_macro_nested_dependency_fails_closed() -> None:
    source = r"""
\documentclass{article}
\newcommand{\innerasset}[1]{\includegraphics{#1}}
\NewDocumentCommand{\outerasset}{m}{\innerasset{#1}}
\begin{document}
\outerasset{nested-unsupported.png}
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "nested-unsupported.png" not in str(caught.value)


def test_unsupported_structural_macro_graphics_argument_fails_closed() -> None:
    source = r"""
\documentclass{article}
\NewDocumentCommand{\passthrough}{m}{#1}
\begin{document}
\passthrough{\includegraphics{argument-panel.png}}
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "argument-panel.png" not in str(caught.value)


def test_unsupported_structural_macro_delimited_def_arguments_fail_closed() -> None:
    source = r"""
\documentclass{article}
\def\delimitedasset#1,#2{\includegraphics{#2}}
\begin{document}
\delimitedasset ignored,delimited-panel.png
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "delimited-panel.png" not in str(caught.value)


def test_unsupported_structural_macro_malformed_definition_never_executes_body() -> None:
    source = r"""
\documentclass{article}
\NewDocumentCommand{}{m}{\includegraphics{malformed-ghost.png}}
\begin{document}
Visible body.
\end{document}
"""

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "malformed-ghost.png" not in str(caught.value)


@pytest.mark.parametrize(
    "definition",
    [
        r"\NewDocumentCommand{\maketitle}{}{\includegraphics{unsupported-title.png}}",
        r"\newrobustcmd{\maketitle}{\includegraphics{unsupported-title.png}}",
    ],
    ids=["xparse", "etoolbox"],
)
def test_unsupported_structural_macro_maketitle_fails_at_invocation(
    definition: str,
) -> None:
    source = "\n".join(
        [
            r"\documentclass{article}",
            definition,
            r"\title{Semantic title}",
            r"\begin{document}",
            r"\maketitle",
            r"\end{document}",
        ]
    )

    with pytest.raises(LatexParseError) as caught:
        parse_latex_source("main.tex", {"main.tex": source})

    assert caught.value.kind == "unsupported_structural_macro"
    assert "unsupported-title.png" not in str(caught.value)


def test_unsupported_structural_macro_later_supported_renewal_replaces_risk() -> None:
    source = r"""
\documentclass{article}
\NewDocumentCommand{\switchasset}{m}{\includegraphics{#1}}
\renewcommand{\switchasset}[1]{Text marker #1}
\begin{document}
\switchasset{safe-value}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    visible = " ".join(block_to_plain(block) for block in document.blocks)

    assert [block for block in document.blocks if block.type == "figure"] == []
    assert "Text marker safe-value" in visible


def test_unsupported_structural_macro_in_unloaded_style_has_no_effect() -> None:
    source = r"""
\documentclass{article}
\begin{document}
Visible body text.
\end{document}
"""
    unused = r"\NewDocumentCommand{\unusedasset}{m}{\includegraphics{#1}}"

    document = parse_latex_source(
        "main.tex",
        {"main.tex": source, "unused-structural.sty": unused},
    )

    assert [block for block in document.blocks if block.type == "figure"] == []


def test_maketitle_text_inside_literal_environment_does_not_evaluate_frontmatter() -> None:
    source = r"""
\documentclass{article}
\title{Unused \includegraphics{literal-title.png}}
\begin{document}
\section{Body}
\begin{lstlisting}
\maketitle
\end{lstlisting}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})

    assert [block for block in document.blocks if block.type == "figure"] == []
    code = next(block for block in document.blocks if block.type == "code")
    assert code.code is not None and "\\maketitle" in code.code


def test_body_frontmatter_graphics_are_evaluated_at_maketitle_in_field_order() -> None:
    source = r"""
\documentclass{article}
\newcommand{\fieldasset}[1]{\includegraphics{#1}}
\begin{document}
\title{Body title \verb|\includegraphics{literal-title-code.png}| \fieldasset{body-title.png}}
\author{Body author \fieldasset{body-author.png}}
\date{\fieldasset{body-date.png}}
\maketitle
\title{Not displayed \fieldasset{after-maketitle.png}}
\section{Body}
Visible.
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [block.asset_key for block in figures] == [
        "body-title.png",
        "body-author.png",
        "body-date.png",
    ]


def test_inline_verb_graphics_are_code_not_figures() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Body}
Literal \verb|\includegraphics{inline-fake.png}| and
\verb*+\includegraphics{starred-fake.png}+ remain code.
\includegraphics{evaluated-panel.png}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    figures = [block for block in document.blocks if block.type == "figure"]
    code_values = [
        inline.v
        for block in document.blocks
        for inline in block.inlines
        if inline.t == "code_inline"
    ]

    assert [block.asset_key for block in figures] == ["evaluated-panel.png"]
    assert code_values == [
        r"\includegraphics{inline-fake.png}",
        r"\includegraphics{starred-fake.png}",
    ]


def test_archive_comment_stripping_preserves_percent_inside_inline_verb() -> None:
    source = r"""\documentclass{article}
\begin{document}
\section{Body}
Literal \verb|% \includegraphics{comment-fake.png}| remains code.
\includegraphics{evaluated-after-code.png}
\end{document}
"""

    document = parse_arxiv_latex(gzip.compress(source.encode()))
    figures = [block for block in document.blocks if block.type == "figure"]
    code_values = [
        inline.v
        for block in document.blocks
        for inline in block.inlines
        if inline.t == "code_inline"
    ]

    assert [block.asset_key for block in figures] == ["evaluated-after-code.png"]
    assert code_values == [r"% \includegraphics{comment-fake.png}"]


def test_literal_top_level_inline_verb_never_creates_sections_environments_or_figures() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Real section}
Before \verb|\begin{figure}\includegraphics{inline-fake.png}\end{figure}| after.
\verb*+\begin{center}\section{Fake section}\includegraphics{starred-fake.png}\end{center}+
\includegraphics{real-panel.png}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    headings = [block for block in document.blocks if block.type == "heading"]
    figures = [block for block in document.blocks if block.type == "figure"]
    code_values = [
        inline.v
        for block in document.blocks
        for inline in block.inlines
        if inline.t == "code_inline"
    ]

    assert [(heading.number, heading.title) for heading in headings] == [("1", "Real section")]
    assert [figure.asset_key for figure in figures] == ["real-panel.png"]
    assert code_values == [
        r"\begin{figure}\includegraphics{inline-fake.png}\end{figure}",
        r"\begin{center}\section{Fake section}\includegraphics{starred-fake.png}\end{center}",
    ]


def test_literal_top_level_fake_environment_end_markers_do_not_close_real_environments() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\section{Before literal}
\begin{figure}
\verb|\end{figure}\includegraphics{figure-fake.png}|
\includegraphics{real-inside-figure.png}
\end{figure}
\verb|\end{document}\section{Document fake}|
\section{After literal}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    headings = [block for block in document.blocks if block.type == "heading"]
    figures = [block for block in document.blocks if block.type == "figure"]

    assert [(heading.number, heading.title) for heading in headings] == [
        ("1", "Before literal"),
        ("2", "After literal"),
    ]
    assert [figure.asset_key for figure in figures] == ["real-inside-figure.png"]


@pytest.mark.parametrize(
    ("begin", "end"),
    [
        (r"\begin{verbatim}", r"\end{verbatim}"),
        (r"\begin{lstlisting}", r"\end{lstlisting}"),
        (r"\begin{minted}{tex}", r"\end{minted}"),
    ],
    ids=["verbatim", "lstlisting", "minted"],
)
def test_literal_top_level_code_environments_have_no_structural_side_effects(
    begin: str,
    end: str,
) -> None:
    source = rf"""
\documentclass{{article}}
\begin{{document}}
\section{{Before code}}
{begin}
\begin{{figure}}\includegraphics{{environment-fake.png}}\end{{figure}}
\begin{{center}}\section{{Fake section}}\end{{center}}
{end}
\section{{After code}}
\includegraphics{{real-after-code.png}}
\end{{document}}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    headings = [block for block in document.blocks if block.type == "heading"]
    figures = [block for block in document.blocks if block.type == "figure"]
    code = next(block for block in document.blocks if block.type == "code")

    assert [(heading.number, heading.title) for heading in headings] == [
        ("1", "Before code"),
        ("2", "After code"),
    ]
    assert [figure.asset_key for figure in figures] == ["real-after-code.png"]
    assert code.code is not None
    assert r"\section{Fake section}" in code.code
    assert r"\includegraphics{environment-fake.png}" in code.code


@pytest.mark.parametrize(
    ("begin", "end"),
    [
        (r"\begin{verbatim}", r"\end{verbatim}"),
        (r"\begin{lstlisting}", r"\end{lstlisting}"),
        (r"\begin{minted}{tex}", r"\end{minted}"),
    ],
    ids=["verbatim", "lstlisting", "minted"],
)
def test_literal_environment_macro_definitions_are_preserved_but_never_evaluated(
    begin: str,
    end: str,
) -> None:
    source = rf"""
\documentclass{{article}}
\begin{{document}}
\section{{Body}}
{begin}
\newcommand{{\codeasset}}{{\includegraphics{{literal-definition.png}}}}
\codeasset
{end}
\codeasset
\end{{document}}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    code = next(block for block in document.blocks if block.type == "code")

    assert [block for block in document.blocks if block.type == "figure"] == []
    assert code.code is not None
    assert r"\newcommand{\codeasset}" in code.code
    assert r"\includegraphics{literal-definition.png}" in code.code


def test_table_keeps_tabular_latex_source() -> None:
    doc = _doc()
    tbl = next(b for b in doc.blocks if b.type == "table")
    assert tbl.label == "tab:results"
    assert tbl.raw is not None
    assert "\\begin{tabular}" in tbl.raw
    assert "Ours & 0.99" in tbl.raw


def test_tblr_table_produces_populated_tabular_grid() -> None:
    """tabularray の `tblr` は classic tabular と同じ grid 表現に落ちる(arXiv 2607.07534 再現)。"""
    source = r"""
\documentclass{article}
\begin{document}
\section{Results}
\begin{table}[t]
    \small\centering
    \caption{Comparison with recent interactive world models.}
    \label{tab:comparison}
    \SetTblrInner{rowsep=1.2pt}
    \SetTblrInner{colsep=4.6pt}
    \definecolor{linegray}{HTML}{BDBDBD}
    \definecolor{bg_purple}{HTML}{6A67F3}
    \begin{tblr}{
        cells={halign=l,valign=m},
        column{1}={bg=white},
        column{7}={bg=bg_purple, fg=white},
        hline{2}={0.5pt, fg=linegray},
    }
    \ & \textbf{M-G 3.0}~\cite{matrix3} & \textbf{D-W} & \textbf{LingBot} & \textbf{Happy} & \textbf{Genie 3} & \textbf{Ours} \\
    Generation Duration & Minutes & Minutes & Minutes & Minutes & Minutes & Hours (Infinite) \\
    Semantic Interaction & None & None & None & Few & Few & Infinite \\
    Domain & Game & General & General & General & General & General \\
    Dynamic Degree & Medium & Medium & High & Medium & Medium & High \\
    Real-time & \ding{51} & \ding{51} & \ding{51} & \ding{51} & \ding{51} & \ding{51} \\
    Open-source & \ding{51} & \ding{51} & \ding{51} & \ding{55} & \ding{55} & \ding{51} \\
    \end{tblr}
\end{table}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    tbl = next(b for b in document.blocks if b.type == "table")

    assert tbl.label == "tab:comparison"
    assert "Comparison with recent interactive world models" in block_to_plain(tbl)
    assert tbl.raw is not None
    assert "\\begin{tabular}" in tbl.raw
    assert "Generation Duration" in tbl.raw
    assert "Hours (Infinite)" in tbl.raw

    grid = parse_table_grid(tbl.raw)
    assert grid.supported
    assert len(grid.rows) == 7
    assert all(len(row) == 7 for row in grid.rows)
    flat = [cell.source for row in grid.rows for cell in row]
    assert "Generation Duration" in flat
    assert "Hours (Infinite)" in flat
    assert "Ours" in flat


def test_longtblr_table_produces_populated_grid() -> None:
    """`longtblr` も `tblr` と同じ options-skip 経路で grid 化される。"""
    source = r"""
\documentclass{article}
\begin{document}
\section{Results}
\begin{table}[t]
    \caption{Long variant.}
    \label{tab:long}
    \begin{longtblr}[caption={}]{
        colspec={X[1] X[1]},
        row{1}={bg=white},
    }
    Method & Score \\
    Baseline & 0.50 \\
    Ours & 0.99 \\
    \end{longtblr}
\end{table}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    tbl = next(b for b in document.blocks if b.type == "table")

    assert tbl.label == "tab:long"
    assert tbl.raw is not None
    assert "\\begin{tabular}" in tbl.raw

    grid = parse_table_grid(tbl.raw)
    assert grid.supported
    assert len(grid.rows) == 3
    flat = [cell.source for row in grid.rows for cell in row]
    assert "Baseline" in flat
    assert "Ours" in flat


def test_tblr_setcell_prefix_is_stripped_without_losing_cell_text() -> None:
    """`\\SetCell{...}` の先頭コマンドは除去し、セル本文自体は保持する。"""
    source = r"""
\documentclass{article}
\begin{document}
\section{Results}
\begin{table}[t]
    \caption{SetCell variant.}
    \label{tab:setcell}
    \begin{tblr}{colspec={ll}}
    \SetRow{bg=white} Method & Score \\
    Baseline & \SetCell{bg=red}Failing \\
    Ours & \SetCell[c]{bg=green,fg=white}0.99 \\
    \end{tblr}
\end{table}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    tbl = next(b for b in document.blocks if b.type == "table")

    assert tbl.raw is not None
    assert "\\SetCell" not in tbl.raw
    assert "\\SetRow" not in tbl.raw

    grid = parse_table_grid(tbl.raw)
    assert grid.supported
    flat = [cell.source for row in grid.rows for cell in row]
    assert "Failing" in flat
    assert "0.99" in flat
    assert "Method" in flat


def test_tblr_malformed_options_degrade_to_caption_only_without_raising() -> None:
    """options group が閉じていない tblr は caption-only へ安全に劣化する。"""
    source = r"""
\documentclass{article}
\begin{document}
\section{Results}
\begin{table}[t]
    \caption{Broken options.}
    \label{tab:broken}
    \begin{tblr}{
        cells={halign=l,valign=m
    Method & Score \\
    Ours & 0.99 \\
    \end{tblr}
\end{table}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    tbl = next(b for b in document.blocks if b.type == "table")

    assert tbl.label == "tab:broken"
    assert "Broken options" in block_to_plain(tbl)
    assert tbl.raw is None


def test_list_ordered_flag_and_items() -> None:
    doc = _doc()
    lst = next(b for b in doc.blocks if b.type == "list")
    assert lst.ordered is False  # itemize
    assert len(lst.items) == 2
    assert any(il.t == "ref" for item in lst.items for il in item)


def test_theorem_title_and_label() -> None:
    doc = _doc()
    thm = next(b for b in doc.blocks if b.type == "theorem")
    assert thm.title == "Theorem 1"
    assert thm.label == "thm:main"


def test_theorem_preserves_double_dollar_display_math_as_one_inline() -> None:
    source = r"""
\documentclass{article}
\begin{document}
\begin{proposition}[Key inequality]
Let $c_i$ be constants. For every $n \geq 0$,
$$ c_n \geq \exp\left(-\sum_{i=0}^{n-1} c_i\right). $$
\end{proposition}
\end{document}
"""

    document = parse_latex_source("main.tex", {"main.tex": source})
    theorem = next(block for block in document.blocks if block.type == "theorem")
    maths = [inline.v for inline in theorem.inlines if inline.t == "math_inline"]

    assert maths == [
        "c_i",
        r"n \geq 0",
        r"c_n \geq \exp\left(-\sum_{i=0}^{n-1} c_i\right).",
    ]
    assert all(value for value in maths)


def test_code_and_algorithm_content() -> None:
    doc = _doc()
    code = next(b for b in doc.blocks if b.type == "code")
    assert code.code == "pip install rectified-flow"
    alg = next(b for b in doc.blocks if b.type == "algorithm")
    assert alg.label == "alg:sampling"
    cap = " ".join(il.v for il in alg.caption)
    assert "Rectified Flow Sampling" in cap
    body = " ".join(il.v for il in alg.inlines)
    assert "range(N)" in body
    assert "\\begin" not in body
    assert "\\STATE" not in body


def test_quote_block_present() -> None:
    doc = _doc()
    quote = next(b for b in doc.blocks if b.type == "quote")
    text = " ".join(il.v for il in quote.inlines if il.t == "text")
    assert "shortest paths" in text


def test_url_inline_keeps_href() -> None:
    doc = _doc()
    urls = [il for b in doc.blocks for il in b.inlines if il.t == "url"]
    assert any(il.href == "https://github.com/gnobitab/RectifiedFlow" for il in urls)


def test_footnote_ref_and_collected_block() -> None:
    doc = _doc()
    sec1 = next(s for s in doc.sections if s.id == "sec-1")
    para = next(b for b in sec1.blocks if b.type == "paragraph")
    fn_ref = next(il for il in para.inlines if il.t == "footnote_ref")
    assert fn_ref.ref == "footnote1"
    fn_block = next(b for b in sec1.blocks if b.type == "footnote")
    assert fn_block.label == "footnote1"
    text = " ".join(il.v for il in fn_block.inlines if il.t == "text")
    assert "causal dynamics" in text


def test_blank_line_inside_footnote_argument_is_not_a_paragraph_boundary() -> None:
    parsed = parse_latex_source(
        "main.tex",
        {
            "main.tex": r"""
\documentclass{article}
\begin{document}
Visible body\footnote{First footnote paragraph.

Second footnote paragraph.} after the note.
\end{document}
"""
        },
    ).to_document_content()

    paragraph = next(block for _section, block in parsed.iter_blocks() if block.type == "paragraph")
    assert any(inline.t == "footnote_ref" for inline in paragraph.inlines)
    footnote = next(block for _section, block in parsed.iter_blocks() if block.type == "footnote")
    assert "First footnote paragraph" in block_to_plain(footnote)
    assert "Second footnote paragraph" in block_to_plain(footnote)


def test_reference_structuring_from_thebibliography() -> None:
    doc = _doc()
    refs = {b.label: (b.structured or {}) for b in doc.references}
    assert refs["liu2022flow"]["arxiv_id"] == "2209.03003"
    assert refs["liu2022flow"]["year"] == "2022"
    assert "Flow Straight and Fast" in refs["liu2022flow"]["title"]
    assert refs["song2020ddpm"]["year"] == "2020"
    assert refs["song2020ddpm"]["doi"].startswith("10.48550")


def test_reference_raw_text_strips_emph_markup_for_display() -> None:
    doc = _doc()
    ref = next(b for b in doc.references if b.label == "liu2022flow")
    assert "\\emph" not in (ref.raw or "")
    assert "Flow Straight and Fast" in (ref.raw or "")


def test_bibliography_resolved_from_bbl_file() -> None:
    """`\\bibliography{}` (BibTeX 外部)→ 同梱 `.bbl` の thebibliography を採用する。"""
    doc = parse_arxiv_latex(_BBL_TAR_GZ.read_bytes())
    refs = {b.label: (b.structured or {}) for b in doc.references}
    assert "ext2021" in refs
    assert refs["ext2021"]["year"] == "2021"
    para = next(b for b in doc.blocks if b.type == "paragraph")
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "ext2021"


def test_bibliography_resolved_from_bib_file_and_multi_optional_cite() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{M}"
                "See \\citep[see][Sec.~2]{zhao2026towards} for details."
                "\\bibliography{refs}\\end{document}"
            ),
            "refs.bib": """
                @inproceedings{zhao2026towards,
                  author = {Zhao, Alice and Smith, Bob},
                  title = {Towards Better Reference Extraction},
                  booktitle = {Proceedings of Tests},
                  year = {2026},
                  eprint = {2601.01234},
                  archivePrefix = {arXiv}
                }
            """,
        },
    )
    refs = {b.label: b for b in doc.references}
    assert "zhao2026towards" in refs
    assert "Towards Better Reference Extraction" in (refs["zhao2026towards"].raw or "")
    para = next(b for b in doc.blocks if b.type == "paragraph")
    citation = next(il for il in para.inlines if il.t == "citation")
    assert citation.ref == "zhao2026towards"


def test_inline_parser_treats_latex_linebreak_and_control_space_as_space() -> None:
    doc = parse_latex_source(
        "main.tex",
        {
            "main.tex": (
                "\\documentclass{article}\\begin{document}\\section{Prompt}"
                r"Please describe this video in detail. Include: \ 1. The main subject \\ 2. The environment."
                "\\end{document}"
            )
        },
    )
    para = next(b for b in doc.blocks if b.type == "paragraph")
    text = block_to_plain(para)
    assert "\\" not in text
    assert "Include: 1. The main subject 2. The environment." in text


# ============================ carryover(既存基盤の再利用確認) ============================


def test_carryover_identical_document_keeps_all_ids() -> None:
    v1 = _doc()
    old = flatten_blocks(v1.sections)
    v2 = _doc()
    stats = carry_over_ids(old, v2.sections)
    assert stats.total == stats.carried
    assert stats.carried_ratio == 1.0
    assert [b.id for b in flatten_blocks(v2.sections)] == [b.id for b in old]


def test_carryover_edit_same_count_by_order() -> None:
    base = {
        "main.tex": (
            "\\documentclass{article}\\begin{document}\\section{Intro}\n\n"
            "First paragraph about rectified flow methods.\n\n"
            "Second paragraph describing the ODE dynamics carefully.\n\n"
            "Third paragraph with experimental results here.\n\n"
            "\\end{document}"
        )
    }
    v1 = parse_latex_source("main.tex", base)
    old = flatten_blocks(v1.sections)
    edited = dict(base)
    edited["main.tex"] = base["main.tex"].replace(
        "Second paragraph describing the ODE dynamics carefully.",
        "Second paragraph describing the ODE dynamics very carefully.",
    )
    v2 = parse_latex_source("main.tex", edited)
    stats = carry_over_ids(old, v2.sections)
    new = flatten_blocks(v2.sections)
    assert stats.by_order >= 1
    assert new[1].id == old[1].id


# ============================ document IR 再利用 ============================


def test_to_document_content_roundtrip() -> None:
    doc = _doc()
    content = doc.to_document_content()
    assert isinstance(content, DocumentContent)
    assert content.quality_level == "A"
    assert len(content.iter_blocks()) == len(doc.blocks)
