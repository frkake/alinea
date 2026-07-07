#!/usr/bin/env python3
"""トレーサビリティ検査(plans/12 §6・§16)。

このツールは 2 つの機械検査を 1 コマンドで行う:

1. **件数突合**(plans/12 §16 第1項): docs/00〜12 の受け入れ基準チェックリスト
   (`- [ ]` / `- [x]`)の件数が plans/12 §6 の対応表(`### 6.N docs/NN`)の行数と
   一致し、REV 以外の全行に自動テスト ID が割り当てられていること。

2. **テスト ID の実在**(M0-38 で導入・M1-23 で M1 対応に拡張): §6 が参照する
   「M0/M1 対象」のテスト ID(PY-* / HP-* / VT-*)が、リポジトリ内の実テストファイルに
   docstring/コメントタグとして実在すること。M0/M1 スコープ外(plans/13 §7 で M2/M3
   タスクに割付済み)の ID は `DEFERRED` レジストリで明示し、検査対象から外す(理由付き)。

   PW-* / XT-* / VR-* は E2E・VR(M0-39・M1-24 などが並行/後続で作成中)のため
   **ソフト扱い**: 未実装でも FAIL させず警告のみ。SM-*(実 LLM スモーク)/ PF-*
   (性能)/ REV-*(手動レビュー)は自動テストではないため情報表示のみ。

終了コード: M0/M1 対象 ID の未割付・未実装が 0、かつ件数突合が成立していれば 0。
それ以外は 1(CI ゲート)。

依存: 標準ライブラリのみ。実行: `uv run python tools/traceability/check.py [--verbose]`。
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANS_12 = REPO_ROOT / "plans" / "12-testing.md"
DOCS_DIR = REPO_ROOT / "docs"

# --- テスト ID 語彙 --------------------------------------------------------
# 認識するプレフィックス。番号部は数字 or 英数字(VR-1a など)。
ID_TOKEN = re.compile(r"(?:PY|HP|VT|PW|XT|VR|SM|PF|REV)-[0-9A-Za-z]+(?:-[0-9A-Za-z]+)?")

# レイヤ分類。
HARD_PREFIXES = ("PY", "HP", "VT")  # M0/M1 自動テスト(pytest/hypothesis/vitest)= 必達
SOFT_PREFIXES = ("PW", "XT", "VR")  # E2E / 拡張E2E / VR = M0-39・M1-24 で並行/後続作成中(ソフト)
INFO_PREFIXES = ("SM", "PF", "REV")  # スモーク / 性能 / 手動レビュー = 自動ゲート対象外

# --- M0/M1 スコープ外(deferred)レジストリ ---------------------------------
# plans/12 §6 は M0〜M3 全体の受け入れ基準を対象とする。本ツールの M0/M1 ゲートは
# 「M0/M1 で実装される機能のテスト」だけを必達とする(M1-23 で M1 分を追加)。割付の正は
# plans/13-work-breakdown.md §7(受け入れテストのタスク割付表)。以下は同表で M2/M3
# タスクに割付済み(= M0/M1 では未実装)と判定した ID。各行に理由を明記。
#
# 家族単位(そのプレフィックス配下の全 ID を defer):
DEFERRED_FAMILIES: dict[str, str] = {
    "PY-VOC": "M2 語彙帳(docs/11・docs/10 AC-10-10。plans/13 §7 M2-11)",
    "PY-RES": "M2 リソース(docs/12。plans/13 §7 M2-13)",
    "PY-ART": "M2 記事生成(docs/07・AC-10-08。plans/13 §7 M2-03/M2-08)",
    "PY-FIG": "M2 概要図/解説図(docs/07・AC-10-08。plans/13 §7 M2-05/M2-06)",
    "PY-COL": "M2 コレクション(docs/06・AC-10-09。plans/13 §7 M2-09)",
    "PY-SHR": "M2 共有ページ(AC-10-09。plans/13 §7 M2-10)",
    "VT-VOC": "M2 語彙帳UI(plans/13 §7 M2-12)",
}
# 個別 ID 単位(家族の中で当該項目だけ M2/M3。M1-23 で PY-EXP/NTF/ANN/NOTE/GLS/SRCH/
# ING-04・07/PARSE-03/LIB-05・06 および VT-VIEW-08/VT-LIB-03/VT-XTU-02・03 の M1 実装分を
# HARD(必達・非 deferred)へ昇格した):
DEFERRED_IDS: dict[str, str] = {
    "PY-DB-07": "M1/M2 テーブル制約(vocab_entries。plans/13 §7 は PY-VOC 経由で M2-11)",
    "PY-DB-08": "M2 テーブル制約(resource_links。plans/13 §7 は PY-RES 経由で M2-13)",
    "PY-DB-09": (
        "M1 テーブル制約(notifications)。M1-07 で機能実装済みだが制約自体の専用テストは"
        "未実装(M1-23 の対象 ID 一覧に含まれないため本ツールでは継続 defer)"
    ),
    "PY-DB-10": "M2 テーブル制約(collection_share_tokens。plans/13 §7 M2-10)",
    "PY-DB-11": "M2 テーブル制約(articles。plans/13 §7 M2-03 系)",
    "PY-DB-12": "M2 テーブル制約(overview/explainer_figures。plans/13 §7 M2-05/M2-06)",
    "PY-PARSE-02": "M2 LaTeX パーサ(AC-10-11。plans/13 §7 M2-01)",
    "PY-EXP-03": "M2 CSV エクスポート(plans/13 §7 M2-15)",
    "PY-EXP-04": "M2 全量 JSON エクスポート(plans/13 §7 M2-15)",
    "PY-LIB-07": "M2 保存フィルタ(docs/06。plans/13 §7 M2-14)",
    "VT-VIEW-13": "M2 概要図フレーム(docs/07。plans/13 §7 M2-07)",
    "VT-VIEW-14": "M2 記事メタ行(docs/07。plans/13 §7 M2-07)",
    "VT-VIEW-15": "M2 記事ブロックホバー(docs/07。plans/13 §7 M2-07)",
    "VT-VIEW-16": "M2 議論リスト(docs/07。plans/13 §7 M2-07)",
    "VT-VIEW-17": "M2 リソースカード(docs/12。plans/13 §7 M2-13)",
    "VT-VIEW-18": "M2 リソースタブバッジ(docs/12。plans/13 §7 M2-13)",
    "VT-VIEW-19": "M2 リソース『開く ↗』(docs/12。plans/13 §7 M2-13)",
    "HP-05": "M2 SVG 決定性(概要図・docs/09 AC-09-10。plans/13 §7 M2-05)",
}

# §6 サブセクション見出し → docs ファイル prefix の対応(件数突合用)。
SECTION_HEADING = re.compile(r"^###\s+6\.\d+\s+(docs/\d+)")


def _family(test_id: str) -> str:
    """`PY-DB-05` → `PY-DB`、`VR-1a` → `VR`。"""
    parts = test_id.split("-")
    if len(parts) >= 3:
        return f"{parts[0]}-{parts[1]}"
    return parts[0]


def is_deferred(test_id: str) -> str | None:
    """deferred なら理由文字列、そうでなければ None。"""
    if test_id in DEFERRED_IDS:
        return DEFERRED_IDS[test_id]
    fam = _family(test_id)
    if fam in DEFERRED_FAMILIES:
        return DEFERRED_FAMILIES[fam]
    return None


def expand_cell(cell: str) -> set[str]:
    """テスト ID セルを個別 ID 集合に展開する。

    対応表記: カンマ区切り / `FAM-01〜04`(数値レンジ)/ `PW-03〜PW-21` /
    `FAM-01/02/06`(スラッシュ列)/ `VR-1a/1b/1c` / 括弧注記の混在。
    """
    ids: set[str] = set()

    # スラッシュ列: FAM-NN/MM/... (数値) と VR-1a/1b (英数)。
    for m in re.finditer(r"((?:PY|VT|VR)-[A-Za-z]+)-([0-9A-Za-z]+(?:/[0-9A-Za-z]+)+)", cell):
        fam = m.group(1)
        for part in m.group(2).split("/"):
            part = part.strip()
            if part.isdigit():
                ids.add(f"{fam}-{int(part):02d}")
            elif part:
                ids.add(f"{fam}-{part}")

    # 数値レンジ: FAM-NN〜MM もしくは FAM-NN〜FAM-MM。
    for m in re.finditer(
        r"((?:PY|VT|PW|XT)-[A-Za-z]+)-(\d+)〜(?:(?:PY|VT|PW|XT)-[A-Za-z]+-)?(\d+)", cell
    ):
        fam, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        for n in range(a, b + 1):
            ids.add(f"{fam}-{n:02d}")
    # PW-03〜PW-21 / PW-03〜21(接尾レンジ)。
    for m in re.finditer(r"(PW)-(\d+)〜(?:PW-)?(\d+)", cell):
        a, b = int(m.group(2)), int(m.group(3))
        for n in range(a, b + 1):
            ids.add(f"PW-{n:02d}")

    # 単独トークン。
    for m in ID_TOKEN.finditer(cell):
        ids.add(m.group(0))

    return ids


def parse_section6() -> tuple[dict[str, list[dict]], list[str]]:
    """§6 を解析。

    返り値: (doc_prefix -> [{"ac","layer","test_ids"} ...], エラー一覧)。
    """
    errors: list[str] = []
    if not PLANS_12.exists():
        return {}, [f"plans/12-testing.md が見つからない: {PLANS_12}"]

    text = PLANS_12.read_text(encoding="utf-8")
    try:
        start = text.index("## 6.")
        end = text.index("## 7.")
    except ValueError:
        return {}, ["plans/12 §6 の範囲(## 6. 〜 ## 7.)を特定できない"]
    body = text[start:end]

    rows_by_doc: dict[str, list[dict]] = defaultdict(list)
    current_doc: str | None = None
    for line in body.splitlines():
        head = SECTION_HEADING.match(line)
        if head:
            current_doc = head.group(1)  # 例 "docs/00"
            continue
        if current_doc is None:
            continue
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 4:
            continue
        ac = cols[0]
        # ヘッダ行・区切り行はスキップ。
        if ac in ("AC", "ID") or set(ac) <= {"-", ":", " "}:
            continue
        if not ac.startswith("AC-"):
            continue
        layer = cols[-2]
        test_cell = cols[-1]
        rows_by_doc[current_doc].append(
            {"ac": ac, "layer": layer, "test_ids": expand_cell(test_cell), "cell": test_cell}
        )
    return rows_by_doc, errors


def count_doc_acs(doc_prefix: str) -> int | None:
    """docs/NN-*.md の受け入れ基準チェックリスト(`- [ ]`/`- [x]`)件数。"""
    matches = sorted(DOCS_DIR.glob(f"{doc_prefix.split('/')[1]}*.md"))
    if not matches:
        return None
    total = 0
    checkbox = re.compile(r"^\s*- \[[ xX]\]")
    for path in matches:
        for line in path.read_text(encoding="utf-8").splitlines():
            if checkbox.match(line):
                total += 1
    return total


def scan_repo_tags() -> dict[str, list[str]]:
    """リポジトリの **テストファイル** から ID タグを収集。id -> [ファイル...]。"""
    patterns = [
        "apps/*/tests/**/*.py",
        "apps/*/tests/*.py",
        "packages/*/tests/**/*.py",
        "packages/*/tests/*.py",
        "apps/*/src/**/*.test.ts",
        "apps/*/src/**/*.test.tsx",
        "apps/*/src/**/*.spec.ts",
        "apps/*/src/**/*.spec.tsx",
        "apps/*/e2e/**/*.ts",
        "packages/*/src/**/*.test.ts",
        "packages/*/src/**/*.test.tsx",
    ]
    found: dict[str, list[str]] = defaultdict(list)
    seen_files: set[str] = set()
    for pat in patterns:
        for path in glob.glob(str(REPO_ROOT / pat), recursive=True):
            if "node_modules" in path or path in seen_files:
                continue
            seen_files.add(path)
            try:
                content = Path(path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = os.path.relpath(path, REPO_ROOT)
            for m in ID_TOKEN.finditer(content):
                found[m.group(0)].append(rel)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="トレーサビリティ検査(plans/12 §6・§16)")
    ap.add_argument("--verbose", "-v", action="store_true", help="全 ID の割付先も表示")
    args = ap.parse_args()

    rows_by_doc, errors = parse_section6()
    tags = scan_repo_tags()

    # --- 参照される全 ID を収集・分類 -------------------------------------
    all_referenced: set[str] = set()
    rows_without_id: list[str] = []
    # 意図的に未割付な行(v1 対象外 = M3)。cell が `—` のみ、または「対象外/M3」を含む。
    intentional_blank = re.compile(r"対象外|M3")
    for doc, rows in rows_by_doc.items():
        for row in rows:
            all_referenced |= row["test_ids"]
            has_auto = any(not tid.startswith("REV-") for tid in row["test_ids"])
            if not has_auto:
                has_rev = any(tid.startswith("REV-") for tid in row["test_ids"])
                cell = row["cell"]
                is_blank_placeholder = not row["test_ids"] and (
                    cell.strip() in ("—", "-", "") or intentional_blank.search(cell)
                )
                # REV 割付も自動 ID も無く、意図的プレースホルダでもない行だけ NG。
                if not has_rev and not is_blank_placeholder:
                    rows_without_id.append(
                        f"{doc} {row['ac']}: テスト ID 未割付 (cell={row['cell']!r})"
                    )

    hard = sorted(
        tid for tid in all_referenced if tid.split("-")[0] in HARD_PREFIXES and not is_deferred(tid)
    )
    deferred = sorted(tid for tid in all_referenced if is_deferred(tid))
    soft = sorted(tid for tid in all_referenced if tid.split("-")[0] in SOFT_PREFIXES)
    info = sorted(tid for tid in all_referenced if tid.split("-")[0] in INFO_PREFIXES)

    hard_missing = [tid for tid in hard if tid not in tags]
    soft_missing = [tid for tid in soft if tid not in tags]

    # --- 件数突合 ---------------------------------------------------------
    count_errors: list[str] = []
    total_docs = 0
    total_rows = 0
    for doc in sorted(rows_by_doc):
        rows = rows_by_doc[doc]
        n_rows = len(rows)
        n_docs = count_doc_acs(doc)
        total_rows += n_rows
        if n_docs is None:
            count_errors.append(f"{doc}: docs ファイルが見つからない(件数突合不可)")
            continue
        total_docs += n_docs
        if n_docs != n_rows:
            count_errors.append(f"{doc}: docs の受け入れ基準 {n_docs} 件 ≠ §6 表 {n_rows} 行")

    # --- レポート ---------------------------------------------------------
    print("=" * 72)
    print("トレーサビリティ検査(plans/12 §6・§16 / M0-38・M1-23)")
    print("=" * 72)
    print(f"§6 参照テスト ID: {len(all_referenced)} 種")
    print(f"  M0/M1 必達(PY/HP/VT・非 deferred): {len(hard)}")
    print(f"  M0/M1 スコープ外(deferred M2/M3): {len(deferred)}")
    print(f"  ソフト(PW/XT/VR・M0-39 並行作成): {len(soft)}")
    print(f"  自動ゲート対象外(SM/PF/REV): {len(info)}")
    print("-" * 72)
    print(f"件数突合: docs 受け入れ基準 合計 {total_docs} 件 / §6 表 合計 {total_rows} 行")

    if args.verbose:
        print("-" * 72)
        print("[M0/M1 必達 ID の割付先]")
        for tid in hard:
            where = tags.get(tid)
            mark = "OK  " if where else "MISS"
            loc = where[0] if where else "(未実装)"
            print(f"  {mark} {tid:14s} -> {loc}")
        print("[deferred(理由)]")
        for tid in deferred:
            print(f"  -   {tid:14s} : {is_deferred(tid)}")

    ok = True

    if count_errors:
        ok = False
        print("-" * 72)
        print("NG 件数突合エラー:")
        for e in count_errors:
            print(f"  - {e}")

    if rows_without_id:
        ok = False
        print("-" * 72)
        print("NG テスト ID 未割付の行(REV も無い):")
        for e in rows_without_id:
            print(f"  - {e}")

    if hard_missing:
        ok = False
        print("-" * 72)
        print(f"NG M0/M1 必達だが未実装のテスト ID({len(hard_missing)} 件):")
        for tid in hard_missing:
            print(f"  - {tid}  (家族 {_family(tid)}・タグが実テストに存在しない)")
        print("  → 実テストに `# <ID>:` タグを付けるか、M1/M2 なら DEFERRED に理由付きで登録する。")

    if soft_missing:
        print("-" * 72)
        print(f"WARN ソフト(M0-39 並行作成中)で未実装: {len(soft_missing)} 件")
        print("     " + ", ".join(soft_missing))

    if errors:
        ok = False
        print("-" * 72)
        print("NG 解析エラー:")
        for e in errors:
            print(f"  - {e}")

    print("=" * 72)
    if ok:
        print("PASS: M0/M1 対象テスト ID の未割付・未実装 0 / 件数突合 OK")
        return 0
    print("FAIL: 上記を解消すること")
    return 1


if __name__ == "__main__":
    sys.exit(main())
