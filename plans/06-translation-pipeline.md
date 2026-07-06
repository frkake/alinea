# 06. 翻訳パイプライン実装計画 — プレースホルダ・プロンプト・キャッシュ・品質検査

> 対象読者と前提
> 本書は「訳読 / YAKUDOKU — 論文読解ワークベンチ」の翻訳パイプライン(自然訳/直訳の生成・再翻訳・用語集・共有キャッシュ・品質検査・進捗)の実装計画である。対象読者は apps/worker・apps/api・packages/py-core の実装者。機能仕様の正は [docs/03-translation.md](../docs/03-translation.md)(および docs/01 §4–6・docs/02 §5)。データモデルは [plans/02-data-model.md](02-data-model.md)、API は [plans/03-api.md](03-api.md) §7、ジョブ基盤は [plans/01-architecture.md](01-architecture.md) §3.2・§4、LLM 抽象化層は [plans/04-llm-providers.md](04-llm-providers.md) を正とし、本書はそれらの識別子をそのまま使う(再定義しない)。基盤計画に不足する項目は §16「⚠ 基盤への追加要求」に集約した。

## 1. 全体像とファイル配置

### 1.1 コンポーネント

| コンポーネント | 責務 | 配置 |
|---|---|---|
| プレースホルダ codec | インライン保護のエンコード/デコード/検証(§4) | `packages/py-core/src/yakudoku_core/translation/placeholder.py` |
| プロンプトビルダ | system/user メッセージ構築(§5–6)・対訳例集(§5.4) | `packages/py-core/src/yakudoku_core/translation/prompts.py` / `examples.py` |
| 用語集マージ | 3層マージ・スナップショット・逆引き検索(§8) | `packages/py-core/src/yakudoku_core/translation/glossary.py` |
| 品質検査 | 5種の機械チェック(§12) | `packages/py-core/src/yakudoku_core/translation/quality.py` |
| パイプライン駆動 | バッチ化・LLM 呼び出し・再試行・UPSERT(§3–7) | `packages/py-core/src/yakudoku_core/translation/pipeline.py` |
| arq タスク | `translate_section` 等のジョブ実装 | `apps/worker/src/yakudoku_worker/tasks/translate_blocks.py` |
| API | plans/03 §7 のエンドポイント群 | `apps/api/src/yakudoku_api/routers/translations.py` + `services/translation_service.py` |
| LLM 実行 | `LLMRouter.run(task=..., mode="structured")` | [plans/04](04-llm-providers.md) §9(`packages/llm`。§16-7 参照) |

### 1.2 データフロー(初回全文翻訳)

1. ingest ジョブが `translating_abstract` 段でアブストラクトを翻訳し `papers.abstract_ja` を確定([plans/05](05-ingest-pipeline.md) §11.1。§10.5)。
2. `readable` 段の冒頭で TranslationSet(shared、§9)を確保し([plans/05](05-ingest-pipeline.md) §11.2)、アブスト訳の `translation_units` UPSERT(§10.5)に続けて、第 1 本文セクション(イントロ)を **ingest ジョブ内で直接翻訳**(60 秒目標の確実化。plans/05 §2.1 の決定 — ジョブ enqueue の往復を挟まない)。
3. `translating_body` 段で残りセクションぶんの `translation` ジョブ(`payload.reason='initial'`。§3.1)をセクション文書順に `yk:bulk` へ張り出す([plans/05](05-ingest-pipeline.md) §11.2)。
4. 各セクションジョブ(arq タスク `translate_section`)はセクション内の翻訳対象ブロックをバッチ化(§3.3)→ プレースホルダ化(§4)→ `LLMRouter.run("translation", mode="structured")` → 検証(§4.4)→ 品質検査(§12)→ `translation_units` UPSERT → Redis Pub/Sub `events:user:{user_id}` に `translation.unit_completed` を発行。
5. 全セクションジョブ完了を検知した最後のジョブが ingest ジョブを `complete` にし、`notifications`(kind=`translation_complete`)を INSERT([plans/05](05-ingest-pipeline.md) §11.3)。

## 2. 翻訳対象スコープと進捗の分母

### 2.1 自動翻訳スコープの判定関数(確定)

`compute_translation_scope(content: DocumentContentJson) -> ScopeResult` を `pipeline.py` に置く。ブロックが**自動翻訳対象**であるのは、以下をすべて満たすとき:

1. `block.type ∈ {paragraph, heading, figure, table, list, quote, theorem, footnote}`(docs/03 §2)。
   - `equation` / `code` / `algorithm` / `reference_entry` は常に対象外(docs/03 §2。algorithm は擬似コード本体のため対象外。`quote`・`theorem` は「種別付き段落コンテナ」として本文扱い)。
   - `figure` / `table` はキャプションのみが対象(表セルは §2.2)。
2. ブロックの属するセクションが参考文献セクション(`reference_entry` のみを含むセクション)でない。
3. ブロックの属するセクションが付録でない。付録判定: セクション見出しの `number` が `A`〜`Z` 始まり(例 `A`, `B.1`)、または `title` が正規表現 `^\s*Appendi(x|ces)\b` に一致(大文字小文字非依存)。

`ScopeResult = { in_scope_block_ids: list[str](文書順), sections: list[{section_id, block_ids}] , appendix_section_ids: list[str], reference_section_ids: list[str] }`。

- 構造化ジョブ(ingest の `structuring` 段)がこの関数を実行し、(a) `document_revisions.stats.translatable_blocks` に `len(in_scope_block_ids)` を保存、(b) `block_search_index.in_translation_scope`(§16-6。plans/02 §4.3 に反映済み)に反映する。判定は決定的で、`block_search_index` 再生成時にも同値になる。

### 2.2 設定(4f)との関係

| 設定項目(4f 逐語) | 既定 | 実装 |
|---|---|---|
| 「付録(Appendix)を自動翻訳しない」 | ON | ON: 付録セクションの `translate_section` ジョブを積まない。OFF: 初回翻訳時に付録セクションも `yk:bulk` に積む(**分母は変えない**。§13.1) |
| 「表のセル内テキストを翻訳しない」 | ON | ON: table ブロックはキャプションのみ翻訳。OFF: セルも §10.4 のフローで翻訳。「この表を翻訳」は設定に関わらず常に利用可 |
| 「30 ページ超の論文はセクション選択を提案」 | ON | `stats.pages > 30` のとき ingest の `readable` 到達時に `translate_section` を積まず、選択 UI の確定(POST `/api/translation-sets/{set_id}/sections/{section_id}/translate` の一括呼び出し)を待つ。既定は全選択(提案であって強制ではない。P6)。ユーザー設定は `users.settings.translation.*`([plans/03](03-api.md) §17.1 のキー: `default_style` / `auto_translate_appendix` / `translate_table_cells` / `suggest_section_selection_over_30_pages`) |

- 設定はいずれも**ジョブを積む範囲**を変えるだけで、スコープ判定(§2.1)と分母は不変。理由: shared セット(§9)は全ユーザー共通であり、個人設定で分母が揺れると進捗表示が人によって矛盾する。

## 3. ジョブ分割とバッチ化

### 3.1 ジョブ種別(DB の正 = plans/02 §4.13、API 露出への導出 = plans/05 §2.7)

本書の翻訳系ジョブは**すべて `jobs.kind = 'translation'`**([plans/02](02-data-model.md) §4.13 の CHECK)で INSERT し、用途は `payload.reason` で判別する。API 露出の `Job.kind`(`translation_set` / `section_translate` / `retranslate_unit` / `glossary_apply` 等)への導出表は **[plans/05](05-ingest-pipeline.md) §2.7 が唯一の正**(本書は再定義しない。`reason` の全値域が §2.7 の表で網羅される)。

- `reason` 値域(7 値): `initial`(初回全文)/ `literal`(直訳オンデマンド)/ `on_demand`(付録・任意セクション)/ `table`(表セル)/ `retranslate`(再翻訳)/ `instructed`(指示つき再翻訳)/ `glossary_change`(訳語変更起因)。

| arq タスク(worker 実装名) | 担当 `reason` | キュー | 用途 |
|---|---|---|---|
| `translate_section` | `initial` / `literal` / `on_demand` / `table` | `yk:bulk`(繰り上げ・オンデマンドは `yk:interactive` + `priority=100`。plans/05 §2.5) | セクション単位の翻訳(初回・直訳・付録オンデマンド・表) |
| `retranslate_blocks` | `retranslate` / `instructed` / `glossary_change` | `yk:interactive` | 再翻訳・指示つき再翻訳・用語変更の影響ブロック再翻訳 |

