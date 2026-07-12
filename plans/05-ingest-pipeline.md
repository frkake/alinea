# 05. 取り込みパイプライン実装計画 — arXiv 解決・パーサ・ジョブステートマシン

> **対象読者と前提**: 本書は「Alinea」の取り込みパイプライン(apps/worker: Python 3.12 + arq、および apps/api の ingest 系エンドポイントの裏側)を実装するエンジニア向け。機能仕様の正は [docs/02-ingest.md](../docs/02-ingest.md)(取り込み)・[docs/01-domain-model.md](../docs/01-domain-model.md)(ドメインモデル)・[docs/08-extension.md](../docs/08-extension.md)(拡張)であり、本書はそれらを実装コードレベルまで確定させる。テーブル・カラムは [plans/02-data-model.md](02-data-model.md) の DDL、エンドポイントは [plans/03-api.md](03-api.md)(`/api` プレフィックス・全 133 本)、キュー構成・S3 レイアウトは [plans/01-architecture.md](01-architecture.md)(`alinea:interactive` / `alinea:bulk`、`sources/…` キー)、LLM 呼び出しは [plans/04-llm-providers.md](04-llm-providers.md)(`alinea_llm.LLMRouter`、8 タスク)を正として一致させる。基盤計画に不足がある箇所は「⚠ 基盤への追加要求」(§13)に集約した。

## 1. 全体像とコード配置

### 1.1 パイプライン概観

```
拡張(3a) ── GET /api/ingest/check ──► apps/api(書誌プレビュー・LaTeX有無・重複判定)
拡張(3a) ── POST /api/ingest/arxiv | /api/ingest/pdf ──► apps/api
  └─► papers UPSERT + library_items INSERT + jobs INSERT(kind='ingest') + arq enqueue(alinea:bulk)
apps/worker(worker-bulk)... ingest_paper(job_id)
  queued → fetching → parsing → structuring → translating_abstract → readable
        → translating_body(translate_section ジョブ群) → complete
  (任意段階から failed(stage, reason) / 翻訳段のみ waiting_quota)
完了 ─► notifications INSERT(kind='translation_complete') + Redis PUBLISH events:user:{uid}
```

### 1.2 コード配置(確定)

パーサ・アンカー処理は api / worker 共有ライブラリ `libs/alinea_core`(import 名 `alinea_core`。plans/01 §2.3)に置く。LLM 呼び出しのみ `packages/llm`(`alinea_llm`。plans/04 §2)を使う。

```
libs/alinea_core/src/alinea_core/
  arxiv/
    ids.py             # URL/ID 正規化(§3.1)
    metadata.py        # メタデータ API + OAI-PMH ライセンス(§3.2, §3.3)
    licenses.py        # ライセンス正規化表(§3.3)
    fetch.py           # HTML/PDF/e-print 取得 + レート制限(§3.4, §3.5)
  parsing/
    model.py           # Block / Inline / Section の Pydantic モデル(plans/02 §3.2 と同型)
    block_ids.py       # ブロック安定 ID 生成(§4.4)
    carryover.py       # リビジョン間 ID 引き継ぎ(§4.5)
    html_parser.py     # arXiv HTML / ar5iv パーサ(§4)— parser_version 'html-1.3.0'
    latex_parser.py    # LaTeX パーサ(§5, M2)— parser_version 'latex-1.3.0'
    pdf_parser.py      # PDF パーサ(§6)— parser_version 'pdf-1.2.0'
    pdf_sync.py        # 品質 A の page+bbox 同期(§4.6)
  ingest/
    dedupe.py          # 重複検知(§7)
    thumbnail.py       # サムネイル生成(§8)
    bib_estimate.py    # アップロード PDF の書誌推定(§9.3)
    joblog.py          # 処理ログ・タイムライン記録(§10)
    progress.py        # 進捗計算・SSE 発行(§2.2)
apps/worker/src/alinea_worker/
  settings.py          # InteractiveWorker / BulkWorker(plans/01 §8.3 の arq 起動対象)
  tasks/ingest.py      # arq タスク ingest_paper(ctx, job_id)
  tasks/translate.py   # arq タスク translate_section(ctx, job_id)(§11)
  cron.py              # check_quality_promotions(§12.3)ほか
apps/api/app/routers/ingest.py   # /api/ingest/*(plans/03 §3)
apps/api/app/routers/papers.py   # /api/papers/*(plans/03 §4)
```

### 1.3 マイルストーン対応(docs/10)

| マイルストーン | 本書の範囲 |
|---|---|
| M0 | §2 ステートマシン、§3 arXiv 解決、§4 arXiv HTML パーサ(主経路・品質 A)、§7〜§12 |
| M1 | §6 PDF パイプライン(品質 B)、§9 拡張 PDF 直接送信、B→A 昇格提案(§12.3) |
| M2 | §5 LaTeX パーサ(品質 A の主経路へ昇格。取得優先順位 LaTeX > HTML > PDF が最終状態) |

## 2. ジョブステートマシン

### 2.1 stage 値と遷移(確定)

`jobs` テーブル(plans/02 §4.13)の `kind='ingest'` 行を使う。`stage` の値域は docs/02 §5.1 の逐語どおり **8 値+failed**:

```
queued → fetching → parsing → structuring → translating_abstract → readable → translating_body → complete
```

- 任意の段階から `status='failed'`(`stage` は失敗した段階のまま保持し、`error` に理由を格納 — UI は「段階名+理由+再試行手段」の 3 点セットで表示する。plans/01 §9.2)。
- `translating_body` でクォータ超過時のみ `status='waiting_quota'`(plans/03 §17.4。§2.6)。
- 各段階の意味と、拡張パイプライン表示(3a「✓ 書誌 → ✓ 構造化 → 翻訳中 12%」)との対応:

| stage | 処理内容 | 完了時の外部化 |
|---|---|---|
| `queued` | arq 起床待ち | — |
| `fetching` | メタデータ確定+ソース取得(§3.5)→ `source_assets` INSERT + S3 保存 | 拡張「✓ 書誌」 |
| `parsing` | ソース → 内部ブロックモデル(パーサ実行。§4/§5/§6) | — |
| `structuring` | ブロック ID 付与・相互参照解決・図アセット保存・`document_revisions` INSERT・`block_search_index` 展開・PDF 位置同期(§4.6)・サムネイル(§8)・リアンカー(reingest 時) | 拡張「✓ 構造化」+タイムライン 2 段目 |
| `translating_abstract` | アブスト訳(`papers.abstract_ja`)+✦3行要約(`papers.summary_lines`)+提案タグ(`library_items.suggested_tags`) | 1d カードに要約表示(20 秒目標) |
| `readable` | 第 1 本文セクションを **ingest ジョブ内で直接翻訳**(60 秒目標を確実化) | 「読み始める」可(部分読書) |
| `translating_body` | 残セクションの `translate_section` ジョブ群を張り出し、完了を集計 | 「翻訳中 68%」/「§3 まで読めます」 |
| `complete` | `status='succeeded'`・タイムライン 3 段目・通知発火(§12) | 通知「翻訳完了」 |

- **決定**: readable 段の「最初のセクション」= 参考文献・付録を除く本文セクションの先頭 1 つ(通常 §1 Introduction)。ingest ジョブが `translate_section` と同一の内部関数を直接呼んで訳す。理由: ジョブ enqueue の往復を挟むと p50 60 秒(docs/09 §1)を外しやすいため。**境界ケース**: 本文セクションが 0 個(付録・参考文献のみ等)の場合は readable 段を no-op で通過し、`translating_body` で張り出すジョブも 0 件なら §11.3 の完了検知を経ずにその場で `complete` に遷移する。
- 2 人目以降のユーザー(既存 Paper + 既存 complete リビジョン + shared 翻訳あり)の ingest ジョブは、全段階の冪等チェック(§2.3)が即スキップになり数秒で `complete` に達する(docs/03 §12「翻訳待ちなしで読める」)。

### 2.2 進捗値(`jobs.progress`)の確定

plans/03 §1.7 `PipelineState` の定義「0–100(translating_body 中は翻訳済ブロック比)」に従い、次の固定マップとする:

| stage | progress |
|---|---|
| queued | 0 |
| fetching | 10 |
| parsing | 20 |
| structuring | 35 |
| translating_abstract | 50 |
| readable | 55 |
| translating_body | `floor(訳済ブロック数 ÷ translatable_blocks × 100)`(`document_revisions.stats.translatable_blocks` が分母。値域 0–100 で、55 未満にもなる — 3a の「翻訳中 12%」はこの値) |
| complete | 100 |

- 固定値は各 stage の**開始(遷移)時**に設定する(fetching 実行中は 10 のまま。段内の連続的な進捗表示は translating_body のみ)。
- 進捗イベントは stage 遷移時+`translating_body` 中は 5% 刻み(plans/03 §21.2)で Redis Pub/Sub `events:user:{user_id}` に `job.progress` を PUBLISH し、Redis Stream `events:log:{user_id}`(MAXLEN ~1000)にも書く(plans/01 §5)。
- `readable_upto`(「§3 まで読めます」): 保存しない導出値。`translation_units` を先頭セクションから走査し、「対象ブロックが全訳済みの連続セクション」の最後の節番号を `§{n}` 形式で返す(`alinea_core.ingest.progress.readable_upto(set_id, revision)`)。

### 2.3 冪等性 — 段階ごとの再開条件(checkpoint カラムは持たない)

**決定: 段階再開はドメインテーブルの存在チェックで行い、専用 checkpoint は持たない。** plans/02 の `jobs` DDL に checkpoint カラムが無く、各段の出力はすべてドメインテーブル(一意制約付き)に落ちるため、再実行時に「出力が既にあればスキップ」で二重処理が構造的に起きない(docs/09 §8)。

| stage | スキップ判定(再実行時に真ならスキップ) | 一意性の担保 |
|---|---|---|
| fetching | `source_assets` に `(paper_id, source_version, kind)` の行があり S3 オブジェクトが存在 | S3 キーが決定的(`sources/{paper_id}/{source_version}/…`。plans/01 §7.1) |
| parsing / structuring | `document_revisions` に `(paper_id, source_version, parser_version)` の行が存在 → 既存リビジョンを再利用 | `uq_document_revisions_paper_ver_parser` |
| translating_abstract | `papers.abstract_ja IS NOT NULL AND papers.summary_lines IS NOT NULL` | 単一行 UPDATE |
| readable / translating_body | `translation_units` を `(set_id, block_id)` + `source_hash` 照合で UPSERT(既訳スキップ) | `uq_translation_units_set_block` |
| translate_section の張り出し | `pg_advisory_xact_lock(hashtext(set_id::text))` 内で既存 queued/running ジョブを SELECT してから不足分のみ INSERT | アドバイザリロック |
| complete | 通知は「同一 job_id からの translation_complete が既に存在するか」を `notifications.payload->>'job_id'` で確認して 1 回だけ INSERT | アプリ層チェック |