- 優先度は `jobs.priority INT`(大きいほど先。plans/02 §4.13)。オンデマンド系は作成時 `priority=100`、繰り上げは `priority = priority + 100`(§10.1・plans/05 §2.5)。
- 冪等性キー([plans/01](01-architecture.md) §4.4): `translate_section` = `xlate:{set_id}:{section_id}`、`retranslate_blocks` = `rexlate:{set_id}:{blocks_hash}:{instruction_hash}`(`blocks_hash` = ソート済み block_id 列の xxh64、`instruction_hash` = 指示文字列の xxh64、指示なしは `0`)。
- `jobs.payload`(translate_section 系): `{"set_id": "…", "section_id": "sec-3", "block_ids": ["blk-…", …], "reason": "initial" | "literal" | "on_demand" | "table", "table_block_id": null | "blk-…"}`(キー名は plans/05 §11.2 と同一)。`block_ids` はジョブ作成時に §2.1 のスコープから確定して埋める(実行時に設定を再解釈しない=冪等)。
- `jobs.payload`(retranslate_blocks 系): `{"set_id": "…", "block_ids": […], "reason": "retranslate" | "instructed" | "glossary_change", "instruction": "" , "unit_id": null | "tu_…"}`。`instruction` は指示なしのとき空文字列 `""` とし(NULL は使わない)、そのとき冪等性キーの `instruction_hash` は定数 `"0"`。

### 3.2 実行順・優先度

- 初回翻訳: アブストラクト(ingest `translating_abstract` 段で同期実行)→ イントロダクション(第 1 本文セクション。`readable` 段で ingest ジョブ内直接翻訳 — §1.2・plans/05 §2.1)→ 以降セクション順(docs/03 §3)。残りセクションの `translation` ジョブは `translating_body` 段で文書順に enqueue し、arq(FIFO)がそのまま順に処理する。
- ユーザーが開いているセクションの繰り上げ(§10.1)・直訳・付録・表・再翻訳は常に `yk:interactive`([plans/01](01-architecture.md) §3.2 の決定)。
- 1 ジョブ内のバッチは**直列**に実行する(決定)。理由: (a) Anthropic プロンプトキャッシュ TTL 5 分内の呼び出し間隔維持([plans/04](04-llm-providers.md) §13 の要件)、(b) 進捗イベントの単調性、(c) レート制限の自己制御。並列性はセクションジョブ間(worker プロセスの arq 並列度)で確保する。全文翻訳 5 分の目標(docs/09 §1)は「約 390 対象ブロック ÷ バッチ 6 ≒ 65 リクエスト × p50 3 秒 ≒ 200 秒/直列」で単一ワーカーでも達成可能。

### 3.3 バッチ化規則(確定値)

セクション内の対象ブロック列を文書順のまま貪欲に分割する:

| パラメータ | 値 | 説明 |
|---|---|---|
| `BATCH_MAX_BLOCKS` | **6** | 1 リクエストに含める最大ブロック数 |
| `BATCH_MAX_SOURCE_TOKENS` | **2,000 トークン** | バッチ内ブロックのプレースホルダ化済み原文の合計(見積りは tiktoken `o200k_base` × 1.1。[plans/04](04-llm-providers.md) §14) |
| 単独超過ブロック | 単独バッチ | 1 ブロックで 2,000 トークン超は 1 ブロック=1 リクエスト。それでも `CONTEXT_LENGTH` になったら文分割はせず、§4.6 の原文フォールバックと同形式(`content_ja='[]'::jsonb` / `text_ja=''` / `state='machine'`)で UPSERT し、**決定: `quality_flags: ['context_overflow']`**(§16-4 で値域に追加)を付与、ジョブログに `{"event":"context_length_fallback","block_id":…}` を記録する(`placeholder_mismatch` とはイベント・フラグとも区別する)。段落の分割翻訳は docs/03 §1 原則2 の段落 1:1 対応を壊すため行わない |
| `max_output_tokens` | 4,096(routing.yaml の translation 設定) | `stop_reason == "max_tokens"` で打ち切られたバッチは**二分割して再送**(検証失敗と同じ扱いにしない。分割は再帰、最終的に 1 ブロックまで) |

- 既訳スキップ(冪等)。判定順(決定): バッチ構築前に `translation_units` を `(set_id, block_id)` で引き、(a) `state = 'edited' | 'protected'` の行は `source_hash` を問わずスキップ(docs/03 §9)、(b) それ以外(`state='machine'`)は `source_hash` が現在の原文と一致する行をスキップする — フォールバック行(`placeholder_mismatch` / `provider_refusal` / `context_overflow`)も一致すればスキップし、自動では再試行しない(再試行は明示の再翻訳 API §11.1 のみ)。行が存在しない、または `source_hash` 不一致の block だけをバッチに入れる。
- ヘッダ 1 個だけのバッチなどの縮退も許容(規則を優先し、詰め直しはしない)。

## 4. プレースホルダプロトコル

### 4.1 トークン書式(確定)

書式: `⟦KIND:id⟧`(U+27E6 / U+27E7。docs/03 §4 の逐語)。KIND と Inline 型(docs/01 §4.2)の対応:

| KIND | 対応 Inline | id 規則 | 例 |
|---|---|---|---|
| `MATH` | `math_inline` | `m-{ブロック内出現順1起点}` | `⟦MATH:m-3⟧` |
| `CIT` | `citation` | 参照先 `ref` の値そのまま | `⟦CIT:ref-12⟧` |
| `REF` | `ref` | 参照先 `ref` の値そのまま | `⟦REF:eq-5⟧` |
| `FN` | `footnote_ref` | `fn-{出現順}` | `⟦FN:fn-1⟧` |
| `URL` | `url` | `u-{出現順}` | `⟦URL:u-1⟧` |
| `CODE` | `code_inline` | `k-{出現順}` | `⟦CODE:k-2⟧` |
| `EM` / `/EM` | `emphasis`(**対トークン**) | `e-{出現順}` | `⟦EM:e-1⟧…⟦/EM:e-1⟧` |

- 決定: `emphasis` のみ**対トークン**とし、内部テキストは翻訳対象に含める。理由: emphasis の中身は本文であり、原子トークン化すると強調語が未訳のまま残る。開始・終了とも「ちょうど 1 回ずつ・開始が先」を検証する(§4.4)。他 7 種は原子トークン(削除・複製・改変・分割不可、**順序移動は可**。docs/03 §4)。
- 同一 `ref` を同一ブロック内で 2 回参照するケース(例「図2 と 図2 の比較」)では id が重複するため、2 回目以降は `#2` を付ける: `⟦REF:fig-2#2⟧`。検証は文字列完全一致の多重集合で行うので一意性が保たれる。

### 4.2 エンコード(placeholder.py 完全形)

```python
# packages/py-core/src/yakudoku_core/translation/placeholder.py
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import xxhash
from pydantic import BaseModel

TOKEN_RE = re.compile(r"⟦(/?)(MATH|CIT|REF|FN|URL|CODE|EM):([A-Za-z0-9_.#-]+)⟧")
BRACKET_RE = re.compile(r"[⟦⟧]")

_ATOMIC_KIND = {"math_inline": "MATH", "citation": "CIT", "ref": "REF",
                "footnote_ref": "FN", "url": "URL", "code_inline": "CODE"}
_SEQ_PREFIX = {"MATH": "m", "FN": "fn", "URL": "u", "CODE": "k", "EM": "e"}


class TokenEntry(BaseModel):
    token: str                      # "⟦MATH:m-1⟧" / "⟦EM:e-1⟧"(開始形)
    kind: str                       # MATH/CIT/REF/FN/URL/CODE/EM
    inline: dict[str, Any]          # 元 Inline(復元用。EM は {"t":"emphasis"} の外殻のみ)
    paired: bool = False            # EM のみ True


class EncodedBlock(BaseModel):
    block_id: str
    text: str                       # プレースホルダ化済み原文(LLM に渡す)
    tokens: list[TokenEntry]
    source_hash: str                # §4.5


def encode_block(block: dict[str, Any]) -> EncodedBlock:
    """docs/01 §4.4 の Block JSON を受け、text 以外の Inline をトークン化する。
    figure/table はキャプションの inlines、list は項目を "\n- " 連結、
    heading は title 文字列をそのままテキスト片 1 個として扱う
    (docs/01 §4.1: heading はインライン列を持たないため、トークン化は発生しない)。"""
    counters: Counter[str] = Counter()
    used: Counter[str] = Counter()
    tokens: list[TokenEntry] = []
    parts: list[str] = []

    def _emit(kind: str, ident: str, inline: dict[str, Any], paired: bool = False) -> str:
        used[f"{kind}:{ident}"] += 1
        if used[f"{kind}:{ident}"] > 1:                       # 同一参照の再出現
            ident = f"{ident}#{used[f'{kind}:{ident}']}"
        tok = f"⟦{kind}:{ident}⟧"
        tokens.append(TokenEntry(token=tok, kind=kind, inline=inline, paired=paired))
        return tok

    def _walk(inlines: list[dict[str, Any]]) -> None:
        for inl in inlines:
            t = inl["t"]
            if t == "text":
                parts.append(inl["v"])
            elif t == "emphasis":
                counters["EM"] += 1
                ident = f"e-{counters['EM']}"
                tok = _emit("EM", ident, {"t": "emphasis"}, paired=True)
                parts.append(tok)
                _walk(inl["children"])
                parts.append(f"⟦/EM:{ident}⟧")
            elif t in _ATOMIC_KIND:
                kind = _ATOMIC_KIND[t]
                if t in ("citation", "ref"):
                    ident = inl["ref"]
                else:
                    counters[kind] += 1
                    ident = f"{_SEQ_PREFIX[kind]}-{counters[kind]}"
                parts.append(_emit(kind, ident, inl))
            else:                                              # 未知型は保護側に倒す
                counters["CODE"] += 1
                parts.append(_emit("CODE", f"k-{counters['CODE']}", inl))

    _walk(_block_inlines(block))
    text = "".join(parts)
    return EncodedBlock(block_id=block["id"], text=text, tokens=tokens,
                        source_hash=compute_source_hash(text, tokens))
```