- API 層の冪等性: `POST /api/ingest/*` の `Idempotency-Key`(Redis 24 時間、同キー再送は初回レスポンス再生。plans/03 §3.2)。
- **二重実行の無害化**: 優先繰り上げ(§2.5)の二重 enqueue は claim(`UPDATE jobs SET status='running', started_at=now(), attempt=attempt+1 WHERE id=$1 AND status='queued' RETURNING id` — 0 行なら即 no-op)で先着 1 実行を保証する(plans/01 §4.5)。

### 2.4 リトライとエラー分類

- 失敗時: `attempt < max_attempts`(既定 3。plans/02)なら `status='queued'` に戻し、arq の `defer_by` で指数バックオフ **30 秒 → 2 分 → 8 分**(attempt 1/2/3。plans/01 §4.5)。`attempt = max_attempts` 到達で `status='failed'` 確定。手動「再試行」は attempt=0 の新規 jobs 行(§2.7 の reingest と同経路)。
- エラー分類(`alinea_core.arxiv.fetch.FetchError.kind` / パーサ例外):

| code(jobs.error 先頭・Problem code) | 例 | リトライ |
|---|---|---|
| `network_error` | 接続断・DNS 失敗 | する |
| `upstream_5xx` | arXiv 503 | する |
| `rate_limited` | arXiv 429 | する(バックオフ延長: 次回 `defer_by` を 2 倍) |
| `source_not_found` | メタデータ API が 0 件 / HTML・PDF とも 404 | しない(即 failed) |
| `no_text_layer` / `document_incomplete` | テキストレイヤ無し、または可視本文が不足する PDF | 通常抽出後に最終 OCR 候補を 1 回試す。OCR 候補も不完全なら `document_incomplete` として再試行しない |
| `ocr_engine_unavailable` / `ocr_language_unavailable` / `ocr_language_invalid` / `ocr_output_too_large` | Tesseract・言語データ・入力設定・出力上限の決定的な問題 | しない。機械判定可能な code を保持して failed |
| `ocr_timeout` / `ocr_crashed` / `ocr_lifecycle` / `ocr_failed` | OCR 子プロセスの期限超過・異常終了・後始末失敗・一時的実行失敗 | する。子プロセスを terminate/kill/reap してから既定バックオフ |
| `parse_error` | パーサ内部例外 | しない(パーサのバグ。Sentry 送信) |
| `llm_chain_exhausted` | 翻訳の全プロバイダ失敗(plans/04 §9) | する(arq 再試行がそのまま docs/09 §2 の 3 回) |

- **部分成功は正の状態**: 図 1 枚の切り出し失敗・キャプション対応付け失敗・PDF 同期率低下はジョブを失敗させず、`jobs.log` に `level='warn'` で記録して次へ進む(plans/01 §4.5)。

### 2.5 優先繰り上げ(開いたセクションを優先翻訳)

- トリガ: `POST /api/translation-sets/{set_id}/prioritize`(plans/03 §7.4。ビューアがセクション入場時に呼ぶ)。
- 実装(apps/api 内・同期):
  1. `UPDATE jobs SET priority = priority + 100 WHERE kind='translation' AND status='queued' AND payload->>'set_id' = :set_id AND payload->>'section_id' = :section_id RETURNING id`(+100 は plans/02 §4.13 の規約)。
  2. 返った job_id を **`alinea:interactive` キューへ二重 enqueue**(元の `alinea:bulk` 側はそのまま)。先着 claim が実行し後着は no-op(§2.3)。
- 直訳オンデマンド(plans/03 §7.3)・付録オンデマンド(§7.5)・「この表を翻訳」は最初から `alinea:interactive` + `priority=100` で投入する(plans/01 §3.2)。

### 2.6 waiting_quota(翻訳段のみ停止)

- `translating_body` 開始前に月次クォータ(全文翻訳本数。`usage_records` 集計 — plans/02 §4.13)を確認し、超過なら `jobs.status='waiting_quota'` で停止する(stage は `translating_body` のまま)。取り込み自体(書誌・構造化・アブスト訳・readable)は完了済みなので失敗にしない(plans/03 §17.4)。
- SSE には `event: progress` の `status: "waiting_quota"` として流す(plans/03 §21.2)。
- 再開トリガ: BYOK キー保存 API のハンドラが `UPDATE jobs SET status='queued' WHERE user_id=:uid AND status='waiting_quota'` して再 enqueue(自動再開。plans/03 §17.4)。

### 2.7 DB `jobs.kind` と API `Job.kind` の対応(確定)

plans/02 の CHECK(`ingest/translation/article/figure/vocab/resource_meta/export`)と plans/03 の API 表示 kind は粒度が異なる。**DB は plans/02 の 7 値を正、API kind は次の導出**とする。**本表が `ingest` / `translation` の導出の唯一の正**であり、plans/06 の対応表(§3.1)もこれに従う(article/figure/vocab 等は担当計画書が同じ規則で定義する — plans/07 §1 参照):

| DB kind | payload の判別キー | API `Job.kind` |
|---|---|---|
| `ingest` | `payload.mode = 'initial'`(既定) | `ingest` |
| `ingest` | `payload.mode = 'reingest'` | `reingest` |
| `translation` | `payload.reason = 'initial'`(セット全体の初回) | `translation_set` |
| `translation` | `payload.reason = 'literal'`(直訳セットのオンデマンド生成) | `translation_set` |
| `translation` | `payload.reason = 'on_demand'`(付録等のセクション単位オンデマンド) | `section_translate` |
| `translation` | `payload.reason = 'table'`(「この表を翻訳」— plans/03 §7.5 のブロック指定版) | `section_translate` |
| `translation` | `payload.reason = 'retranslate'`(通常再翻訳) | `retranslate_unit` |
| `translation` | `payload.reason = 'instructed'`(指示つき再翻訳) | `retranslate_unit` |
| `translation` | `payload.reason = 'glossary_change'`(訳語変更の影響ブロック再翻訳) | `glossary_apply` |

- **導出規則(確定)**: セット全体の生成(`initial` / `literal`)→ `translation_set`、セクション/ブロック単位の部分翻訳(`on_demand` / `table`)→ `section_translate`、単一ユニット起因の再翻訳(`retranslate` / `instructed`)→ `retranslate_unit`、訳語変更起因 → `glossary_apply`。API `Job.kind` は plans/03 §1.7 の列挙の範囲に収まり、新しい API kind は増やさない。
- **決定**: 新 worker kind **`translate_set` は採用しない**(plans/06 §16-5 の新設要求は取り下げ。plans/02 の CHECK 7 値を変えない)。直訳セットのオンデマンド生成も `kind='translation'` + `payload.reason='literal'` のセクションジョブ群として張り出し、専用の親ジョブは設けない — 進捗集計・完了検知は §11.3 と同じ set_id 集計方式(`payload->>'reason'='literal'` で絞る)を使う。

- ingest ジョブの `payload`(`IngestJobPayload`、Pydantic):

```json
{
  "mode": "initial",
  "source": "arxiv",
  "arxiv_id": "2209.03003",
  "requested_version": "v3",
  "url": "https://arxiv.org/abs/2209.03003v3",
  "library_item_id": "0197…"
}
```

`source ∈ {"arxiv", "pdf_upload"}`。`requested_version` は URL にバージョン指定が無ければ `null`(最新を取る。docs/02 §2)。

- arq タスク関数名は `ingest_paper` / `translate_section`(plans/01 §4.3 の名称)とし、DB kind とは独立(arq ペイロードは job_id のみ — plans/01 §4.1)。

## 3. arXiv 解決

### 3.1 URL 正規化(全パターン・実装コード)

`alinea_core/arxiv/ids.py`。docs/02 §2 の「abs / pdf / html / 旧形式をすべて `arxiv_id + version` に解決」を次で確定する:

```python
import re
from dataclasses import dataclass

# 新形式 ID: 2007-04 以降。YYMM.NNNN(2014-12 以前)/ YYMM.NNNNN(2015-01 以降)
_NEW_ID = r"\d{4}\.\d{4,5}"
# 旧形式 ID: archive(.subject)?/YYMMNNN。例 cs/9901002, math.GT/0309136, cond-mat/0207270
_OLD_ID = r"[a-z][a-z-]+(?:\.[A-Z]{2})?/\d{7}"
_ID = rf"(?P<id>{_NEW_ID}|{_OLD_ID})"
_VER = r"(?:v(?P<ver>\d+))?"
_HOST = r"(?:www\.|export\.|browse\.)?arxiv\.org"
_AR5IV = r"(?:ar5iv\.labs\.arxiv\.org|ar5iv\.org)"

_PATTERNS: list[re.Pattern[str]] = [
    # 1) https://arxiv.org/abs/2209.03003v3(+ ?query #fragment)
    re.compile(rf"^https?://{_HOST}/abs/{_ID}{_VER}(?:[?#].*)?$"),
    # 2) https://arxiv.org/pdf/2209.03003v3(.pdf 拡張子は任意)
    re.compile(rf"^https?://{_HOST}/pdf/{_ID}{_VER}(?:\.pdf)?(?:[?#].*)?$"),
    # 3) https://arxiv.org/html/2209.03003v3
    re.compile(rf"^https?://{_HOST}/html/{_ID}{_VER}(?:[?#].*)?$"),
    # 4) https://arxiv.org/e-print/2209.03003v3 ・ /format/…
    re.compile(rf"^https?://{_HOST}/(?:e-print|format)/{_ID}{_VER}(?:[?#].*)?$"),
    # 5) ar5iv ミラー(バージョン指定は無視されることがある)
    re.compile(rf"^https?://{_AR5IV}/(?:html|abs)/{_ID}{_VER}(?:[?#].*)?$"),
    # 6) テキスト形式 "arXiv:2209.03003v3"(拡張の Cite as 行・手入力)
    re.compile(rf"^(?i:arxiv):{_ID}{_VER}$"),
    # 7) 素の ID("2209.03003v3" / "cs/9901002")
    re.compile(rf"^{_ID}{_VER}$"),
]

@dataclass(frozen=True)
class ArxivRef:
    arxiv_id: str          # バージョン抜き。papers.arxiv_id と同値(例 '2209.03003')
    version: int | None    # v 指定が無ければ None(=最新を取る)

def parse_arxiv_url(raw: str) -> ArxivRef | None:
    s = raw.strip()
    # ホスト部のみ小文字化(旧形式 ID の '.GT' 等は大文字を保持)
    s = re.sub(r"^(https?://)([^/]+)", lambda m: m.group(1) + m.group(2).lower(), s)
    for pat in _PATTERNS:
        if m := pat.match(s):
            ver = m.group("ver")
            return ArxivRef(m.group("id"), int(ver) if ver else None)
    return None
```