### 4.3 デコード

`decode_translation(encoded: EncodedBlock, output_ja: str) -> list[dict]`:

1. `TOKEN_RE.split` で出力を「テキスト片 | トークン」の列に分解する。
2. テキスト片 → `{"t": "text", "v": …}`。原子トークン → `tokens` から対応 `inline` を**そのまま**復元(1 文字も変えない保証は「元オブジェクトの再利用」で構造的に満たす)。`⟦EM:…⟧`〜`⟦/EM:…⟧` 区間は `{"t": "emphasis", "children": [区間の再帰デコード]}`。
3. 結果が `translation_units.content_ja`(プレースホルダ復元済みインライン列。[plans/02](02-data-model.md) §4.4)になる。`text_ja` は `content_ja` のテキスト片連結+原子インラインの表示表記(`citation`→`[12]`、`ref`→`図2`/`式(5)`、`math_inline`→LaTeX 平文)で導出する(検索・長さ検査用)。

### 4.4 検証アルゴリズム(全トークンちょうど 1 回)

`verify_tokens(encoded: EncodedBlock, output_ja: str) -> VerifyResult`:

```python
class VerifyResult(BaseModel):
    ok: bool
    missing: list[str]      # 出力に現れなかったトークン
    duplicated: list[str]   # 2回以上現れたトークン
    unknown: list[str]      # 原文に存在しないトークン(改変・捏造)
    malformed: bool         # ⟦⟧ の残骸(TOKEN_RE 不一致の括弧)がある
    em_order_ok: bool       # 各 EM ペアで開始が終了より前


def verify_tokens(encoded: EncodedBlock, output_ja: str) -> VerifyResult:
    expected = Counter()
    for te in encoded.tokens:
        expected[te.token] += 1
        if te.paired:
            expected[te.token.replace("⟦EM:", "⟦/EM:")] += 1
    found = Counter(m.group(0) for m in TOKEN_RE.finditer(output_ja))
    missing = sorted((expected - found).elements())
    duplicated = sorted(t for t, c in found.items() if c > expected.get(t, 0) and t in expected)
    unknown = sorted(t for t in found if t not in expected)
    stripped = TOKEN_RE.sub("", output_ja)
    malformed = BRACKET_RE.search(stripped) is not None
    em_order_ok = all(
        output_ja.find(te.token) < output_ja.find(te.token.replace("⟦EM:", "⟦/EM:"))
        for te in encoded.tokens if te.paired
        if te.token in found and te.token.replace("⟦EM:", "⟦/EM:") in found
    )
    ok = not missing and not duplicated and not unknown and not malformed and em_order_ok
    return VerifyResult(ok=ok, missing=missing, duplicated=duplicated,
                        unknown=unknown, malformed=malformed, em_order_ok=em_order_ok)
```

- 合格条件: 全期待トークンが**ちょうど 1 回**(missing・duplicated・unknown が空)+括弧残骸なし+EM の順序正。トークンの順序移動は自由(docs/03 §4)。

### 4.5 source_hash(確定)

`source_hash = xxhash.xxh64(text + "\x1f" + "\x1e".join(te.token for te in tokens)).hexdigest()`(16 桁 hex)。プレースホルダ化済みテキストとトークン列の両方を含むため、本文もインライン構成も変わらなければリビジョンをまたいで一致し、翻訳キャッシュの移送(§9.4)に使える。[plans/02](02-data-model.md) §4.4 の `translation_units.source_hash`(xxhash64 hex)と同一。

### 4.6 検証失敗時の再試行(温度なし・プロンプト再構成)

Anthropic 4.7+ は temperature 系パラメータ不可([plans/04](04-llm-providers.md) §3 の決定で `LLMRequest` に存在しない)。したがって**再試行はサンプリング変更ではなくプロンプト再構成**で行う(決定)。最大 2 回再試行(docs/03 §4)、いずれも同一タスクルート(チェーンのフォールバックは LLMRouter 側の責務で、ここでは扱わない):

| 試行 | リクエスト構成 |
|---|---|
| 1 回目(初回) | 通常バッチ(§3.3)。バッチ内の**不合格ブロックのみ**を次の試行へ回す(合格ブロックはそのまま確定) |
| 2 回目(再試行1) | 不合格ブロックを**単独リクエスト**にし、user メッセージ末尾に修正フィードバックを追記:<br>`前回の出力はトークン検証に失敗した。欠落: ⟦REF:eq-5⟧ / 重複: ⟦CIT:ref-12⟧ / 不明: (なし)。原文中の全トークンを、変更せずちょうど1回ずつ含めて翻訳し直すこと。` |
| 3 回目(再試行2) | 単独リクエスト+強化指示: 原文のトークン一覧を明示列挙(`この訳文には次のトークンを必ず各1回含める: ⟦CIT:ref-12⟧, ⟦REF:eq-5⟧, ⟦MATH:m-3⟧`)し、`思考の途中経過や説明を出力しない` を追記 |
| 全滅 | **原文フォールバック**: `translation_units` に `content_ja='[]'::jsonb` / `text_ja=''` / `quality_flags={placeholder_mismatch}` / `state='machine'` で UPSERT。API 層は `text_ja: null` で返し(plans/03 §7.2 の決定)、ビューアは原文のまま表示+「訳せなかった段落」一覧に載せる(P3)。失敗の生出力は `jobs.log` に `{"event":"placeholder_fallback","block_id":…,"missing":…,"raw_head":出力先頭200字}` で記録 |

- `CONTENT_FILTER` 全滅(plans/04 §9.1-6)も同じ縮退で、フラグは `provider_refusal`(§16-4)。

## 5. 翻訳プロンプトテンプレート(完全形)

プロンプトは 3 層([plans/04](04-llm-providers.md) §13 のキャッシュ可能プレフィックス順): `system[0]`(静的・リリース単位)→ `system[1]`(論文スコープ・TranslationSet 単位)→ `messages[0]`(バッチ単位)。`PROMPT_VERSION = "tr-2026-07-06.1"`(`prompts.py` の定数。変更時は末尾連番を上げる。§9.3)。

### 5.1 system[0] — 静的プリアンブル(自然訳。逐語・完全形)

```text
あなたは機械学習・計算機科学分野の英日学術翻訳者である。与えられた英語論文のブロックを日本語に翻訳する。

## 翻訳規則(優先順)
1. 忠実性: 原文の主張・限定・ニュアンス(may / might / we hypothesize 等)を保つ。要約・省略・補足・意訳による情報の追加をしない。
2. 段落対応: 入力の1ブロックを出力の1ブロックに訳す。文の分割・結合は許すが、ブロックをまたぐ再構成・順序入替をしない。
3. トークン完全保持: ⟦KIND:id⟧ 形式のトークンは数式・引用・参照・URL・コードの保護記号である。各トークンを訳文に「ちょうど1回ずつ」含める。日本語の語順に合わせた位置の移動は自由。削除・複製・内容の改変・翻訳は禁止。⟦EM:…⟧ と ⟦/EM:…⟧ は強調範囲の開始と終了の対であり、両方を残し開始を先に置く。
4. 用語一貫性: 「用語表」がある場合は必ずその訳語に従う。
5. 固有名詞: 著者名・組織名・モデル名・データセット名・手法の固有名は原語のまま。

## 文体規定
- 「だ・である」調に固定する。体言止めを多用しない。
- 学術書として自然で読みやすい日本語にする。逐語直訳調(「〜するところの」等)を避ける。
- 慣用のカタカナ語は無理に訳さない(attention → アテンション、fine-tuning → ファインチューニング)。定訳のある語は定訳を使う(neural network → ニューラルネットワーク、generalization → 汎化)。
- 初出の頭字語は「日本語訳(English, ABBR)」形式で訳す(例: 大規模言語モデル(Large Language Model, LLM))。同じ入力内での2回目以降は略語のみ。
- 用語表で policy=both の語は、この入力内での初出時のみ「訳語(原語)」形式で併記する(例: 整流フロー(rectified flow))。2回目以降は訳語のみ。
- theorem / lemma / corollary / proposition / definition / remark の種別名は 定理 / 補題 / 系 / 命題 / 定義 / 注意 と訳す。
- 見出し(heading)ブロックは見出し本文のみを簡潔に訳す。節番号・原題を訳文に含めない。

## 出力
指定された JSON スキーマに厳密に従い、JSON オブジェクトのみを出力する。説明文・前置き・コードフェンスを含めない。各要素の "id" は入力ブロックの id をそのまま返す。

## 対訳例
[GOOD] 原文: We train with ⟦CIT:ref-12⟧ using the loss in ⟦REF:eq-5⟧, where ⟦MATH:m-1⟧ denotes the drift.
      訳文: ⟦REF:eq-5⟧ の損失を用いて ⟦CIT:ref-12⟧ に従い学習する。ここで ⟦MATH:m-1⟧ はドリフトを表す。
[GOOD] 原文: This ⟦EM:e-1⟧may⟦/EM:e-1⟧ improve sample quality, although we do not verify it at scale.
      訳文: これはサンプル品質を改善する⟦EM:e-1⟧可能性がある⟦/EM:e-1⟧が、大規模には検証していない。
[BAD]  原文: The paths of the rectified flow avoid crossing each other (⟦REF:fig-2⟧).
      訳文: 整流フローの経路は互いに交差しない。   ← ⟦REF:fig-2⟧ を削除しており不合格
[BAD]  原文: We hypothesize that straighter paths reduce discretization error.
      訳文: より直線的な経路は離散化誤差を減らす。   ← we hypothesize の限定を落としており不合格。正: 「…減らすという仮説を立てる。」
```

- `ContentPart(text=上記, cache_hint=True)`(Anthropic キャッシュ境界 1。[plans/04](04-llm-providers.md) §13)。
- 対訳例集は `examples.py` の定数 `TRANSLATION_EXAMPLES`(**確定数: GOOD 8 対・BAD 6 対**。プロンプトとリグレッションテスト(§17)の両方で同一定数を使う — docs/03 §5「仕様アセット」)。system[0] にはうち GOOD 2・BAD 2 を埋め込む(トークン予算 3,000 の内数)。

### 5.2 system[0] — 直訳スタイル差分

`style='literal'` のセットでは「文体規定」節を以下に**差し替える**(他の節は共通):

```text
## 文体規定(直訳)
- 「だ・である」調に固定する。
- 原文の語順・構文を可能な限り写像する。文の分割・結合をせず、原文1文=訳文1文の対応を保つ。
- 関係詞・分詞構文は構造が見える形で訳す(自然さより構文対応を優先する)。
- カタカナ語・定訳・頭字語・用語表の扱いは自然訳と同じ。
```

### 5.3 system[1] — 論文スコープ文脈(TranslationSet 単位・テンプレート)

```text
# 対象論文
タイトル: {papers.title}
著者: {authors_short}
分野プロファイル: {profile_text}

# 論文の見出しツリー(位置把握用。翻訳対象ではない)
{toc_outline}

# 用語表(この論文の訳で必ず従う)
{glossary_lines}
```

- `profile_text` は arXiv カテゴリからの決定的マッピング(`prompts.py` の定数 `FIELD_PROFILES`): `cs.LG|stat.ML` → 「機械学習。損失・最適化・汎化などの ML 標準訳語感覚に従う。」/ `cs.CV` → 「コンピュータビジョン。」/ `cs.CL` → 「自然言語処理。」/ `cs.RO` → 「ロボティクス。」/ 該当なし → 「一般的な計算機科学。」(第一カテゴリで決定。docs/03 §5 の「分野プロファイル」)。
- `toc_outline` はセクション番号+原題の箇条書き(2 階層。例 `- 2 Method` / `  - 2.1 Rectified Flow`)。
- `glossary_lines` は用語スナップショット(§8.2)を 1 行 1 語で列挙: `- rectified flow → 整流フロー [policy=both]` / `- Rectified Flow(手法名) → 原語のまま [policy=keep_original]`。空なら `(用語表なし)`。
- `ContentPart(cache_hint=True)`(Anthropic キャッシュ境界 2)。OpenAI 向けには `LLMRequest.prompt_cache_key = f"tr:{revision_id}:{style}:{glossary_hash}"` を設定([plans/04](04-llm-providers.md) §13)。

### 5.4 user メッセージ(バッチ単位・テンプレート)

```text
# 文脈(参考情報。翻訳しない)
## 現在のセクション: {section_path_display}       ← 例: 2 Method > 2.1 Rectified Flow
## 直前のブロック(原文):
{prev_source_blocks}
## 直前のブロックの既訳:
{prev_translations}
## 直後のブロック(原文):
{next_source_block}

# 翻訳対象ブロック({n}件。id を保ってすべて訳す)
[{block_id_1}] ({block_type_1}) {encoded_text_1}
[{block_id_2}] ({block_type_2}) {encoded_text_2}
…
```

- 再翻訳(`reason=instructed`)では末尾に追記: `# 追加指示(ユーザー): {instruction}`。通常再翻訳(`reason=retranslate`)では `# 注意: 前回の訳はユーザーに「訳がおかしい」と指摘された。原文に忠実に訳し直すこと。` を追記。
- 文脈が存在しない場合(セクション先頭など)は該当小見出しごと省略する。

## 6. 文脈パッキング(確定値)

| 項目 | 値 | 理由 |
|---|---|---|
| 前文脈 N | **前 2 ブロック**(原文。プレースホルダ化済み)+ その既訳(存在する分のみ) | 代名詞・省略の解決に直前 2 段落で十分。既訳を与えることで訳語・文体の連続性を保つ(docs/03 §3) |
| 後文脈 | **後 1 ブロック**(原文のみ) | 「:」で終わる導入文→数式のような前方参照の解決用 |
| 見出しパス | セクション見出しの祖先列(番号+原題)を `>` 連結 | docs/03 §3「セクション見出しを文脈として与える」 |
| 文脈の除外 | `equation` / `code` ブロックが文脈位置にある場合はプレースホルダ化せず `『数式 (5)』` / `『コード』` の 1 行要約で置く | 文脈トークンの浪費防止 |
| 文脈の切り詰め | 前後文脈は各ブロック先頭 600 文字まで | 予算管理。4,000 トークンは設計目標値であり実行時の強制チェックは行わない(決定)— 600 文字切り詰めと `BATCH_MAX_SOURCE_TOKENS`(§3.3)で構造的に上限が保証されるため |

- バッチの前文脈は「バッチ先頭ブロックの直前 2 ブロック」。バッチ内部のブロック間文脈は不要(同一リクエスト内で相互に見えるため)。

## 7. structured output スキーマ

routing.yaml の `translation.structured: true`([plans/04](04-llm-providers.md) §8)に対応する JSON Schema(`JsonSchemaSpec(name="translation_batch_v1")`)。Pydantic モデルから `model_json_schema()` で生成する([plans/04](04-llm-providers.md) §12.2 の決定):

```python
class TranslatedBlock(BaseModel):
    id: str                     # 入力の block_id をそのまま返す
    ja: str                     # プレースホルダ入り訳文

class TranslationBatchOut(BaseModel):
    translations: list[TranslatedBlock]
```

受信後の検証順: (1) LLM 層の schema 検証(`resp.parsed`)→ (2) `id` 集合が入力と一致(欠落 id は不合格ブロック扱い、余剰 id は破棄)→ (3) ブロックごとに `verify_tokens`(§4.4)→ (4) 合格ブロックのみ `decode_translation` → 品質検査(§12)→ UPSERT。

## 8. 用語集(3層マージ・スナップショット・訳語変更)

### 8.1 3層マージアルゴリズム(glossary.py)

`build_snapshot(db, *, user_id, library_item_id, shared: bool) -> tuple[GlossarySnapshotJson, str]`:

1. 収集: `glossaries` から `scope='global'` の全行、(shared でなければ)`scope='user' AND user_id=:user_id`、`scope='paper' AND library_item_id=:library_item_id` の `glossary_terms` を取得。**shared セット構築時は global のみ**([plans/02](02-data-model.md) §3.4 の制約)。paper スコープからは**ユーザーが確定した語のみ**(`auto_extracted=false` に更新された語。§8.4)を含める。
2. マージ: キー `lower(source_term)` で **paper > user > global** の優先で 1 語 1 訳に確定(docs/03 §7)。
3. 正規化: `source_term` の小文字順でソートし、`GlossarySnapshotJson`(`[{source_term, target_term, policy, origin}]`。[plans/02](02-data-model.md) §3.4)に平坦化。
4. ハッシュ: `glossary_hash = sha256(canonical_json(snapshot))[:16]`(canonical_json = キー順固定・空白なしの UTF-8)。plans/03 §7.1 の `glossary_snapshot_id` はこの `glossary_hash` を返す(スナップショットは行を持たない導出識別子。[plans/02](02-data-model.md) §1.4 の決定に整合)。

スナップショットは `translation_sets.glossary_snapshot` に**セット作成時に凍結保存**し、以後そのセットの全翻訳(初回・再翻訳・オンデマンド)はこの凍結値だけを見る(再現性。docs/03 §7)。プロンプトに入れるのは snapshot のみで、DB の現在値は見ない。

### 8.2 プロンプトへの反映

§5.3 の `glossary_lines`。`policy` 別の指示文言: `translate` → `→ {target_term}` / `keep_original` → `→ 原語のまま` / `both` → `→ {target_term} [初出時のみ「{target_term}({source_term})」と併記]`。

### 8.3 訳語変更 → 影響ブロック検索(逆引きインデックス)

**決定: 専用の逆引きテーブルは新設せず、`block_search_index` の PGroonga インデックス `pgroonga_block_search_index_source_text`([plans/02](02-data-model.md) §4.14)を逆引きインデックスとして使う。** 手順(`find_affected_blocks(db, revision_id, source_term) -> list[str]`):

```sql
-- 候補抽出(PGroonga。TokenBigram+NormalizerAuto で大文字小文字非依存)
SELECT block_id, source_text FROM block_search_index
WHERE revision_id = :revision_id
  AND in_translation_scope                      -- §2.1(対象外ブロックは再翻訳しない)
  AND source_text &@~ :source_term;
```

続けて Python 側で語境界を厳密化: `re.search(rf"(?<![A-Za-z]){re.escape(source_term)}(?![A-Za-z])", source_text, re.IGNORECASE)` に一致する行のみ採用(`&@~` の部分一致による過剰ヒットを除去)。結果件数が `PATCH /api/glossary/terms/{term_id}?dry_run=true` の `affected_block_count`(「12 段落を再翻訳します」)になる。

### 8.4 訳語変更の適用フロー(`PATCH dry_run=false`)

1. `glossary_terms` を更新(paper/user スコープ。global は 403)。paper スコープの自動抽出語は確定操作(訳語確定・修正)で `auto_extracted=false` に更新する。
2. 対象ユーザーの personal セットを解決。存在しなければ**フォーク作成**(§9.2)— このとき新スナップショット(§8.1、shared=False)を凍結。既存 personal セットがあれば `glossary_snapshot` を新スナップショットで**置き換え**、`updated_at` 更新(personal セットのスナップショットは「そのユーザーの現在の確定用語」を表す。凍結の対象はあくまで各ジョブ実行時の値で、ジョブ payload には set の snapshot 参照のみを持たせ実行時に読む)。
3. §8.3 で影響ブロックを検索し、`retranslate_blocks` ジョブ(`reason='glossary_change'`、`yk:interactive`)を 1 本 enqueue(`block_ids` 一括)。**モデルは通常の `translation` タスクルート**を使う(決定。エスカレーション(§11)は「訳の品質を疑った」場合の脱出口であり、用語置換起因の一括再翻訳は物量作業のため。docs/03 §9 のエスカレーション対象は再翻訳・指示つき再翻訳のみ)。
4. `state='edited' | 'protected'` の unit はスキップし、レスポンスの `affected_block_count` から除いた件数をジョブログに記録(上書きしない。docs/03 §9)。
5. 完了イベントは §13.2 と同じ `translation.unit_completed`。受け入れ基準「1 分以内反映」(docs/03 §12)は interactive キュー+一括ジョブ+バッチ 6 で満たす(12 ブロック=2 リクエスト ≒ 10 秒)。

### 8.5 「ユーザー用語集に昇格」

`POST /api/glossary/terms/{term_id}/promote`(plans/03 §7.9)。scope=user の複製を作るのみで**再翻訳ジョブは起動しない**(「次の論文から効く」— docs/03 §7)。

### 8.6 論文ローカル用語の自動抽出(取り込み時)

- 実行: ingest の `translating_abstract` 段の直後に 1 回、`summary` タスクルート([plans/04](04-llm-providers.md) §8 の決定)で structured 抽出(`{"terms": [{source_term, target_term, pos_label}]}`、最大 30 語。対象: 頭字語・新規手法名・頻出専門用語 — docs/03 §7)。
- 保存: 結果は Paper の共有資産として `papers.extracted_terms`(§16-3。plans/02 §4.3 に反映済み)に保存し、**各ユーザーの LibraryItem 作成時**に `glossaries(scope='paper')` + `glossary_terms(auto_extracted=true)` へコピーする(2 人目以降は再抽出しない)。
- 自動抽出語は**提案**であり、確定されるまでスナップショットに入らない(§8.1)。したがって初回 shared 翻訳の共有可能性を壊さない。

## 9. 共有キャッシュと personal フォーク

### 9.1 キー設計(確定)

論理キャッシュキーは **`revision_id × style × glossary_hash × prompt_version`** の 4 つ組。DB 上の表現:

- 一意性は `uq_translation_sets_shared (revision_id, style) WHERE scope='shared'`([plans/02](02-data-model.md) §4.4)が持つ。`glossary_hash` は `glossary_snapshot` から導出(§8.1)、`prompt_version` は `translation_sets.prompt_version`(§16-1。plans/02 §4.4 に反映済み)に記録する。
- **解決規則(決定)**: shared セットの再利用判定は `(revision_id, style)` のみで行う。既存 shared セットの `glossary_hash` / `prompt_version` が現行値と異なっていても**そのまま使い、再翻訳しない**。理由: グローバル定訳シードの追記やプロンプト改良のたびに全論文を再翻訳するとコストが際限なく、既訳の品質は変わらない。改良は新規セット(新論文・新リビジョン)から効き、既存論文は「再取り込み」(2a)で新リビジョン+新セットとして手動更新できる。両値は監査・デバッグ用に保持する。
- 2 人目以降のユーザー: public 論文の取り込みで `(revision_id, 'natural')` の shared セットが `status='complete'` なら翻訳ジョブを一切積まない(翻訳待ちゼロ・コストゼロ。docs/03 §8)。`partial` なら既存ジョブの進行に相乗りする(ジョブは冪等キーで重複しない)。

### 9.2 personal フォーク(差分保存)

作成契機は 3 つ(docs/03 §8–9): (a) ユーザー用語集/論文ローカル用語集の適用(§8.4)、(b) 手動編集 `PUT /api/translation-units/{unit_id}`(plans/03 §7.7 — 自動フォーク)、(c) shared セット上での再翻訳 proposal の採用。

```sql
INSERT INTO translation_sets (revision_id, style, scope, user_id, base_set_id,
                              glossary_snapshot, prompt_version, status)
VALUES (:revision_id, :style, 'personal', :user_id, :shared_set_id,
        :snapshot_json, :prompt_version, 'complete');
```

- unit のコピーは行わない(**差分のみ保持**。[plans/02](02-data-model.md) §1.4)。フォーク後に再翻訳・編集された block の行だけが personal セットに入り、表示は plans/02 §5.2 のマージクエリ(personal 優先→shared)で解決する。
- private 論文([plans/02](02-data-model.md) §1.4): 取り込み時から `scope='personal'`, `base_set_id IS NULL` のセットを作り全 unit を自前保持。共有プールに入れない(docs/09 §4)。
- 直訳の shared 化: public × global 用語のみの直訳セットは shared として作る(docs/03 §8 — 誰かが一度オンデマンド生成すれば以降のユーザーは待たない)。ユーザーが用語を確定済み(personal 自然訳を持つ)でも、直訳の初回生成はまず shared を作り、そのユーザーの用語差分は直訳側にも personal フォーク+影響ブロック再翻訳で重ねる(§8.4 を style='literal' にも適用)。

### 9.3 prompt_version の運用

- 定数 `PROMPT_VERSION`(§5)はテンプレート・対訳例・プレースホルダ規約のいずれかの変更で上げる。`translation_units.model` と併せ、任意の訳文について「どのプロンプト×どのモデルで生成されたか」を再現できる。

### 9.4 リビジョン昇格時のキャッシュ移送

新 DocumentRevision 作成時(再取り込み・B→A 昇格・arXiv 新版)、ワーカーは新リビジョンの各セット作成時に:

```sql
INSERT INTO translation_units (set_id, block_id, source_hash, content_ja, text_ja,
                               state, quality_flags, model)
SELECT :new_set_id, n.block_id, n.source_hash, o.content_ja, o.text_ja,
       o.state, o.quality_flags, o.model
FROM   old_new_block_map n                -- 旧→新 block_id 対応表(docs/01 §4.3 の引き継ぎ規則)
JOIN   translation_units o ON o.set_id = :old_set_id AND o.block_id = n.old_block_id
WHERE  o.source_hash = n.source_hash;     -- 原文不変ブロックのみ移送
```

移送されなかったブロックだけを `translate_section` ジョブに積む(docs/01 §4.3「翻訳キャッシュの大部分が無傷で移行」)。

## 10. オンデマンド系フロー

### 10.1 開いているセクションの優先繰り上げ

- 入口 2 つ: (a) `PUT /api/library-items/{id}/position`(読書位置保存)の副作用、(b) 明示 API `POST /api/translation-sets/{set_id}/prioritize {section_id}`(plans/03 §7.4)。(a) は position の block_id からセクションを解決して (b) と同じ処理を呼ぶ。
- 実装([plans/05](05-ingest-pipeline.md) §2.5・[plans/02](02-data-model.md) §4.13 の規約に従う):

```sql
UPDATE jobs SET priority = priority + 100
WHERE kind = 'translation' AND status = 'queued'
  AND payload->>'set_id' = :set_id
  AND payload->>'section_id' = :section_id
RETURNING id;
```

該当行があれば同一 `job_id` を `yk:interactive` に**二重 enqueue**する(先着 claim が勝ち、後着は no-op。plans/05 §2.3)。該当行なし(実行中・完了・対象外)は 202 のまま no-op。連続スクロールでの多重呼び出しも無害(繰り上げ済みジョブの priority がさらに上がるだけ)だが、デバウンスはクライアント側 1,000ms(plans/05 §2.5)。

### 10.2 直訳スタイルのオンデマンド生成

`POST /api/revisions/{revision_id}/translations {style: "literal", priority_section_id?}`(plans/03 §7.3)のシーケンス:

1. `(revision_id, 'literal')` の shared セットを検索。`complete` なら **200** `{set_id, job_id: null}`(即時表示。2 回目以降の切替は即時 — docs/03 §12)。
2. なければセット作成(public → shared / private → personal。§9.2)+ 親ジョブ(`jobs.kind='translation'`・`payload.reason='literal'`(セット全体。§3.1)、`yk:interactive`、冪等キー `xlateset:{set_id}`)を作成し **202** `{set_id, job_id}`。
3. 親ジョブは §2.1 のスコープでセクション単位の子ジョブ群(`kind='translation'`・`reason='literal'`・`payload.section_id` あり)を作る。`priority_section_id` のセクションを `yk:interactive` + `priority=100`、残りをセクション順で `yk:bulk` に積む(docs/03 §5 の決定: 表示中セクション優先、以降は自然訳と同じキュー)。親ジョブは子の完了集計で `jobs.progress`(plans/02 §4.13)を進め、全完了で `complete`。
4. ビューアは切替直後から部分表示(未訳ブロックは原文+「翻訳中…」)。**通知は発行しない**(決定: `translation_complete` 通知は初回取り込みの全文翻訳完了のみ。直訳・付録はユーザーが画面内で待つ操作であり SSE で足りる。通知 3 種の枠を濫用しない)。

### 10.3 付録・任意セクションのオンデマンド翻訳

`POST /api/translation-sets/{set_id}/sections/{section_id}/translate`(plans/03 §7.5)→ `translate_section`(`reason='on_demand'`、`yk:interactive`)。目次の「付録 A 証明 — 未翻訳」「開くと翻訳します(オンデマンド)」ボックス(1a)からは、付録を開いた時点でクライアントがこの API を呼ぶ。付録ブロックは §2.1 のスコープ外なので、`block_ids` はスコープ判定を**付録除外なし**で再計算した「そのセクションの翻訳可能ブロック」(type 条件のみ適用)。進捗の分母・分子には入らない(§13.1)。

### 10.4 「この表を翻訳」(表セル)

同エンドポイントのブロック指定版 `Request: {block_id}`(plans/03 §7.5)→ `translate_section`(`reason='table'`, `table_block_id` 指定)。

- 1 テーブル = 1 リクエスト。セルを id 付きで渡す: user メッセージに `[r{行}c{列}] {セル原文}` を列挙し、スキーマは `{"cells": [{"id": "r0c1", "ja": "…"}]}`(`JsonSchemaSpec(name="table_cells_v1")`)。数値のみ・記号のみのセル(正規表現 `^[\d\s.,%±×+/():-]*$`)は送らずそのまま保持。
- 保存形式(確定): table ブロックの `translation_units.content_ja` は インライン列ではなく `{"kind": "table", "caption": Inline[] | null, "cells": (string | null)[][] }`(型名 `TableTranslationJson`。`cells[r][c] = null` は未訳=原文表示)。キャプション翻訳だけが先行している場合は `cells: null`。Pydantic の判別は `content_ja` が配列(通常ブロック)かオブジェクト(表)かで行う。
- `text_ja` はキャプション+訳済みセルをスペース連結(検索用)。

### 10.5 アブストラクト

ingest の `translating_abstract` 段で、アブストラクトセクションのブロックを通常のバッチ翻訳(§3–7)で処理し、完了後 `papers.abstract_ja = 各段落 text_ja の "\n\n" 連結` を UPDATE([plans/05](05-ingest-pipeline.md) §11.1。docs/02 §5.2 の 20 秒目標のため本文より先行)。TranslationSet の確保は `readable` 段の冒頭(plans/05 §11.2)なので、アブスト訳の `translation_units` UPSERT はセット確保直後に同じ訳文を流用して行う(§1.2-2。単一ソースは unit、`abstract_ja` はカード表示用の非正規化)。

## 11. 再翻訳・proposal・手動編集

### 11.1 再翻訳・指示つき再翻訳

`POST /api/translation-units/{unit_id}/retranslate {instruction?, discard_edit?}`(plans/03 §7.6)→ `retranslate_blocks`(`reason='retranslate' | 'instructed'`、block 1 件、`yk:interactive`)。

- **タスクルートは `retranslation_escalation`**([plans/04](04-llm-providers.md) §8: chain `[claude-sonnet-5, gpt-5.5, gemini-3.1-pro-preview]`、effort=high)。docs/03 §9「上位モデルへエスカレーション」の実装。
- プロンプトは §5 と同一構成+ §5.4 の追記(指示 or 再訳注意)。加えて user 末尾に前回訳を提示: `# 前回の訳(不採用): {現 text_ja}`(同じ誤りの再生産防止)。
- 結果は unit を直接上書きせず `translation_units.proposal`(§16-2。plans/02 §4.4 に反映済み。JSONB `{text_ja, content_ja, generated_at, model}`)に保存(plans/03 §7.6 の決定: 差分表示→採用の UI 前提)。品質検査(§12)は proposal にも実行し、`placeholder_mismatch` の proposal は保存せずジョブを `failed` にしてエラー表示(壊れた候補を見せない)。
- `state='edited'` の unit への実行は `discard_edit: true` が必須(409 `conflict` / detail `edit_protected`)。

### 11.2 proposal の採用・破棄

- `POST /api/translation-units/{unit_id}/proposal/accept`: 対象セットが shared なら**先に personal フォーク**(§9.2)を作り、フォーク側 unit に proposal の内容を `state='machine'` で書き込む(plans/03 §7.8)。proposal は消去。品質検査フラグは採用時に再計算。
- `DELETE /api/translation-units/{unit_id}/proposal`: proposal を NULL に。204。

### 11.3 手動編集

`PUT /api/translation-units/{unit_id} {text_ja}`(plans/03 §7.7)。shared セット上なら自動フォーク→フォーク側に `state='edited'` で保存。`content_ja` は編集テキストを単一 `{"t":"text","v":…}` インラインとして保存(プレースホルダ構造は失われる。編集はユーザーの責任範囲で、参照リンクが必要なら再翻訳を使う)。編集済み unit は以後の自動再翻訳(用語変更・リビジョン移送)で上書きしない(docs/03 §9。§8.4-4・§9.4 の `state` 条件)。

### 11.4 テレメトリ

「訳がおかしい?」(1b 対訳ポップのフッター)の押下はクライアントが `POST /api/telemetry` に `{"kind": "translation_doubt", "unit_id": …}` を送る(docs/03 §9 の品質テレメトリ)。このエンドポイントは plans/03 に反映済み(§16-8)。API 実装(決定): 認証 `session`、レスポンス 204、保存先は `jobs.log` ではなく専用テーブルを作らず構造化アプリログ(stdout JSON)への記録のみ(v1 は集計をログ基盤で行い、DB 保存は v2)。

## 12. 自動品質検査(5 種の判定式)