- `GET /api/ingest/check` は `parse_arxiv_url` が None のとき URL 末尾 `.pdf` / `Content-Type: application/pdf`(拡張がタブ情報から判定して `kind` ヒントを送る必要はない — check はサーバー判定のみ)で `kind: "pdf"`、それ以外は `kind: "unsupported"` を返す(plans/03 §3.1)。
- 正規化の単体テストは上記 7 パターン × { バージョン有/無, http/https, export./www., query 付き } の直積+旧形式 3 例(`cs/9901002` `math.GT/0309136` `cond-mat/0207270`)を固定ケースとする。

### 3.2 メタデータ API(エンドポイント・レスポンス対応表)

- エンドポイント: `GET https://export.arxiv.org/api/query?id_list={arxiv_id}{vN?}&max_results=1`(Atom XML。タイムアウトは `GET /api/ingest/check` と fetching 段では 8 秒、`POST /api/ingest/arxiv` の同期呼び出しのみ 4 秒 — 本節末尾の決定)。
- パース: `xml.etree.ElementTree`(名前空間 `atom=http://www.w3.org/2005/Atom`, `arxiv=http://arxiv.org/schemas/atom`)。
- Atom → `papers` カラム対応表(確定):

| Atom 要素 | papers カラム | 変換規則 |
|---|---|---|
| `entry/title` | `title` | 改行→空白、連続空白圧縮 |
| `entry/author/name`(複数) | `authors` | `[{"name": "Xingchao Liu"}, …]`(JSONB) |
| `entry/summary` | `abstract` | 前後 strip・改行→空白 |
| `entry/published` | `published_on` | 日付部分(v1 の投稿日) |
| `entry/category/@term`(複数) | `arxiv_categories` | `arxiv:primary_category/@term` を配列先頭に置き、残りを出現順(重複除去) |
| `entry/arxiv:doi` | `doi` | そのまま(無ければ NULL) |
| `entry/arxiv:journal_ref` | `venue` | あればそのまま |
| `entry/arxiv:comment` | `venue`(フォールバック) | §3.2.1 の会議名正規表現で抽出。抽出できなければ venue=NULL |
| `entry/id`(例 `http://arxiv.org/abs/2209.03003v3`) | `latest_version` | 末尾 `v(\d+)` を `'v3'` 形式で保存 |

- 応答スナップショットは S3 `sources/{paper_id}/{source_version}/metadata.json`(plans/01 §7.1)に保存し、`source_assets` に `kind='metadata_api'` で記録する。
- Redis キャッシュ: `ingest:meta:{arxiv_id}` に正規化済み JSON を TTL 86,400 秒で保存。`GET /api/ingest/check` と `POST /api/ingest/arxiv` が共用する。
- **決定**: `POST /api/ingest/arxiv` は保存後 3 秒以内のカード表示(docs/02 §5.2)のため、キャッシュがあればそれで Paper を作成、無ければ同期でメタデータ API を呼ぶ(タイムアウト 4 秒)。同期取得も失敗した場合は `title = 'arXiv:{id}(取得中)'` の仮行で INSERT し、fetching 段が確定値で UPDATE する(3 秒以内のカード表示を優先。P3 的にも仮タイトルは即座に置き換わる)。

#### 3.2.1 venue 抽出正規表現(comment フォールバック)

```python
_VENUE = re.compile(
    r"\b(ICLR|ICML|NeurIPS|NIPS|CVPR|ICCV|ECCV|WACV|ACL|EMNLP|NAACL|COLING|AAAI|IJCAI|"
    r"KDD|WWW|TheWebConf|SIGIR|SIGGRAPH(?:\s+Asia)?|SODA|STOC|FOCS|COLT|AISTATS|UAI|"
    r"CoRL|RSS|ICRA|IROS|INTERSPEECH|ICASSP)\s*[',]?\s*((?:19|20)\d{2})\b")
# 例: "ICLR 2023 (spotlight)" → venue='ICLR 2023'
```

### 3.3 ライセンス取得と正規化表

Atom API はライセンスを返さないため、**OAI-PMH** で取得する(1 論文 1 リクエスト・fetching 段で実行):

```
GET https://export.arxiv.org/oai2?verb=GetRecord&identifier=oai:arXiv.org:{arxiv_id}&metadataPrefix=arXiv
→ <metadata><arXiv …><license>http://creativecommons.org/licenses/by/4.0/</license>…
```

正規化(`alinea_core/arxiv/licenses.py`。比較前に scheme を除去し末尾 `/` を落とす):

| license URL(scheme・末尾スラッシュ非依存) | `papers.license` |
|---|---|
| `creativecommons.org/licenses/by/4.0` | `cc-by-4.0` |
| `creativecommons.org/licenses/by-sa/4.0` | `cc-by-sa-4.0` |
| `creativecommons.org/licenses/by-nc/4.0` | `cc-by-nc-4.0` |
| `creativecommons.org/licenses/by-nc-sa/4.0` | `cc-by-nc-sa-4.0` |
| `creativecommons.org/licenses/by-nd/4.0` | `cc-by-nd-4.0` |
| `creativecommons.org/licenses/by-nc-nd/4.0` | `cc-by-nc-nd-4.0`(⚠ §13-3: CHECK 追加要求。追加まで暫定 `unknown`) |
| `creativecommons.org/publicdomain/zero/1.0` | `cc0` |
| `arxiv.org/licenses/nonexclusive-distrib/1.0` | `arxiv-nonexclusive` |
| CC 3.0/2.5/2.0 系・assumed-1991-2003・上記以外・取得失敗 | `unknown`(`jobs.log` に `warn` で原文字列を記録) |

- `unknown` は転載可否判定で最も安全側(転載不可。docs/09 §5.2)に倒れるため、未知値の既定として妥当。
- 2a 情報パネルのライセンスカード文言(「CC BY 4.0 — 図表転載可」)は `papers.license` からフロントが導出する(本書の責務は正規化保存まで)。

### 3.4 LaTeX ソース有無判定(保存前の「品質レベル A 見込み」)

`GET /api/ingest/check` の `latex_available`(plans/03 §3.1)の実装:

```python
async def latex_available(ref: ArxivRef, redis: Redis, http: httpx.AsyncClient) -> bool:
    key = f"ingest:latex:{ref.arxiv_id}:{ref.version or 'latest'}"
    if (hit := await redis.get(key)) is not None:
        return hit == b"1"
    await arxiv_throttle(redis)                      # §3.5 のレート制限
    ver = f"v{ref.version}" if ref.version else ""
    resp = await http.head(
        f"https://export.arxiv.org/e-print/{ref.arxiv_id}{ver}",
        follow_redirects=True, timeout=6.0)
    # e-print は LaTeX ソースなら application/x-eprint-tar / application/x-eprint / application/gzip、
    # PDF-only 投稿なら application/pdf を返す
    ok = resp.status_code == 200 and "application/pdf" not in resp.headers.get("content-type", "")
    await redis.set(key, b"1" if ok else b"0", ex=86_400)   # 24h キャッシュ(plans/03 §3.1 の決定)
    return ok
```

- M0(HTML 主経路)でもこの判定をそのまま使う。「A 見込み」の意味は「LaTeX ソースがある = arXiv HTML も LaTeXML で生成されている可能性が高い = 品質 A」で一致する(docs/02 §2 の決定: HTML 経由の完全構造化も A)。

### 3.5 fetching 段の取得手順とレート制限

- **レート制限(確定)**: arXiv 系ホスト(export.arxiv.org / arxiv.org / ar5iv.labs.arxiv.org)への全リクエストを **全ワーカー横断で 1 リクエスト / 3.1 秒**に制限する。実装は Redis の `SET arxiv:throttle 1 NX PX 3100` をスピンで取得する `arxiv_throttle()`(取得失敗時 200ms スリープ。スピンに上限は設けず、全体の打ち切りは arq のジョブタイムアウトに委ねる)。User-Agent は環境変数 `ARXIV_USER_AGENT`(plans/01 §8.4)。
- **取得タイムアウト(確定)**: 表の #2〜#3(HTML)は接続 5 秒・全体 30 秒、#4(PDF)と #5(e-print tar)は接続 5 秒・全体 120 秒(httpx `Timeout(connect=5.0, ...)`)。超過は `network_error` としてリトライ分類(§2.4)。
- **原本サイズ境界(確定)**: HTTP は `stream()` で読み、`Content-Length` と実読 max+1 の両方を検査する。PDF/e-print は各 128MiB、HTML は 64MiB。retained S3 再読込も同じ上限の `get_bounded` を使う。超過は決定的な `source_too_large`(候補 source は次候補へフォールバック、必須 PDF/PDF upload は非リトライ終了)とし、超過 payload は S3/DB に部分保存しない。
- 取得順(M0。M2 で LaTeX を先頭に昇格):

| # | 取得物 | URL | 保存先(S3)/ source_assets.kind | 失敗時 |
|---|---|---|---|---|
| 1 | メタデータ+ライセンス | §3.2 / §3.3 | `sources/{paper_id}/{sv}/metadata.json` / `metadata_api` | リトライ分類(§2.4)。0 件は `source_not_found` |
| 2 | arXiv 公式 HTML | `https://arxiv.org/html/{id}{vN}` | `sources/{paper_id}/{sv}/arxiv.html` / `arxiv_html` | 404 か `ltx_document` 不在 → #3 |
| 3 | ar5iv HTML | `https://ar5iv.labs.arxiv.org/html/{id}` | 同上 | 404 か `ltx_document` 不在 → #5(PDF へフォールバック。`jobs.log` に warn) |
| 4 | 原文 PDF(**常に取得**) | `https://arxiv.org/pdf/{id}{vN}` | `sources/{paper_id}/{sv}/original.pdf` / `pdf` | 404 → PDF モード無効のまま続行(warn)。HTML も無い場合は `source_not_found` |
| 5 | (M2)LaTeX ソース | `https://export.arxiv.org/e-print/{id}{vN}` | `sources/{paper_id}/{sv}/latex.tar.gz` / `arxiv_latex` | 無ければ #2 へ |

- 原文 PDF を常に取得する理由: PDF モード(2a)・「⤓ 原文PDF」・品質 A の page+bbox 同期(§4.6)の 3 機能が品質 A でも PDF 実体を要求するため。
- `source_version` は取得したバージョン(メタデータの `latest_version`、URL 指定があればそれ)。`{sv}` は `'v3'` 形式。
- バージョン指定なし URL は最新を取り、既存 Paper に旧バージョンがある場合の分岐は §7(重複検知)へ。

## 4. arXiv HTML パーサ(M0 主経路・品質 A)

### 4.1 対象と前提

- 対象 HTML: arXiv 公式 HTML(2023-12 以降の論文で提供)と ar5iv。**どちらも LaTeXML 生成で CSS クラス体系(`ltx_*`)が共通**のため、単一パーサ `html_parser.py` で両対応する。ルート要素は `article.ltx_document`。
- **決定**: HTML パーサは **selectolax(lexbor バックエンド)** を使う。理由: C 実装で BeautifulSoup 比 10 倍以上高速、CSS セレクタと属性走査で本用途に十分(spec-decisions C7 の選択肢から確定)。
- `parser_version = 'html-1.3.0'`。出力は plans/02 §3.2 の `DocumentContentJson`(`quality_level: "A"`, `source_format: 'arxiv_html'`)。
- 回帰テストのフィクスチャは Rectified Flow(arXiv:2209.03003、C10 のシード論文)の公式 HTML と ar5iv HTML を `libs/alinea_core/tests/fixtures/` に凍結する。

### 4.2 DOM → ブロック対応表(完全)

セクションツリーを先に構築し(`section.ltx_section > .ltx_subsection > .ltx_subsubsection`)、各セクション直下を文書順に走査して Block 化する。

| セレクタ(文書順走査で先勝ち) | Block type | 抽出規則 |
|---|---|---|
| `section.ltx_section / .ltx_subsection / .ltx_subsubsection` | (セクション境界) | 見出し番号= `h2/h3/h4 .ltx_tag_section` のテキスト(例 `2.2`)。タイトル= `.ltx_title` から tag span を除いた残り。レベル= section=1 / subsection=2 / subsubsection=3 |
| `section.ltx_appendix` | (セクション境界) | 同上+`is_appendix: true`(付録の自動翻訳除外 — docs/03 §2 — の判定元)。番号は `A`, `B`, … |
| `h2.ltx_title_section` 等(境界内) | `heading` | セクション境界と同時に heading ブロックも生成(レベル 1–4、番号、タイトル) |
| `div.ltx_para > p.ltx_p` | `paragraph` | 子ノードを §4.3 のインライン列に変換。空段落(数式のみを含む `ltx_p` で display 数式に昇格した残り)は捨てる |
| `table.ltx_equation`, `div.ltx_equation`, `table.ltx_equationgroup` | `equation` | LaTeX= 内部 `annotation[encoding="application/x-tex"]`(`<semantics>` 内)を優先、無ければ `math/@alttext`。番号= `.ltx_tag_equation` の `(7)` から括弧を除いた `7`。label= 要素 `@id`(例 `S2.E7`)。equationgroup は行ごとに equation ブロックへ分割 |
| `figure.ltx_figure` | `figure` | caption 配下を除く安全な SVG/img を DOM 順に全列挙し、panel ごとに 1 figure block。SVG+ラスタを同一 panel の代替とみなすのは `picture`/visual/fallback wrapper または明示的な inert fallback signal がある場合だけで、直下 sibling は別 panel。相対 URL は取得元基準で解決し S3 `figures/{paper_id}/{revision_id}/{block_id}.png` へ保存。キャプション/label/図番号は先頭 panel のみ保持。 |
| `figure.ltx_table` | `table` | セル構造= 内部 `table.ltx_tabular` を HTML 文字列として保持(`content_html`)。キャプション処理は figure と同様(`span.ltx_tag_table` 除去)。label= `@id`(例 `S4.T1`) |
| `.ltx_listing`, `pre.ltx_verbatim` | `code` | 言語= 不明(`language: null`)。テキストをそのまま |
| `ul.ltx_itemize`, `ol.ltx_enumerate` | `list` | `ordered` = ol か。項目= `li.ltx_item` ごとのインライン列(入れ子リストは項目内の子リストとして再帰) |
| `blockquote.ltx_quote` | `quote` | 内部段落をインライン列に |
| `div.ltx_theorem`(`ltx_theorem_theorem/lemma/corollary/definition/proposition/remark/proof` 等) | `theorem` | `kind` = クラス接尾辞(`theorem` 等)。見出し `h6.ltx_title_theorem` は種別名+番号として保持(訳出対象 — docs/01 §4.1) |
| `figure.ltx_float.ltx_float_algorithm`, `.ltx_algorithm` | `algorithm` | 内容はレンダリングテキスト(行構造保持)+キャプション |
| `span.ltx_note.ltx_role_footnote` | `footnote`(+ 本文側に `footnote_ref` インライン) | 内容= `span.ltx_note_content` から番号マーカーを除いたインライン列。ブロックは出現セクション末尾に集約し、`label='footnote{n}'` |
| `section.ltx_bibliography ul.ltx_biblist > li.ltx_bibitem` | `reference_entry` | `label` = `@id`(`bib.bib12`)。`raw_text` = 全テキスト。構造化(著者/年/タイトル/arXiv リンク)は §4.2.1 |
| `section.ltx_abstract` | (papers.abstract の照合のみ。本文ブロックにしない) | メタデータ API の abstract を正とする |
| `.ltx_authors`, `.ltx_dates`, `.ltx_keywords`, `.ltx_pagination`, `.ltx_page_footer`, `.ltx_role_acknowledgement` 以外の acknowledgements は通常セクション扱い | (スキップ) | — |
| `.ltx_ERROR` | (スキップ) | `jobs.log` に `warn`(「LaTeXML 変換エラー要素をスキップ」+周辺テキスト 80 字) |

#### 4.2.1 reference_entry の構造化

`li.ltx_bibitem` 内の `span.ltx_bibblock` テキストに対して: ① `arXiv:{id}` / `arxiv.org/abs/{id}` を §3.1 の正規表現で検出 → `arxiv_id`(「+この論文も取り込む」(1c)の導線元)。② 年= 括弧付き `\((19|20)\d{2}\)` を優先し、無ければ raw_text を末尾から検索して最初に現れる `(19|20)\d{2}`(どちらも無ければ NULL)。③ タイトル= 引用符(`“…”` / `"…"` / `‘…’`)内文字列を優先し、無ければ `. `(ピリオド+空白)分割の 2 番目のセンテンス(要素が 2 個未満なら NULL)。④ `doi.org/…` リンク → `doi`。構造化失敗でも `raw_text` 表示で成立する(P3)。

### 4.3 インライン対応表(数式・参照・脚注)

| DOM | Inline `t` | 規則 |
|---|---|---|
| テキストノード | `text` | 連続空白を 1 個に圧縮(先頭末尾は文脈で保持) |
| `math`(`display="inline"` / 親が `.ltx_p`) | `math_inline` | LaTeX= `annotation[application/x-tex]` 優先 → `@alttext` フォールバック |
| `cite.ltx_cite` 内の `a`(href=`#bib.bib12`) | `citation` | `ref='bib.bib12'`(reference_entry の label と一致) |
| `a.ltx_ref`(href=`#S2.E7` 等) | `ref` | `kind` を id パターンで判定(§4.3.1)。`ref` = href の `#` 以降 |
| 脚注マーカー(`.ltx_note` の出現位置) | `footnote_ref` | `ref='footnote{n}'` |
| `a.ltx_url` / href が外部 URL の `a` | `url` | `href` 保持 |
| `.ltx_text.ltx_font_italic`, `em`, `.ltx_emph` | `emphasis` | 中身を再帰変換 |
| `.ltx_text.ltx_font_typewriter`, `code`, `tt` | `code_inline` | — |
| `.ltx_text.ltx_font_bold`, `strong` | `emphasis` | 太字も emphasis に正規化(Inline 8 種を増やさない — docs/01 §4.2 準拠) |

#### 4.3.1 相互参照 id パターン → ref.kind(確定)

| id 正規表現 | kind |
|---|---|
| `^S\d+(\.SS\d+)*$` / `^A\d+$` | `section` |
| `^S\d+\.E\d+$` / `^A\d+\.E\d+$` | `equation` |
| `^S\d+\.F\d+$` / `^A\d+\.F\d+$` | `figure` |
| `^S\d+\.T\d+$` / `^A\d+\.T\d+$` | `table` |
| `^Thm[a-z]+\d+$` | `theorem` |
| `^alg\d+$` / `^algorithm\d+$` | `algorithm` |
| `^footnote\d+$` | `footnote` |
| 上記以外 | `section` に縮退+`jobs.log` warn |

`ref` の解決検証(参照先ブロックの実在)は structuring 段の最終検査で行い、未解決参照は `text` インラインに縮退して warn を記録する(リンク切れをレンダラに渡さない)。

### 4.4 ブロック安定 ID 生成(実装コード)

docs/01 §4.3「セクションパス + ブロック種別 + セクション内出現順 + 内容ハッシュ(先頭 64bit)」の実装。`translation_units.source_hash`(xxhash64 hex — plans/02 §4.4)と同一のハッシュ関数を使う。

```python
# libs/alinea_core/src/alinea_core/parsing/block_ids.py
import re
import unicodedata
from collections import defaultdict
import xxhash

_WS = re.compile(r"\s+")

def normalize_for_hash(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()

def content_hash64(block: "Block") -> str:
    """内容ハッシュ(xxhash64, hex 16 桁)。translation_units.source_hash と同一値。"""
    match block.type:
        case "equation":
            basis = block.latex or ""
        case "figure" | "table":
            basis = plain_text(block.caption) + "|" + (block.asset_name or "")
        case "code" | "algorithm":
            basis = block.text or ""
        case "reference_entry":
            basis = block.raw_text
        case "heading":
            basis = (block.number or "") + " " + block.title
        case _:  # paragraph / list / quote / theorem / footnote
            basis = plain_text(block.inlines)
    return xxhash.xxh64(normalize_for_hash(basis).encode("utf-8")).hexdigest()

_TYPE_CODE = {
    "paragraph": "p", "heading": "h", "figure": "fig", "table": "tab",
    "equation": "eq", "code": "code", "list": "ls", "quote": "q",
    "theorem": "thm", "algorithm": "alg", "footnote": "fn", "reference_entry": "ref",
}

def assign_block_ids(sections: list["Section"]) -> None:
    """docs/01 §4.4 の例(blk-3-p2-a1f9 / blk-3-eq5-77c2)と同形式の ID を決定的に付与する。"""
    for sec in sections:
        counters: dict[str, int] = defaultdict(int)
        for block in sec.blocks:
            code = _TYPE_CODE[block.type]
            if block.type == "equation" and block.number:
                ordinal = block.number            # 式番号があれば式番号(blk-3-eq5-…)
            elif block.type in ("figure", "table") and block.number:
                ordinal = block.number            # 図表番号(blk-2-fig2-…)
            else:
                counters[code] += 1
                ordinal = str(counters[code])     # セクション内の種別別出現順
            h = content_hash64(block)
            block.id = f"blk-{sec.number_path}-{code}{ordinal}-{h[:4]}"
            block.source_hash = h                 # 16 桁全体は translation_units 照合に使う
```