`run_quality_checks(encoded: EncodedBlock, source_plain: str, text_ja: str, snapshot) -> list[str]` が UPSERT 直前に実行し、`translation_units.quality_flags` に格納する。フラグ付きブロックはビューアで下線表示(docs/03 §10)。**フラグは表示のみで、訳の配信は止めない**(止めるのは `placeholder_mismatch` / `provider_refusal` / `context_overflow` の 3 つのみ。API はこの 3 フラグの unit を `text_ja: null` で返す — plans/03 §7.2 への追記は §16-4)。

| # | フラグ | 判定式(確定) |
|---|---|---|
| 1 | `placeholder_mismatch` | §4.4 の `VerifyResult.ok == False`(最大 2 回再試行後)。このとき訳は保存しない(§4.6) |
| 2 | `number_mismatch` | 数値抽出 `NUM_RE = /\d+(?:[.,]\d+)*/`。原文(プレースホルダ除去後。MATH 等の内部は保護済みなので対象外)と訳文それぞれで、各マッチを「桁区切りカンマ除去・全角→半角正規化」した多重集合 `Counter` にし、**両者が不一致**なら付与。年号・式番号もプレーンテキスト中なら対象(同数残るべきもの) |
| 3 | `length_outlier` | `r = len(strip_tokens(text_ja)) / len(strip_tokens(source_text))`(いずれも空白除去後の文字数)。**原文 60 文字未満は検査しない**。合格帯 **0.30 ≤ r ≤ 1.10**(実測: 学術英文→和訳の文字数比は概ね 0.5〜0.8)。帯域外で付与(短すぎ=省略疑い / 長すぎ=幻覚・重複疑い) |
| 4 | `glossary_violation` | スナップショット各語について、原文に語境界一致(§8.3 の正規表現)で出現するとき: `policy=translate|both` → 訳文に `target_term` が部分文字列として含まれない / `policy=keep_original` → 訳文に `source_term`(大文字小文字非依存)が含まれない、で付与 |
| 5 | `untranslated` | `strip_tokens(text_ja)` の非空白文字のうち、日本語文字(ひらがな U+3040–309F・カタカナ U+30A0–30FF・CJK U+4E00–9FFF)の比率が **5% 未満**、かつ原文の語数(空白区切り)≥ 4 で付与(英文がそのまま返ってきた検出。固有名詞の羅列段落を誤検知しないための語数条件) |

- 追加値 `provider_refusal`(全プロバイダ content_filter 全滅。[plans/04](04-llm-providers.md) §9.1-6)と `context_overflow`(単独超過ブロックの `CONTEXT_LENGTH` 縮退。§3.3)は原文フォールバックと同時に付与(§16-4)。
- 検査結果の統計(フラグ率)は `jobs.log` に `{"event": "quality_summary", "flags": {"number_mismatch": 2, …}}` として記録し、処理ログ(2a)から確認できる。

## 13. 進捗計算・SSE・通知

### 13.1 進捗の定義(「翻訳 96%」への写像)

- **分母** = `document_revisions.stats.translatable_blocks`(§2.1 の自動翻訳対象ブロック数。参考文献・付録・表セル・equation/code/algorithm を含まない — docs/03 §6.1)。
- **分子** = 対象スコープ内で「表示可能な訳を持つ」unit 数:

```sql
SELECT count(*) FROM translation_units u
JOIN block_search_index b
  ON b.revision_id = :revision_id AND b.block_id = u.block_id AND b.in_translation_scope
WHERE u.set_id IN (:resolved_set_ids)            -- plans/02 §5.2 のマージ解決(personal 優先)
  AND NOT (u.quality_flags && ARRAY['placeholder_mismatch','provider_refusal','context_overflow']);
```

- `progress_pct = floor(100 * 分子 / 分母)`(分母 0 のときは 100)。`GET /api/library-items/{id}/viewer` の `translation.progress_pct`・目次ヘッダ「翻訳 96%」・拡張/カードの「翻訳中 68%」(ingest `translating_body` 中は本値を `jobs.progress`(plans/02 §4.13。API 露出名 `progress_pct` とは別物)に反映 — plans/05 §2.2)がすべてこの 1 式を使う。
- `translation_sets.status` 遷移: unit 0 件 = `pending` → 1 件以上 = `partial` → **対象スコープ全ブロックに unit 行が存在**(フォールバック行含む)= `complete`。フォールバック行があっても complete になり得る(処理は終わっている)。その場合 progress_pct < 100 のまま表示され、「訳せなかった段落」一覧が差分を説明する(P3)。
- 目次(`TocNode`。plans/03 §6.1)の各 boolean: `translated` = 「セクション内スコープ対象ブロックの分子カウント(上式)が対象ブロック数に等しい(対象 0 のセクションは false)」 / `in_progress_denominator` = 「セクションがスコープ対象ブロックを 1 つ以上持つ」 / `on_demand` = 「付録セクション(§2.1-3)である」。参考文献は `in_progress_denominator: false` で淡色(1a)。

### 13.2 SSE イベント

バッチ確定(UPSERT コミット)ごとに Redis Pub/Sub `events:user:{user_id}` へ発行([plans/01](01-architecture.md) §5 のイベント型をそのまま使用):

```json
{"type": "translation.unit_completed",
 "library_item_id": "li_…", "translation_set_id": "…",
 "block_ids": ["blk-3-p2-a1f9", "…"],
 "section_progress": 58, "total_progress": 68}
```

shared セットのジョブでは、その revision を参照する**全ユーザー**(`library_items` JOIN `papers`)のチャンネルに発行する(2 人目以降も進捗が見える)。頻度制御: バッチ単位(≒ 6 ブロックごと)。ビューアは受信 block_ids のうち表示中のものを訳文に差し替え、目次進捗を更新する。

### 13.3 通知

- `translation_complete` 通知は **ingest の全セクションジョブ完了時のみ**発行([plans/05](05-ingest-pipeline.md) §11.3・§12.1。payload は [plans/02](02-data-model.md) §3.7)。完了検知は plans/05 §11.3 のとおり: 各セクションジョブの成功トランザクション内で `pg_advisory_xact_lock(hashtext(set_id::text))` を取り、`kind='translation' AND payload->>'reason'='initial'` の未完了(queued/running/waiting_quota)ジョブ数が 0 なら発行(競合はアドバイザリロックで直列化)。
- 直訳・付録・表・再翻訳・用語変更では通知を発行しない(§10.2 の決定)。

## 14. 見出し原題併記のデータ形式(確定)