- `sec.number_path`: 番号付きセクションは `3` / `3-1`(`.` を `-` に置換)。付録は `A` / `A-1`。無番号の特別セクションは固定名 — 参考文献 `refs`、無番号前置き節は `s{文書内出現順}`。セクション JSON の `id` は `sec-{number_path}`(docs/01 §4.4 の `sec-3` と一致)。
- ID の一意性検査: 同一 revision 内で衝突(同 path・同 code・同 ordinal・同ハッシュ 4 桁)した場合のみ末尾に `-2`, `-3` を付す(実質的に発生しないが検査は必須。`block_search_index` の `uq_block_search_index_rev_block` が最終防壁)。

### 4.5 リビジョン間 ID 引き継ぎ(carryover)

新リビジョン作成時(arXiv v 更新・parser_version 更新・reingest・B→A 昇格)、docs/01 §4.3 の「内容ハッシュ一致 → 前後関係 → 編集距離」を次の 3 パスで実装する(`parsing/carryover.py`):

```python
from rapidfuzz import fuzz

def carry_over_ids(old_blocks: list[BlockRef], new_sections: list[Section]) -> CarryOverStats:
    """一致した新ブロックに旧 ID をそのまま与える(ID は不透明識別子であり、
    パス③で内容が変わっていても旧 ID を引き継ぐ — docs/01 §4.3)。"""
    new_blocks = flatten(new_sections)
    # パス①: source_hash(16桁)完全一致。双方で一意なペアのみ確定
    by_hash_old = index_unique(old_blocks, key=lambda b: (b.type, b.source_hash))
    by_hash_new = index_unique(new_blocks, key=lambda b: (b.type, b.source_hash))
    matched = {}
    for key, ob in by_hash_old.items():
        if nb := by_hash_new.get(key):
            matched[nb.pos] = ob
            nb.id = ob.id
    # パス②: パス①の確定ペアを「アンカー対」とし、アンカー間区間で同種ブロックの
    # 個数が両側で一致する場合に限り出現順で対応付け
    for (old_seg, new_seg) in anchor_segments(old_blocks, new_blocks, matched):
        for t in BLOCK_TYPES:
            olds, news = filter_type(old_seg, t), filter_type(new_seg, t)
            if olds and len(olds) == len(news):
                for ob, nb in zip(olds, news):
                    if nb.pos not in matched:
                        matched[nb.pos] = ob
                        nb.id = ob.id
    # パス③: 残りは同一区間内・同種で fuzz.ratio(正規化テキスト) >= 90 の最良ペア
    #(貪欲・スコア降順。1 対 1 制約)
    greedy_fuzzy_match(threshold=90)
    return CarryOverStats(total=len(new_blocks), carried=len(matched))
```

- 引き継げなかった旧ブロックに紐づく注釈は Anchor の `quote`(引用スナップショット)による文字列探索でリアンカーし、失敗分は `annotations.orphaned=true`(「未配置」)にする。**リアンカーは reingest ジョブの structuring 段の最終処理として同一トランザクションで実行**する(別ジョブにしない — 決定。plans/01 の `reanchor_annotations` は本方式に統合。理由: 新リビジョン公開と注釈移行の間に不整合ウィンドウを作らないため)。
- carryover 結果(`carried / total`、リアンカー成功数・未配置数)は `jobs.log` に info で記録する。

### 4.6 品質 A の page+bbox 同期(PDF 位置同期)

2a は品質 A の論文で「同期: p.5 ≒ §2.2 Reflow」と bbox 選択チップを示すため、**品質 A でも page+bbox を導出して `block_search_index.page / bbox` に格納する**(`document_revisions.content` には入れない — 派生・再生成可能な値のため。plans/02 の「品質 B のみ」コメントの読み替えは §13-5)。

アルゴリズム(`parsing/pdf_sync.py`。structuring 段のサブステップ、原文 PDF がある場合のみ):

1. PyMuPDF で各ページの `page.get_text("words")` を取り、ページごとの正規化テキスト(NFKC・空白圧縮・小文字化)と単語 bbox 列を作る。
2. 対象ブロック(paragraph / heading / figure・table キャプション / theorem / quote / list 項目連結)を文書順に走査。各ブロックの正規化テキストの**先頭 80 字**を、直前マッチ位置以降のページから `rapidfuzz.fuzz.partial_ratio_alignment` で探索(単調性制約: マッチ位置は文書順に前進のみ)。
3. スコア ≥ 85 でマッチとし、対応区間の単語 bbox の外接矩形を `bbox [x0,y0,x1,y1]`(pt)、ページ番号(1 起点)を `page` とする。ブロックがページをまたぐ場合は開始ページ+開始ページ内の外接矩形。
4. スコア < 85 は `page/bbox = NULL`(そのブロックのチップ非表示)。同期率(マッチ数/対象数)を `jobs.log` に info で記録し、70% 未満なら warn。

- ツールバー常時表示「p.5 ≒ §2.2」は `block_search_index` の「現在表示ページに bbox を持つ最初の heading/paragraph の section_label」から API が導出する(保存しない)。

## 5. LaTeX パーサ(M2)

**決定: pandoc(JSON AST)+自前後処理。** `pandoc -f latex -t json` で AST を得て、`latex_parser.py` が AST → ブロックモデル変換・label/ref 解決・図ファイル対応付けを行う。理由: pandoc は LaTeX の環境・マクロ展開・数式抽出が実戦済みで、JSON AST が本書のブロックモデルとほぼ同型のため後処理が薄い。代替比較(1 行ずつ):

- LaTeXML: 構造保持は最良だが 1 論文数分〜数十分の変換時間と Perl 依存が VPS 常駐ワーカーに重い(HTML 経路が既に LaTeXML 産出物を利用しており重複投資)。
- 自前 TeX パーサ: マクロ・スタイル差異の裾野が広すぎ、品質 A の網羅率で pandoc に届く見込みがない。

実装要点(M2 で詳細化。本書では契約のみ確定):

- 入力: `sources/{paper_id}/{sv}/latex.tar.gz` を展開 → メインファイル判定(`\documentclass` を含み `\begin{document}` を持つ .tex。複数候補時は `ms.tex`/`main.tex`/最大サイズの順)→ `latexpand` 相当の自前 `\input`/`\include` 展開 → pandoc 3.x(`--from=latex+raw_tex`)。
- 展開は streaming tar iteration と bounded gzip read のみを使う。上限は入力 128MiB、member 10,000、1 member 64MiB、総展開 256MiB、単一 gzip 32MiB。`getmembers()`・無制限 `read()`・`gzip.decompress()` は使わない。
- 評価される全 `\includegraphics` は figure/wrapfigure に限らず本文、center/minipage、table、abstract 等から文書順に 1 宣言 1 figure block へ変換する。table の caption/表構造は table block に残し、abstract の prose は `papers.abstract` を正として除外するが画像宣言は除外しない。表示本文/raw へコマンドや asset path を漏らさない。
- pandoc AST → Block 対応: `Header`→heading / `Para`・`Plain`→paragraph / `Math DisplayMath`→equation(`\label` は前処理で捕捉した対応表から)/ `Figure`→figure(graphics ファイルを PDF ならページラスタライズ、EPS/PDF 図は `pymupdf` で 200dpi PNG 化)/ `Table`→table / `CodeBlock`→code / `BulletList`・`OrderedList`→list / `BlockQuote`→quote / `Div` の theorem 環境→theorem / `Note`→footnote / thebibliography・bbl→reference_entry。
- ID 付与・carryover・PDF 同期は §4.4〜§4.6 を共用(パーサ非依存)。`parser_version='latex-1.3.0'`, `source_format='latex'`, 品質 A。
- pandoc が変換不能(exit≠0・AST 空)なら arXiv HTML へフォールバックし `jobs.log` に warn(処理ログで判別可能 — docs/02 §2)。

## 6. PDF パイプライン(品質 B)

タブ内 PDF 送信(§9)と、arXiv で HTML が取得できなかった場合のフォールバック。PyMuPDF(fitz)を主、表セル抽出のみ pdfplumber(spec-decisions C7)。`parser_version='pdf-1.2.0'`, `source_format='pdf'`, `quality_level='B'`。数値はすべて pt(1/72 インチ)。

### 6.1 テキスト・bbox 抽出

- `page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)` で block/line/span(bbox・size・font・flags)を取得。
- 全ページの span フォントサイズの最頻値(0.1pt 丸め)を `body_size`、本文行高の中央値を `line_h` とする。
- **候補順序**: 原本 arXiv PDF・HTML 内埋め込み PDF・PDF アップロードのすべてで、まず通常の PDF テキスト抽出を行う。`no_text_layer`、または抽出後の可視本文が `document_incomplete` と判定された場合に限り、同じ PDF bytes を使う OCR 候補を最終候補として 1 回試す。通常候補を採用できた場合、無関係な parse error、図表アセット materialize の失敗では OCR を起動しない。
- **テキストレイヤ判定**: 全ページの抽出文字数合計 < `40 × ページ数` なら通常候補を `no_text_layer` とし、上記 OCR 候補へ進む。OCR 後も本文不足なら `document_incomplete` として failed にする。
- **OCR 抽出**: ページごとに `page.get_textpage_ocr(language='eng', dpi=200, full=True)` をちょうど 1 回生成し、そのページの文字数判定と `get_text("dict", …, textpage=ocr_textpage)` の双方に同一 `TextPage` を渡す。ページ処理終了時に解放し、全ページ分を保持しない。OCR は可視テキストと bbox の復元だけを目的とし、§6.8 の数式 OCR(LaTeX 化)は引き続き行わない。
- **実行隔離と上限**: OCR は worker から kill 可能な spawn 子プロセスで実行する。既定 deadline は 300 秒、子プロセスには CPU・アドレス空間・ファイルサイズ・open files の上限を課し、親子間の結果 payload は 160MiB を上限とする。deadline、キャンセル、異常終了時は terminate → kill → join により必ず reap してから終了する。子へ渡すのは PDF bytes と検証済み language、親へ戻すのは parser 結果のみとする。
- **readiness**: API `/api/readyz` と worker 起動時に Tesseract binary と要求言語 traineddata(既定 `eng`)を検査する。OCR 不可は通常のテキスト PDF を処理できるためサービス全体を unready にせず、診断を `pdf_ocr` として公開・記録する。実際に OCR が必要な job は上表の安定 code で失敗する。
- **再開の同一性**: OCR 候補を採用した checkpoint と `document_revisions.stats` に `candidate_identity={"kind":"pdf_ocr","version":"pdf-ocr-1.0.0","language":"eng"}` を保存する。再開時は完全一致した OCR mode/language でのみ再解析・再利用し、改ざんまたは不明な identity は `parse_error` とする。OCR 導入前の非 OCR checkpoint は identity が無くても後方互換で再開できる。

### 6.2 ヘッダ・フッタ・ページ番号の除去

- 候補帯: ページ上端から 56pt 以内(y1 ≤ 56)または下端から 48pt 以内(y0 ≥ height−48)の行。
- 正規化テキスト(数字を `#` に置換)が**全ページの 60% 以上で同位置(y 中心 ±4pt)に反復**する行を除去(ランニングヘッダ「Published as a conference paper at ICLR 2023」等)。
- 単独の `^\d{1,4}$` 行(ページ番号)は反復条件なしで除去。

### 6.3 段組み判定と読み順復元

1. 本文領域(ヘッダフッタ除去後)の各行 bbox について、ページ中央 x_c = width/2 の**中央帯 `[x_c−0.12·width, x_c+0.12·width]`** を横切らない行の比率 r を計算。
2. r ≥ 0.85 かつ、左右クラスタ間の空白(ガター)幅 ≥ 16pt が全行で確保されるなら **2 段組**。それ以外は 1 段組。判定はページごと(タイトルページは 1 段+以降 2 段が典型)。
3. 読み順: 2 段組は「左列を y 昇順 → 右列を y 昇順」。列への割当ては行中心 x で決める。図表領域(§6.6)は本文フローから除外して列走査後に文書順へ挿入(アンカー= 領域上端の y)。

### 6.4 行 → 段落の組み立て

- 同一列内で、行間ギャップ ≤ 0.9 × line_h かつ左端差 ≤ 8pt なら同一段落に連結。
- 段落境界: 行間ギャップ > 0.9 × line_h、または新行の字下げ(左端が段落左端より 8pt 超右)。
- ハイフネーション結合: 行末 `-` +次行先頭が小文字 → ハイフンを除去して連結。それ以外の行連結は空白 1 個。
- 段落 bbox = 構成行 bbox の外接矩形。`page` = 開始行のページ。**ページまたぎ段落**(列末が文途中で終わり、次ページ先頭行が小文字/接続語で始まる)は 1 ブロックに連結し、page/bbox は開始側。

### 6.5 見出し検出とセクションツリー構築

行(段落化前)が次のいずれかを満たせば見出し候補:

- フォントサイズ ≥ body_size + 1.4pt、または bold フラグ(`span.flags & 2**4`)かつサイズ ≥ body_size。
- かつ番号パターン `^(?:(\d+(?:\.\d+){0,3})|(?:Appendix\s+)?([A-Z]))[.\s]+\S` にマッチ、または全大文字比率 ≥ 0.7 の短行(≤ 60 字)。

確定規則: 番号パターンにマッチした候補のみセクション見出しとし、深さ= 番号のドット数+1(`2.2` → level 2)。`Abstract` / `References` / `Acknowledg(e)ments` / `Appendix` は無番号でも固定認識(References 以降は §6.9、Appendix 以降のセクションに `is_appendix: true`)。番号なし候補は段落先頭の強調とみなし heading にしない(誤検出で目次が壊れる方を避ける)。

### 6.6 図領域の検出・切り出しとキャプション対応付け

1. **領域検出**: `page.get_image_info(xrefs=True)` のラスタ画像 bbox と、`page.get_drawings()` のベクタ描画を**距離 12pt 以内で連結成分クラスタリング**した bbox の和集合を図候補領域とする(面積 < 1,600pt²(約 40×40)は装飾として無視)。
2. **キャプション検出**: 正規表現 `^(Figure|Fig\.|Table)\s*~?\s*(\d+|[IVXL]+)\s*[.:]` で始まる段落。図キャプションは領域の**下 90pt 以内**、表キャプションは**上下 90pt 以内**で、水平方向の重なり ≥ 50% の最近接領域と対応付ける。
3. **切り出し**: 対応付いた領域(キャプションを含まない)を `page.get_pixmap(clip=bbox, dpi=200)` で PNG 化し `figures/{paper_id}/{revision_id}/{block_id}.png` へ保存。figure ブロック(キャプション= インライン列、`number`= キャプション番号、`page/bbox`= 領域)を生成。
4. キャプションだけあって領域が見つからない場合: figure ブロックは画像なし(キャプションのみ)で生成し warn。領域だけの場合: キャプションなし figure(number は採番しない)。到達度(図総数・対応付け成功数)は `document_revisions.stats` と `jobs.log` に記録。

### 6.7 表

- 「Table n」キャプションに対応する領域で `page.find_tables()`(PyMuPDF)→ 失敗時 pdfplumber `page.extract_tables()` を試行。セル抽出に成功したら HTML(`<table>…`)へシリアライズして `table` ブロックの `content_html` に格納。両方失敗したら領域を図と同様に画像化して `table`(画像+キャプション)にする(docs/01 §4.1「セル構造またはレンダリング画像」)。

### 6.8 数式

- 表示数式ヒューリスティクス: 列内で中央寄せ(行中心と列中心の差 ≤ 6pt)かつ数式記号(`∑∫∂√±≤≥≈∈∀∃αβγ…=+−/^_{}`)比率 ≥ 0.25 の行群。行末 `\((\d+)\)$` があれば式番号。
- **決定: v1 では数式 OCR(LaTeX 化)を行わず、領域を 200dpi で画像切り出しして `equation` ブロック(`latex: null` + 画像アセット参照+番号)にする。** docs/02 §3 は「認識できれば LaTeX 化、できなければ画像」の両方を許しており、OCR の誤 LaTeX は KaTeX 描画破綻を生むため画像が安全側。数式 LaTeX 化率(B の到達度)は 0% として stats/処理ログに正直に記録する。
- インライン数式は検出しない(text のまま。品質 B の明示された限界)。

### 6.9 参考文献

- `References` / `Bibliography` 見出し以降の段落をエントリ分割: ① `^\[\d+\]` マーカーがあればそれで分割。② 無ければぶら下げインデント(先頭行より 10pt 以上右の継続行)で分割。各エントリを `reference_entry`(`raw_text`+§4.2.1 と同じ構造化試行)にする。`label` は `[12]` 形式があれば `bib-12`、なければ `bib-{出現順}`。

### 6.10 stats と品質記録

`document_revisions.stats` に `{"pages": 24, "figures": 8, "tables": 4, "blocks": 412, "translatable_blocks": 388, "columns": 2, "ocr": false, "pdf_sync_rate": null, "figure_caption_match_rate": 0.88, "equation_latex_rate": 0.0}` を格納する(タイムライン 2 段目「(24p / 図8 / 表4)」と処理ログの到達度表示の源泉。pages はどのパーサでも原文 PDF から、PDF が無い HTML 経路では NULL)。OCR 採用時は `ocr: true` と上記 `candidate_identity` を加える。

全 source_format 共通で、採用した canonical source の `storage_key` と SHA-256 を `selected_source`、parser 出力直後(未解決参照の縮退・asset key 公開前)の canonical JSON SHA-256 を `parsed_content_sha256`、永続化する最終 `content` の canonical JSON SHA-256 を `revision_content_sha256`、全 display asset の block ID・canonical key・SHA-256・byte size を `figure_asset_manifest` に記録する。既存 revision の再利用・parsing/structuring checkpoint 再開では、retained source から再parseした候補を含めてこれらを完全一致で検証し、identity 不明・source/parser output/final content 不一致なら再利用しない。manifest と一致する図表 object の欠損・破損だけは採用候補の検証済みcacheから同じkeyへ修復し、object read は manifest byte size を上限とする ContentLength 事前検査 + max+1 streaming 検査でメモリを制限する。

同系列の旧 parser version を持つ parsing/structuring checkpoint は破損扱いせず stale として無視し、現行 parser で再選択して旧 revision を保存したまま現行 revision を 1 件だけ作成/再利用する。現行 version の identity 不一致・不正 checkpoint は `parse_error` のまま。structuring の DB COMMIT 試行後に結果が不明な場合は独立 session で revision を照合し、存在時または照合不能時は figure/card/retina を削除しない。確実な不存在時だけ cancellation-safe cleanup する。revision 確定後、翻訳開始前に原本 bytes、展開 archive、binary figures、materialized payload、parser model を解放し、`DocumentContent` と小さい identity/diagnostics だけを保持する。

## 7. 重複検知と統合

判定順は docs/02 §6 の逐語: **① arXiv ID(バージョン無視)→ ② DOI → ③ PDF SHA-256 → ④ ファジー一致**。

### 7.1 実行タイミングと完全一致(①〜③)

- **① / ②**: `POST /api/ingest/arxiv` 時に同期実行。`papers.arxiv_id`(`uq_papers_arxiv_id`)→ `papers.doi` の順で SELECT。Paper が既存で**同一ユーザーの library_item も既存**なら **409 duplicate**(既存の status / progress / last_position 入り Problem — plans/03 §3.2)を返し、拡張は状態 3 を描く。Paper 既存・library_item 未所持なら library_item を作って 202(ingest ジョブは冪等スキップで即完了)。
- **③**: `POST /api/ingest/pdf` の受信ストリームで SHA-256 を計算し、`papers.pdf_sha256 + owner_user_id` で照合(→ §13-2 の一意インデックス変更)。同一ユーザー同一ハッシュは 409、他ユーザーは別 Paper(private は共有しない — plans/03 §3.3)。
- **別バージョン**(v1 所持中に v2 の URL): ① で同一 Paper にヒットするが、`requested_version`(または最新)≠ 既存 `source_version` なら 409 にせず 202 で受け、同一 Paper の新 `source_version` として ingest(新 DocumentRevision + carryover §4.5 + ビューアに「新しいバージョンがあります」バナー。切替は `POST /api/library-items/{id}/adopt-revision` — plans/03 §6.8 — によるユーザー操作のみ)。

### 7.2 ファジー一致(④)の確定アルゴリズム

structuring 段の最終検査で実行(書誌確定後。主に PDF アップロードと書誌推定の突合):

```python
from rapidfuzz import fuzz

def normalize_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()

def is_fuzzy_duplicate(a: PaperBibView, b: PaperBibView) -> bool:
    if not a.first_author_family or not b.first_author_family:
        return False
    return (
        fuzz.token_sort_ratio(normalize_title(a.title), normalize_title(b.title)) >= 92
        and a.first_author_family.lower() == b.first_author_family.lower()
        and (a.year is None or b.year is None or abs(a.year - b.year) <= 1)
    )
```