- heading ブロックの `translation_units.text_ja` は**訳題本文のみ**(節番号・原題・ダッシュを含まない)。例: block(`number: "1"`, `title: "Introduction"`)→ `text_ja: "はじめに"`。プロンプト側の規定は §5.1「見出しブロックは見出し本文のみを簡潔に訳す」。
- 表示文字列「1 はじめに — Introduction」は**クライアント合成**: `{number} {text_ja} — {title_en}`(原題部分は Source Serif 4 イタリック・淡色 #8A8E94 — 1a/1b。番号なし見出しは `{text_ja} — {title_en}`、例「アブストラクト — Abstract」)。
- API 上は `TocNode.title_ja / title_en`(plans/03 §6.1)と、本文レンダリング用の heading unit で二重に取得できる(値は同一。TocNode 側はビューア初期化の 1 リクエストに同梱するための投影)。
- 未訳見出し(`title_ja: null`)は原題のみ表示。

## 15. モデルルーティングとプロンプトキャッシュ適用

### 15.1 タスク → ルート([plans/04](04-llm-providers.md) §8 の確定値を使用)

| 本書の処理 | task | 既定チェーン | mode |
|---|---|---|---|
| 初回翻訳・直訳・付録・表・用語変更再翻訳(§3, §8.4, §10) | `translation` | `[deepseek-v4-flash, gemini-3.5-flash, gpt-5.4-mini]`・effort=none・max_output_tokens=4096・timeout 120s | `structured` |
| 再翻訳・指示つき再翻訳(§11.1) | `retranslation_escalation` | `[claude-sonnet-5, gpt-5.5, gemini-3.1-pro-preview]`・effort=high・timeout 180s | `structured` |
| 論文ローカル用語の自動抽出(§8.6) | `summary` | `[claude-opus-4-8, gpt-5.5, gemini-3.5-flash]` | `structured` |

- 呼び出し形は [plans/04](04-llm-providers.md) §9.3 のとおり `llm_router.run("translation", build=…, user_id=…, library_item_id=…, job_id=…, mode="structured")`。`user_id` は**ジョブ起動ユーザー**(shared 翻訳のコスト・クォータ帰属。[plans/04](04-llm-providers.md) §10.1 の決定)。
- フォールバック・リトライ・usage_records 記録はすべて LLM 層の責務([plans/04](04-llm-providers.md) §9)。本層の再試行(§4.6)は「検証失敗によるプロンプト再構成」であり、LLM 層のエラーリトライと直交する。
- 使用モデルは `translation_units.model` に記録(処理ログ 2a と受け入れ基準「再翻訳が上位モデルで実行される」の検証点)。

### 15.2 プロンプトキャッシュ適用箇所([plans/04](04-llm-providers.md) §13 の実装指示)

| 箇所 | 適用 |
|---|---|
| `system[0]` 静的プリアンブル(§5.1/5.2。約 3,000 トークン) | `cache_hint=True`(Anthropic 境界1)。全論文・全バッチで共通(スタイル別に 2 系統) |
| `system[1]` 論文スコープ(§5.3。1,000〜4,000 トークン) | `cache_hint=True`(Anthropic 境界2)。TranslationSet 単位で安定 |
| OpenAI 系 | `LLMRequest.prompt_cache_key = f"tr:{revision_id}:{style}:{glossary_hash}"` |
| DeepSeek(既定 primary) | 自動コンテキストキャッシュ。プレフィックス順(system[0]→system[1]→user)を守ることが唯一の要件。2 バッチ目以降の system 全量が hit 単価 $0.0028/1M になる |
| Anthropic(エスカレーション時) | 同一ジョブ内直列実行(§3.2)で TTL 5 分内の連続呼び出しを保証 |
| Google / xAI | 暗黙キャッシュ / なし(操作不要) |

- user メッセージ(バッチ・文脈)はキャッシュ境界に含めない(毎回変わるため)。

## 16. ⚠ 基盤への追加要求

本書の実装に必要だが基盤計画に存在しなかった項目。**別名を発明せず、以下の名前で基盤側へ追記する**(状態を明記):

1. **反映済み**: `translation_sets.prompt_version TEXT NOT NULL DEFAULT 'tr-2026-07-06.1'` — [plans/02](02-data-model.md) §4.4 の DDL に反映済み(§9.1 のキャッシュキー記録・§9.3 の再現性)。
2. **反映済み**: `translation_units.proposal JSONB`(NULL 可。`{text_ja, content_ja, generated_at, model}`)— [plans/02](02-data-model.md) §4.4 に反映済み([plans/03](03-api.md) §7.2/§7.6 の `proposal` 保存・返却に対応)。
3. **反映済み**: `papers.extracted_terms JSONB NOT NULL DEFAULT '[]'`([plans/02](02-data-model.md) §4.3。§8.6 の論文ローカル用語自動抽出の共有保存先)および `glossary_terms.auto_extracted BOOLEAN NOT NULL DEFAULT false`(同 §4.5。[plans/03](03-api.md) §7.9 の `GlossaryTerm.auto_extracted` の保存先)— いずれも反映済み。
4. **`quality_flags` の値域に `provider_refusal` と `context_overflow` を追加**: [plans/02](02-data-model.md) §4.4 のコメントと [plans/03](03-api.md) §7.2 の `quality_flags` リテラル型に追記(`provider_refusal` は [plans/04](04-llm-providers.md) §9.1-6 が既に要求している値、`context_overflow` は §3.3 の単独超過ブロック縮退用)。あわせて plans/03 §7.2 の「`placeholder_mismatch` のブロックは `text_ja: null` で返す」を「`placeholder_mismatch` / `provider_refusal` / `context_overflow` のブロックは `text_ja: null` で返す」に拡張することを要求する。
5. **撤回(解消済み)**: 旧要求「`jobs.kind` に `translate_set` を追加」は撤回する。翻訳系ジョブはすべて `kind='translation'` + `payload.reason`(§3.1)で表現し、jobs テーブル定義は [plans/02](02-data-model.md) §4.13、API `Job.kind` への導出表は [plans/05](05-ingest-pipeline.md) §2.7 を唯一の正とすることで、旧指摘の jobs 定義・kind 列挙の不一致も解消した。
6. **反映済み**: `block_search_index.in_translation_scope BOOLEAN NOT NULL DEFAULT false` — [plans/02](02-data-model.md) §4.3 に反映済み(§2.1 のスコープ判定の物理化。§8.3 の影響ブロック検索と §13.1 の分子 SQL が使う。派生テーブルの性質(content から再生成可能)は維持される)。
7. **LLM 層のパッケージ配置の不一致**: [plans/00](00-tech-stack.md) §2 は `packages/py-core/src/yakudoku_core/llm/`、[plans/04](04-llm-providers.md) §2 は `packages/llm/src/yakudoku_llm/`。本書は plans/04(`packages/llm` / `yakudoku_llm`)を正として参照する。plans/00 側の修正を要求する(翻訳ロジック(`yakudoku_core.translation.*`)の配置は plans/00 のとおり)。
8. **反映済み**: `POST /api/telemetry` — [plans/03](03-api.md) に反映済み(§11.4 の品質テレメトリ。Request `{kind: "translation_doubt", unit_id: string}`、認証 `session`、Response 204)。

## 17. テスト計画

`packages/py-core/tests/translation/`(pytest):

1. **プレースホルダ・プロパティテスト**(`test_placeholder_property.py`。C9 の決定): Hypothesis でランダムな Inline 列を生成 → `encode_block` → 「トークンをシャッフルした合成訳文」に対し `decode_translation(verify 合格) == 元インラインの多重集合保存`を検証。欠落・複製・改変・括弧残骸・EM 逆順の各ケースで `verify_tokens` が不合格になることを網羅。
2. **エンコード仕様**(`test_placeholder_encode.py`): docs/03 §4 の例(`⟦CIT:ref-12⟧` / `⟦REF:eq-5⟧` / `⟦MATH:m-3⟧`)がバイト一致で生成される。同一 ref 再出現の `#2` 採番。figure/table/list/heading の対象テキスト抽出。
3. **品質検査**(`test_quality.py`): §12 の 5 判定式を境界値で検証(r=0.30/1.10、日本語比率 5%、数値多重集合、policy 3 種)。
4. **スコープ判定**(`test_scope.py`): 付録判定(`A`/`B.1`/`Appendix`)・参考文献除外・`translatable_blocks` 件数。Rectified Flow シードデータ(C10)で分母 = stats 値の一致。
5. **バッチ化**(`test_batching.py`): 6 ブロック/2,000 トークン境界、単独超過、max_tokens 打ち切り時の二分割。
6. **プロンプトリグレッション**(`test_prompt_regression.py`): `TRANSLATION_EXAMPLES` の GOOD 対が `verify_tokens`・品質検査を通過し、BAD 対が意図したフラグで落ちる(対訳例集=テスト資産の両用。docs/03 §5)。system[0] のスナップショットテスト(`PROMPT_VERSION` 更新漏れの検出)。
7. **用語マージ**(`test_glossary.py`): paper > user > global の優先・shared=global のみ・`glossary_hash` の正準性(キー順・空白に不変)。
8. **統合(FakeLLM)**(`apps/worker/tests/test_translate_section.py`): translate_section の冪等再実行(source_hash スキップ)・検証失敗→再試行→原文フォールバック・進捗イベント発行・personal フォーク解決(plans/02 §5.2 クエリ)・優先繰り上げの二重 enqueue 無害性。

## 18. 受け入れ基準

docs/03 §12 の全項目に加え、本書固有:

- [ ] プレースホルダ検証がプロパティテストを通過し、検証通過率 99.9% 以上・不合格ブロックが訳文として配信されない(`text_ja: null`)
- [ ] 再試行がプロンプト再構成のみで行われ、リクエストに temperature 系パラメータが存在しない(型レベル)
- [ ] system[0]/system[1] がキャッシュプレフィックス順で構成され、DeepSeek 連続バッチ 2 回目の `cached_input_tokens > 0` が usage_records で確認できる
- [ ] `PATCH /api/glossary/terms/{term_id}?dry_run=true` が §8.3 の逆引き検索と同一件数を返し、`dry_run=false` から 60 秒以内に影響ブロックの訳が差し替わる
- [ ] 2 人目のユーザーの public 論文追加で translation タスクの LLM 呼び出しが 0 回(usage_records で検証)
- [ ] 直訳が初回切替時のみ生成され(202)、2 回目以降は 200 即時。`priority_section_id` のセクションが最初に完了する
- [ ] 「開いたセクションを優先翻訳」で対象 translate_section が `yk:interactive` に繰り上がり、二重実行が起きない(claim 検証)
- [ ] 進捗の分母が `stats.translatable_blocks` と一致し、付録のオンデマンド翻訳が進捗率を変えない
- [ ] 見出し unit が訳題本文のみを持ち、「1 はじめに — Introduction」がクライアント合成で表示される
- [ ] 再翻訳が `retranslation_escalation` チェーン(primary `claude-sonnet-5`)で実行され、`translation_units.model` に記録される