- 対象集合: 同一ユーザーの library_items が指す papers + public papers(タイトル前方 32 文字の PGroonga 検索で候補を 20 件以内に絞ってから上式)。
- ヒット時は**自動統合しない**。`jobs.result` に `{"duplicate_candidate": {"paper_id": "…", "title": "…", "score": 96}}` を保存し、UI は「この論文と同じですか?」カードを表示 → ユーザーが `POST /api/library-items/{id}/duplicate-resolution`(plans/03 §5.11)で統合を確定する。
- 統合確定時(private PDF → 既存 arXiv Paper): library_item の紐替え+注釈等はそのまま(library_item 配下のため無傷)、private Paper 側の資産は残置(SourceAsset は破棄しない)。品質が B→A に上がる場合は §12.3 の昇格提案フローに乗せる(自動適用しない)。

## 8. サムネイル生成

- 実行: structuring 段のサブステップ(`ingest/thumbnail.py`)。
- **選定優先順位**(docs/02 §7): ① `number == '1'` の figure ブロックの画像 → ② 画像面積(px²)最大の figure → ③ 図が 1 枚も無ければ生成せず(後続の概要図生成完了イベントで差し替え — [plans/07-ai-features.md](07-ai-features.md) §5(全体概要図)の管轄)→ ④ ③のままなら `papers.thumbnail_key = NULL` とし、フロントが破線プレースホルダ(「…」)→タイトルカードを描画する(画像は生成しない — 決定。テキストの二重管理を避ける)。
- **リサイズ仕様**(Pillow): ソース PNG を白背景 RGB 化 → 4:3 に **cover クロップ(中央基準)** → LANCZOS で 480×360px と 960×720px の 2 枚 → WebP(quality=82, method=6)で `thumbnails/{paper_id}/card.webp` / `card@2x.webp`(plans/01 §7.1)へ PUT → `papers.thumbnail_key = 'thumbnails/{paper_id}/card.webp'`。
- 配信は署名付き URL(毎回ユニーク)のためキー固定の上書きでもキャッシュ不整合は起きない。ユーザーの手動差し替え(図表一覧から)は `library_items.thumbnail_key` に個人値として保存(plans/02 §4.6)。

## 9. 拡張からの PDF 直接送信の受け口

エンドポイント定義は plans/03 §3.3(`POST /api/ingest/pdf`、multipart `file` + `meta`)。本書はサーバー側処理を確定する。

### 9.1 受信と検証

1. **サイズ上限 50MB**(決定。plans/03 §3.3 と一致): `Content-Length` 事前拒否+ストリーム読取中の累積で二重検査 → 超過は 413 `payload_too_large`。
2. 先頭 5 バイトが `%PDF-` でなければ 415 `unsupported_media_type`。
3. ストリームを一時ファイルに書きつつ SHA-256 を計算 → §7.1 ③ の重複検知(同一ユーザー 409)。
4. `papers` INSERT: `visibility='private'`, `owner_user_id=:uid`, `pdf_sha256`, `license='unknown'`(**決定**: private アップロードのライセンスは常に `unknown`。共有機能が無効(docs/09 §4)のため判定不要で、転載判定も最厳格側に倒れる)。`title` は `meta.title_guess`(拡張のローカル推定)か、無ければファイル名から末尾の `.pdf` 拡張子を除いた文字列(それも空なら `'無題の PDF'`)。
5. S3 `sources/{paper_id}/v1/original.pdf` へ PUT(private アップロードの `source_version` は `'v1'` 固定 — plans/01 §7.1)+ `source_assets` INSERT(`kind='extension_capture'`, `source_url = meta.source_url`, `byte_size`, `sha256`)。
6. `library_items` INSERT(`meta.status/tags/collection_id/quick_note` を反映。status 既定 `to_read`)+ jobs INSERT(`payload.source='pdf_upload'`)+ enqueue。

### 9.2 パイプライン差分

- fetching 段はローカル資産の存在確認のみで即完了(タイムライン 1 段目の文言は「PDF 取得(拡張から直接送信)」)。
- parsing/structuring は §6(品質 B)。translating 系は共通(§11。private 論文の翻訳セットは常に `scope='personal'`・`base_set_id=NULL` — plans/02 §1.4)。

### 9.3 書誌推定(`ingest/bib_estimate.py`)

structuring 段で実行し、`papers` を UPDATE する:

1. PyMuPDF `doc.metadata` の title / author(空・"untitled" 類は無視)。
2. 1 ページ目解析: 上部 40% 領域で最大フォントサイズの行群= title 候補。その直下〜アブストラクト見出しまでの行= authors 候補。
3. DOI 検出: 1〜2 ページ目テキストに `10\.\d{4,9}/[-._;()/:A-Za-z0-9]+`。arXiv ID 検出: §3.1 の ID 正規表現(検出時は §7 の統合候補にもなる)。
4. DOI があれば **Crossref** `GET https://api.crossref.org/works/{doi}`(User-Agent: `alinea/1.0 (mailto:contact@alinea.app)`、タイムアウト 5 秒)で補完: `title[0]`→title、`author[].given+family`→authors、`issued.date-parts`→published_on、`container-title[0]`→venue、DOI→doi。
5. **推定フラグ**: Crossref で DOI 直一致の書誌が取れたら `bib_estimated=false`、それ以外は `true`(「書誌は推定」バッジ+編集可能フォームの表示条件。カラムとエンドポイントは §13-4 の追加要求)。

## 10. 取り込みタイムライン・処理ログの記録形式

### 10.1 処理ログ(`jobs.log` — plans/02 §3.10 の JobLog)

各段の開始/完了/警告/失敗を追記する。エントリ形式(確定):

```json
{"at": "2026-07-02T21:04:12+09:00", "stage": "fetching", "level": "info",
 "message": "arXiv から LaTeX ソース取得",
 "detail": {"format": "latex", "bytes": 812345, "timeline": true}}
```

- `level ∈ {info, warn, error}`。`detail` は自由 JSON だが次のキーを予約する: `timeline`(bool。§10.2)、`format`(latex/arxiv_html/pdf)、`fallback_from`(フォールバック時の元 format)、`model`(翻訳段の使用モデル ID — docs/09 §3.5)、`stats`(構造化到達度)。
- 必須で記録するイベント: ソース取得(形式・バイト数)/ フォールバック発生(「arXiv HTML にフォールバック(LaTeX 取得失敗: 404)」)/ 構造化到達度(§6.10 の stats)/ carryover・リアンカー結果(§4.5)/ PDF 同期率(§4.6)/ 翻訳セット作成・完了(スタイル・モデル)/ 失敗(段階+理由)。
- `GET /api/papers/{paper_id}/ingest-log`(plans/03 §4.3)は「その Paper の最新の ingest ジョブ」の `log` を `{at, stage, level, message}` に射影して返す。

### 10.2 タイムライン(2a 情報パネルの 3 段)

タイムラインは**別テーブルを持たず**、`jobs.log` の `detail.timeline=true` エントリの射影とする。3 エントリの message 生成規則(確定・文言は 2a 逐語に一致):

| # | 記録タイミング | message 生成規則 |
|---|---|---|
| 1 | fetching 完了 | `"arXiv から LaTeX ソース取得"`(M2・latex)/ `"arXiv から HTML 取得"`(M0・arxiv_html)/ `"arXiv から PDF 取得"`(arXiv の PDF フォールバック)/ `"PDF 取得(拡張から直接送信)"`(pdf_upload) |
| 2 | structuring 完了 | `"構造化・図表抽出({pages}p / 図{figures} / 表{tables})"`(stats から。pages が NULL なら `"構造化・図表抽出(図{figures} / 表{tables})"`) |
| 3 | translating_body 完了 | `"全文翻訳 完了({スタイル和名} · {source_version})"` + 付録が未翻訳なら `" · 付録は未翻訳"`(スタイル和名: natural=自然訳 / literal=直訳) |

- `GET /api/library-items/{id}/viewer` の `ingest_timeline: {at, label}[]`(plans/03 §6.1)はこの 3 エントリを返す。表示側の「7/02 21:04」「21:05(同日は日付省略)」整形はフロントの責務。
- reingest 時は新ジョブの log に新しい 3 段が積まれ、タイムラインは常に最新 ingest ジョブから取る。

## 11. 翻訳段の実装(translating_abstract / readable / translating_body)

翻訳プロンプト・プレースホルダ検証の詳細は [plans/06-translation-pipeline.md](06-translation-pipeline.md) と plans/04(タスク `translation`)の管轄。本書はパイプラインからの結線のみ確定する。

### 11.1 translating_abstract 段

1. `alinea_llm.LLMRouter.run(task='translation', …)` でアブストラクトを翻訳 → `papers.abstract_ja`。
2. `LLMRouter.run(task='summary', …)`(structured output)で ✦3行要約+提案タグを **1 呼び出し**で生成する(**確定**。要約とタグ提案を別呼び出しに分けない)。JSON Schema 名は `summary_3line_v1`(plans/07 §3.1 も同スキーマ: `{summary_lines: string[3], suggested_tags: string[]}`):

```json
{"summary_lines": ["課題の1行", "手法の1行", "結果の1行"], "suggested_tags": ["distillation", "solver"]}
// summary_lines の行頭に①②③等の記号は付けない(表示側が付与。plans/07 §3.1 のプロンプト規定と一致)
```

   → `papers.summary_lines`(共有資産)/ `library_items.suggested_tags` = arXiv カテゴリ(`cs.CV` 等をそのまま)∪ ユーザーライブラリの共起タグ上位 2 件(**定義**: 当該論文と arXiv カテゴリを 1 つ以上共有する同一ユーザーの既存 library_items に付与された確定タグの出現頻度上位 2 件。同数はタグ名昇順)∪ 上記 LLM 提案(最大計 5 件・承認式 — docs/02 §7)。
3. `usage_records` は plans/04 の MeterHook が `job_id` 付きで記録する。

### 11.2 翻訳セットとセクションジョブの張り出し

1. readable 段の冒頭で翻訳セットを確保: public 論文 → `translation_sets (revision_id, style='natural', scope='shared')` を `ON CONFLICT DO NOTHING` + SELECT(`uq_translation_sets_shared`)。private 論文 → `scope='personal', user_id, base_set_id=NULL`。`glossary_snapshot` は shared ならグローバル既定用語のみ(plans/02 §1.4)。
2. 自動翻訳対象ブロック(分母 `stats.translatable_blocks`): type ∈ {paragraph, heading, list, quote, theorem, footnote} + figure/table のキャプション。除外: equation・code・algorithm・reference_entry(plans/06 §2.1「equation / code / algorithm / reference_entry は常に対象外」が正 — algorithm=擬似コード本体は翻訳しない)・`is_appendix` セクション(設定 4f が既定 ON の間)・表セル(同)。docs/03 §6.1 の進捗分母定義と一致。
3. 第 1 本文セクションを ingest ジョブ内で直接翻訳(§2.1)→ stage=readable。
4. 残セクションぶんの `jobs (kind='translation', payload={"set_id", "section_id", "block_ids": […], "reason": "initial"})` をセクション文書順に INSERT + `alinea:bulk` enqueue(§2.3 のアドバイザリロックで冪等)→ stage=translating_body。
5. **30 ページ超の扱い**(docs/03 §2 設定「30 ページ超の論文はセクション選択を提案」ON のとき): 既定は全選択のまま全ジョブを張り出し、ビューア側の提案 UI でユーザーが選択を確定したら選外セクションの queued ジョブを `status='canceled'` にする(確定エンドポイントは §13-6 の追加要求)。提案であって停止はしない(P6)。

### 11.3 translate_section ジョブと完了検知

- `translate_section(ctx, job_id)`: claim → 対象ブロックを順に `source_hash` 照合 UPSERT(既訳スキップ)→ ブロックごとにプレースホルダ検証(不合格は 2 回再試行 → 原文フォールバック+`quality_flags`)→ 進捗 PUBLISH は**決定**: 10 ブロック訳出ごとに全体進捗(§2.2)を再計算し、直近 PUBLISH 値から 5 ポイント以上増えた場合のみ PUBLISH。加えてセクション(= 本ジョブ)完了時に無条件で 1 回 PUBLISH する。
- **完了検知(決定)**: 各 translate_section の成功トランザクション内で `pg_advisory_xact_lock(hashtext(set_id::text))` を取り、`SELECT count(*) FROM jobs WHERE kind='translation' AND payload->>'set_id'=:set_id AND payload->>'reason'='initial' AND status IN ('queued','running','waiting_quota')` が 0 なら: `translation_sets.status='complete'`、親 ingest ジョブを `stage='complete', status='succeeded', progress=100, finished_at=now()` に更新、タイムライン 3 段目を記録、§12.1 の通知を発火。1 件以上残っていれば `translation_sets.status='partial'` のまま。

## 12. 通知発火

### 12.1 翻訳完了(`translation_complete`)

- タイミング: ingest ジョブ complete 時(§11.3。既訳流用で数秒完了した場合も同様に発火する — 決定。拡張ポップアップを閉じた後でも完了に気づける)。
- 条件: 起動ユーザーの `users.settings` の `notifications.translation_complete` が `false` でないこと(4f「通知」カテゴリの個別 OFF)。
- 実装:

```sql
INSERT INTO notifications (user_id, kind, payload)
SELECT :user_id, 'translation_complete',
       jsonb_build_object('library_item_id', :library_item_id,
                          'paper_title', :paper_title,
                          'job_id', :job_id)
WHERE NOT EXISTS (
  SELECT 1 FROM notifications
  WHERE user_id = :user_id AND kind = 'translation_complete'
    AND payload->>'job_id' = :job_id);   -- §2.3 の 1 回限り保証
```

- 直後に Redis `PUBLISH events:user:{user_id}` へ `notification.created {notification_id, kind, payload}`(plans/01 §5)→ ベル未読ドット(#C49432)+「読み始める →」。拡張はポーリング(15,000ms / ポップアップ表示中 2,000ms)で `GET /api/ingest/recent` と `GET /api/auth/me` の `unread_notifications` を拾い、ツールバーの琥珀ドット・完了チェックを更新する。

### 12.2 失敗の可視化

- ジョブ failed 確定時は通知を発火**しない**(通知 3 種に「失敗」は無い — docs/06。デザイン 4a 準拠)。失敗は SSE `job.failed` + 拡張「直近の取り込み」の失敗表示+ダッシュボードカードの「段階名+理由+再試行」で提示する(P3)。

### 12.3 B→A 昇格提案(`status_suggestion` / `promote_revision`)

- 検出: arq cron `check_quality_promotions`(worker-bulk、毎日 07:30 JST)。対象= 最新リビジョンが `quality_level='B'` かつ `arxiv_id IS NOT NULL` の Paper。Redis キー `promo:checked:{paper_id}`(TTL 604,800 秒= 7 日)で再確認間隔を 7 日に間引き、昇格可否を判定する。**判定規則(確定)**: §3.4 の LaTeX 有無判定が真、**または** `HEAD https://arxiv.org/html/{arxiv_id}{latest_version}` が 200(公式 HTML あり)のいずれかで「A 化可能」とする(M0〜M2 共通。M2 以降も HTML 経路は品質 A のため規則は変わらない)。
- 取得可能になっていたら、その Paper を持つ全ユーザーに `notifications (kind='status_suggestion', payload={"library_item_id", "paper_title", "action": "promote_revision", "revision_id": <現行 B リビジョン>})` を挿入(plans/02 §3.7 の形式。同一 paper × user × action の未読が既にあれば挿入しない)。
- 適用はユーザーのワンクリック(通知 →「変更する」→ `POST /api/papers/{paper_id}/reingest`)。自動適用しない(P6)。reingest は `mode='reingest'` の ingest ジョブとして走り、新リビジョン(品質 A)+ carryover + リアンカー(§4.5)を実施する。

## 13. ⚠ 基盤への追加要求(plans/01〜03 への差分)

1. **`ck_jobs_status` に `'waiting_quota'` を追加**(plans/02 §4.13)。plans/03 §17.4・§21 が `Job.status='waiting_quota'` を要求しているが DB CHECK に無い。`('queued','running','waiting_quota','succeeded','failed','canceled')` へ変更。
2. **`uq_papers_pdf_sha256` のスコープ変更**(plans/02 §4.3)。グローバル部分一意のままだと「private PDF は他ユーザーと共有しない」(plans/03 §3.3)と矛盾(2 人目の同一 PDF アップロードが一意制約違反)。`CREATE UNIQUE INDEX uq_papers_owner_pdf_sha256 ON papers (owner_user_id, pdf_sha256) WHERE pdf_sha256 IS NOT NULL` に置き換え。
3. **`ck_papers_license` に `'cc-by-nc-nd-4.0'` を追加**(plans/02 §4.3)。arXiv の選択肢に CC BY-NC-ND 4.0 が実在する(§3.3)。追加までは `unknown` へ縮退マップ(安全側)。docs/09 §5.2 マトリクスには「BY-NC-ND = 転載 ×(ND+NC)」の行追加が必要。
4. **private 論文の書誌編集**: `papers.bib_estimated BOOLEAN NOT NULL DEFAULT false` カラム(plans/02 §4.3)と `PATCH /api/papers/{paper_id}`(所有者のみ・private のみ。title/authors/venue/published_on/doi を編集可)の追加(plans/03 §4)。docs/02 §3「推定メタデータは編集可能なフォームで確認できる」の受け皿が現状無い。
5. **`block_search_index.page / bbox` のコメント修正**(plans/02 §4.3)。「品質 B のみ」→「品質 B はパーサ出力、品質 A は PDF 位置同期(§4.6)で格納(同期失敗ブロックは NULL)」。DDL 変更は不要(コメントのみ)。
6. **セクション選択確定エンドポイント**: `POST /api/translation-sets/{set_id}/section-selection { section_ids: string[] }`(選外の queued `translation` ジョブを canceled 化)の追加(plans/03 §7)。docs/03 §2 の 4f 設定「30 ページ超はセクション選択を提案」の確定操作が現状の 133 本に無い。
7. **`jobs` のアクティブ ingest 部分一意インデックス**(plans/02 §4.13、推奨): `CREATE UNIQUE INDEX uq_jobs_ingest_active ON jobs (paper_id) WHERE kind='ingest' AND status IN ('queued','running','waiting_quota')`。reingest の 409 `conflict`(plans/03 §4.2)を DB レベルでも保証する。

## 14. 受け入れ基準(本書ぶんの実装検証)

- [ ] §3.1 の 7 URL パターン+旧形式 3 例がすべて `arxiv_id + version` に正規化される(プロパティテスト含む)
- [ ] `GET /api/ingest/check` が e-print HEAD(24h キャッシュ)で `latex_available` を返し、拡張に「✓ LaTeX ソースあり — 品質レベル A 見込み」が出る
- [ ] arXiv レート制限(全ワーカー横断 1 req/3.1s・`ARXIV_USER_AGENT`)が守られる
- [ ] Rectified Flow(2209.03003)の arXiv HTML / ar5iv HTML から、§4.2 の全ブロック種(数式 LaTeX・図・表・脚注・参考文献・相互参照)が抽出され、品質 A のリビジョンが作られる
- [ ] ブロック ID が `blk-3-p2-a1f9` / `blk-3-eq5-77c2` 形式で決定的に生成され、同一入力から常に同一 ID になる
- [ ] リビジョン更新(reingest)で内容不変ブロックの ID が 100% 引き継がれ、注釈が無傷で移行する。失敗分は「未配置」に退避する
- [ ] 品質 A の論文で PDF 位置同期が動作し、「同期: p.n ≒ §x.y」と bbox チップ(2a)が成立する
- [ ] テキストレイヤ無し/可視本文不足の PDF は frontend の通常操作だけで OCR 候補へ進み、成功時は品質 B の記事・図表・日本語 PDF 生成へ進める。Tesseract/言語データ不足または OCR 後も不完全な場合は、安定 code・段階・理由・再試行可否が UI に出る
- [ ] 2 段組 PDF の読み順・見出しツリー・図キャプション対応付けが §6 の閾値どおり動作し、到達度が stats と処理ログに記録される
- [ ] 51MB の PDF 送信が 413、非 PDF が 415、同一ユーザー同一 SHA-256 が 409 になる。private 論文が共有ページに一切露出しない
- [ ] ジョブを translating_body の途中で SIGKILL → 再実行しても、訳済みブロックが再課金されず二重行も生じない(§2.3)
- [ ] `POST /api/translation-sets/{set_id}/prioritize` で対象セクションのジョブが interactive に繰り上がり、後着 claim が no-op になる
- [ ] クォータ超過時に翻訳段だけ `waiting_quota` で停止し、BYOK 登録で自動再開する
- [ ] タイムライン 3 段(§10.2 の逐語)と処理ログ(フォールバック・使用モデル込み)が情報パネルに表示される
- [ ] 全文翻訳完了時に `translation_complete` 通知が 1 回だけ発火し、SSE でベルドットが即時更新される。ジョブ失敗では通知が発火しない
- [ ] 保存後 3 秒でカード、20 秒で ✦3行要約、60 秒で readable(p50。Prometheus `job_duration_seconds` で計測可能)
