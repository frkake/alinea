# 設計: セマンティック検索(S12 / M3)

- 日付: 2026-07-16
- 対象: `apps/api`(横断検索) / `apps/worker`(埋め込みインデクシング) / `packages/llm`(埋め込みプロバイダ抽象) / `packages/py-core`(検索融合) / `docker/db`・Alembic(pgvector)
- ステータス: レビュー待ち(インフラ判断は未確定 — §3 でユーザーに確認を仰ぐ)
- ロードマップ根拠: docs/10 §5「セマンティック検索: 類似手持ち論文・クエリ翻訳のクロス検索強化」、docs/06 §9「セマンティック検索・『似た手持ち論文』は M3。まず確実な全文検索を出す」

> 本ドキュメントは **設計と意思決定の提示** が主目的である。実装は「安全に着手できる第一スライス」(§7)のみを TDD で行い、埋め込みプロバイダ・pgvector・インデクシングジョブなど **新しい依存・DB 拡張を要する部分は着手しない**(すべて §3 で列挙し、ユーザー判断を仰ぐ)。

---

## 1. Context(背景・現状)

現行の横断検索は **PGroonga 全文検索のみ** で成立している。

- ルータ: `apps/api/src/alinea_api/routers/search.py`。1 本の巨大 CTE(`_HITS_SQL`)が
  本文(原文=`block_search_index.source_text` / 訳文=`translation_units.text_ja`)・
  メモ・注釈・チャット・記事・書誌の 6 源を `&@~`(PGroonga 全文検索演算子)で引き、
  `pgroonga_score()` を各行のスコアとする(`search.py:129-310`)。
- 取得後は Python 側で `_HitRow` に正規化 → `library_item` 単位でグループ化 →
  関連度(`group_score = max(hit.score)`)または新しさでソート → カーソルページング
  (`_build_groups` / `_sort_groups` / `_apply_group_cursor`)。
- スニペットは `pgroonga_snippet_html`、整形は `alinea_core.search.pgroonga_query`。
- インデックスの実体は `block_search_index`(派生テーブル。`alinea_core.search.rebuild.rebuild_block_search_index` が revision 単位で DELETE→INSERT)。

**埋め込み・pgvector・ベクトルストアは存在しない**(リポジトリ全体を grep 済み。
`docker/db/init.sql` と `0001_initial_schema.py` の拡張は `pgroonga` / `pgcrypto` / `citext` のみ)。
DB イメージは `groonga/pgroonga:4.0.1-debian-16`(pgvector 非同梱)。

LLM 抽象化層(`packages/llm`)は **テキスト生成(`LLMProvider`)と画像生成(`ImageProvider`)** の
2 系統のみで、**埋め込み(embeddings)インターフェースは無い**。`models.yaml` / `routing.yaml` にも
埋め込みモデル・埋め込みタスクは存在しない。

日英クロス検索は現状 **全文一致のみ**(docs/06 §9「日本語クエリは訳文に、英語クエリは原文にヒット」)。
「クエリ翻訳による相互検索」「意味的な類似」は明示的に M3(=本機能)へ後送りされている。

## 2. Goals / Non-Goals

### Goals

1. **類似手持ち論文の発見**("似た論文"): ある論文(またはブロック)に意味的に近い、自分の
   ライブラリ内の他論文を提示する。
2. **クロス言語セマンティッククエリ**: 日本語クエリで英語原文の *意味* にヒットする(語形一致に
   依存しない)。多言語埋め込みモデルで query/passage を同一ベクトル空間へ写像することで実現する。
3. **既存の語彙検索(PGroonga)との融合**: セマンティック結果を既存の全文結果と **ブレンド** し、
   語彙一致の精度を失わずに意味的想起を足す。セマンティックを既定で全面置換はしない
   (docs/06 §9「まず確実な全文検索を出す」の原則を維持)。

### Non-Goals(本機能の範囲外)

- 全文検索(PGroonga)の置き換え。セマンティックは **加算的**。
- チャット RAG の根拠検索の刷新(チャットの evidence 選択は別系統。将来的に共用しうるが本設計外)。
- 他社ベクトル DB(Pinecone/Qdrant 等)の導入。**Postgres 内(pgvector)で完結** させる方針。
- リアルタイム再ランク用のクロスエンコーダ等の重い後段。第一段は ANN + RRF 融合に留める。

## 3. インフラ意思決定(★ユーザー確認が必要)

以下は **新しい依存 / DB 拡張 / コスト** を伴うため、勝手に導入しない。各項目に推奨案と代替を示す。

### D1. 埋め込みプロバイダと LLM 抽象への追加(★要判断)

現行の `models.yaml` の 5 プロバイダのうち埋め込み API を持つのは:

- **OpenAI**: `text-embedding-3-small`(1536 次元, $0.02/Mtok) / `-3-large`(3072 次元, $0.13/Mtok)。
  `dimensions` パラメータで次元短縮可。多言語性能は実用域。**推奨(既定)**。
- **Google (Gemini)**: `gemini-embedding-001`(可変次元 128–3072, 既定 3072)。多言語対応。
- **DeepSeek / xAI**: 埋め込みエンドポイントは提供が薄い/無い(要確認)。除外候補。
- **Anthropic**: **埋め込み API を提供しない**(Voyage AI を案内)。除外。

**抽象化の設計判断**: 既存 `LLMProvider` プロトコル(`generate`/`generate_stream`/
`generate_structured`/`count_tokens`)に `embed` を足すと **5 プロバイダ全実装** に波及し、
埋め込み非対応プロバイダ(Anthropic 等)が壊れる。したがって **`ImageProvider` と同様に独立した
`EmbeddingProvider` Protocol を新設** する(embed だけを持つ)。ルーティングは既存
`llm_task_routes` に新タスク `embedding` を足す形が自然だが、埋め込みは
「フォールバック時にベクトル空間が変わると既存インデックスと非互換」という固有制約があるため、
**フォールバックは同一次元・同一モデル系列に限定** し、モデル切替時は再インデックスを必須とする
(§6.4)。

- 判断が要る点: (a) 既定プロバイダ/モデル(推奨: OpenAI `text-embedding-3-small`, 1536d)、
  (b) BYOK を埋め込みにも適用するか(推奨: する。既存 `DbKeyStore` を再利用)、
  (c) クォータカウンタを足すか(推奨: 当面はインデクシングを運営バッチ扱いにしコストを実測)。
- **新依存の有無**: OpenAI/Google の埋め込みは **既存 SDK(`openai==1.93.0` / `google-genai`)で
  呼べる** ため Python 依存の追加は不要。ただしプロバイダ実装コードの追加が必要(第一スライス外)。

### D2. pgvector 拡張と Docker/Alembic(★要判断)

- 現行 DB イメージ `groonga/pgroonga:4.0.1-debian-16` は **pgvector を同梱しない**(確認済み)。
  → 選択肢:
  - **(推奨) イメージ差し替え**: pgvector と PGroonga を **両方** 積んだイメージへ。既製の
    単一イメージは無いので、`docker/db/Dockerfile` を新設し `groonga/pgroonga:4.0.1-debian-16`
    を base に `postgresql-16-pgvector`(Debian の PGDG apt)を足すのが最小変更。PGroonga の
    `stem.so`/`mecab.so`(docker-compose のコメント参照)を壊さないため **debian-16 ベースを維持** する。
  - (代替) pgvector を使わず、埋め込みを `REAL[]` 列 + 自前コサインで持つ。ANN 無しの全走査に
    なり、規模(数百ユーザー・1 人数百〜数千論文)なら **当面は許容** だが将来性が無い。
    py-core の融合ロジック(§7)は **どちらの経路でも再利用可能** に設計する。
- **Alembic**: `CREATE EXTENSION IF NOT EXISTS vector;` を持つマイグレーション(手書き SQL、
  `0002` のシード方式に倣う)+ 埋め込み格納テーブル(§5)。`env.py` は既に手書き SQL 前提。
- **本番運用**: 拡張追加は初期化 SQL(`docker/db/init.sql`)にも `CREATE EXTENSION vector` を
  加える必要がある(既存ボリューム `db-data` は初回のみ init.sql が走るため、既存環境は
  Alembic 側の `CREATE EXTENSION` に依存する)。

### D3. 埋め込み粒度とコスト(★要判断)

| 粒度 | 用途 | 件数の目安 | コスト |
|---|---|---|---|
| 論文レベル(title+abstract 1 ベクトル) | 「似た論文」 | 1/論文 | 極小 |
| ブロックレベル(`block_search_index` 行) | クロス言語クエリの精密ヒット | 数百/論文 | 中(要バッチ) |

- **推奨: 段階導入**。第一段(有効化時)は **論文レベル** のみ(abstract 埋め込み)で
  「似た論文」+粗いクエリ検索を出す。ブロックレベルは第二段(コスト実測後)。
- クロス言語クエリの精度はブロック粒度が要るが、コスト(数百ブロック×論文数)と再インデックス
  負荷が大きい。docs/09 のコスト方針(共有キャッシュ・オンデマンド)に合わせ、**共有(shared)
  スコープの revision に対して 1 回だけ埋め込む**(訳文ではなく原文=言語非依存の source を埋める。
  多言語モデルなら日本語クエリが英語 source にヒットする)。

### D4. 融合戦略(lexical × semantic)(★方式は推奨あり)

- スコアの単位が異なる(PGroonga スコア vs コサイン類似度)ため **単純加算は不可**。
- **推奨: Reciprocal Rank Fusion (RRF)**。各リストの *順位* のみを使う `Σ 1/(k+rank)`(k=60 が定番)。
  スケール非依存・実装が単純・決定的で、ハイブリッド検索の標準手法。重み付き RRF で
  lexical/semantic の寄与を調整可能にする。
- この融合は **純関数**(DB も LLM も要らない)として `packages/py-core` に置き、**第一スライスで
  今すぐ実装・テスト** する(§7)。将来 pgvector が入っても、DB から返る 2 つのランク済みリストを
  この関数で合流させるだけで済む。

## 4. アーキテクチャ(全体像)

```
                    ┌──────────────────────────────────────────┐
   query ──────────▶│ /api/search (search.py)                   │
                    │  1. PGroonga CTE  → lexical ranked list     │
                    │  2. embed(query)  → ANN(pgvector) → semantic│
                    │  3. reciprocal_rank_fusion(lex, sem)  ★純関数│
                    │  4. group / sort / paginate(既存)          │
                    └──────────────────────────────────────────┘
                                        ▲
       ┌────────────────────────────────┘
       │ 埋め込み格納(pgvector)  paper_embeddings / block_embeddings
       │
   ┌───┴───────────────┐        ┌─────────────────────────────┐
   │ worker: embed job  │◀──────│ EmbeddingProvider(新Protocol)│
   │ (rebuild 後にフック)│        │  OpenAI / Google 実装(将来)  │
   └────────────────────┘        │  FakeEmbeddingProvider(テスト)│
                                 └─────────────────────────────┘
```

構成要素:

1. **埋め込み抽象**(`packages/llm`): `EmbeddingProvider` Protocol + `EmbeddingRequest`/
   `EmbeddingResult` 型 + 決定的 `FakeEmbeddingProvider`。★第一スライスで実装。
2. **埋め込みストア**(`pgvector`): `paper_embeddings`(論文粒度)/ `block_embeddings`
   (ブロック粒度)。HNSW インデックス。★D2 判断後。
3. **インデクシングジョブ**(`apps/worker`): `rebuild_block_search_index` と同じフック点で
   埋め込みを生成・upsert。運営キーで実行(worker は per-user キーを持たない — bootstrap 参照)。
   ★D1/D2 判断後。
4. **クエリ経路**(`apps/api/routers/search.py`): クエリを埋め込み → ANN → RRF 融合 →
   既存グルーピング。フラグ off 時は現行と完全に同一挙動。★D1/D2 判断後。
5. **融合**(`packages/py-core/search/fusion.py`): RRF・コサイン・類似度ランキングの純関数。
   ★第一スライスで実装。

## 5. データモデル(pgvector 導入時。★D2 判断後)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- 論文粒度(D3 第一段)。abstract(原文=言語非依存)を埋め込む。
CREATE TABLE paper_embeddings (
    paper_id     UUID PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    model        TEXT NOT NULL,           -- 例 "text-embedding-3-small"
    dim          INT  NOT NULL,           -- 例 1536(モデル切替検知用)
    embedding    vector(1536) NOT NULL,
    source_hash  TEXT NOT NULL,           -- 埋め込んだ原文の xxhash(再計算スキップ判定)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_paper_embeddings_hnsw ON paper_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- ブロック粒度(D3 第二段)。block_search_index と 1:1(revision 単位)。
CREATE TABLE block_embeddings (
    revision_id  UUID NOT NULL REFERENCES document_revisions(id) ON DELETE CASCADE,
    block_id     TEXT NOT NULL,
    model        TEXT NOT NULL,
    dim          INT  NOT NULL,
    embedding    vector(1536) NOT NULL,
    source_hash  TEXT NOT NULL,
    PRIMARY KEY (revision_id, block_id)
);
CREATE INDEX idx_block_embeddings_hnsw ON block_embeddings
    USING hnsw (embedding vector_cosine_ops);
```

- **モデル/次元の記録**: `model`/`dim` 列で「どのモデルで埋めたか」を保持。クエリ時に既定モデルと
  不一致な行は無視(混在防止)。モデル切替は再インデックス(バックフィルジョブ)で更新。
- **ANN クエリ例**: `SELECT paper_id, 1 - (embedding <=> :qvec) AS score FROM paper_embeddings
  WHERE model = :model ORDER BY embedding <=> :qvec LIMIT :k`(`<=>` = コサイン距離)。
- **アクセス制御**: 論文は `library_items` 経由でユーザーに紐づく。ANN 候補取得後、既存 CTE と
  同様に `library_items.user_id` で絞る(共有 revision の埋め込みは全ユーザー共用で問題ない)。

## 6. 挙動仕様

### 6.1 フィーチャーフラグ

- `CoreSettings.semantic_search_enabled: bool = False`(既定 off)。★第一スライスで追加。
- off の間、`/api/search` は **完全に現行挙動**(セマンティック経路に一切入らない)。
- on にしても埋め込みストアが空/未整備なら、セマンティックのランクリストは空 → RRF は
  lexical のみを返す(=現行と同等)。**壊れない縮退** を保証。

### 6.2 クエリ経路(有効化時)

1. 既存 CTE で lexical ヒット(`_HitRow`)を取得。
2. クエリを `EmbeddingProvider.embed` で 1 ベクトル化(BYOK/運営キーは既存 `build_router_for_user`
   の解決規則を流用)。埋め込み失敗(プロバイダ落ち)は **握りつぶして lexical のみで返す**
   (P3: 検索は落とさない)。
3. pgvector ANN で意味的近傍を `library_item` 粒度に丸めて取得。
4. `reciprocal_rank_fusion([lexical_by_item, semantic_by_item], weights=[w_lex, w_sem])` で融合。
5. 融合順を `group_score` の代替として使い、既存の `_build_groups`/ページングに載せる。

### 6.3 「似た論文」

- 論文詳細(または情報パネル)から `GET /api/library-items/{id}/similar`(将来エンドポイント)。
- 対象論文の `paper_embeddings.embedding` を種に ANN。自分のライブラリ内の他論文へ限定。
- lexical 融合は不要(純セマンティック)。

### 6.4 モデル切替と再インデックス

- 埋め込みモデルはベクトル空間を規定する。**モデル変更 = 全再インデックス** が必要。
- `paper_embeddings.model/dim` と既定モデルの不一致を検知し、バックフィルジョブ
  (`latex_pdf_backfill.py` 系の運用に倣う)で再生成。切替中は旧モデル行で検索継続。

## 7. 安全な第一スライス(★本 PR で実装する範囲)

新依存・DB 拡張・埋め込み API 呼び出しを **一切伴わない**、決定的にテスト可能な土台のみ:

1. **`packages/llm` — 埋め込み抽象と Fake**
   - `EmbeddingRequest` / `EmbeddingResult` 型(`types.py`)。
   - `EmbeddingProvider` Protocol(`protocols.py`。`ImageProvider` と同格の独立プロトコル)。
   - `FakeEmbeddingProvider`(`testing/fake_provider.py`)— 決定的ハッシュ袋詰め埋め込み。
     ネットワーク不要・`hashlib`(プロセス間で安定)ベース。同一入力→同一ベクトル、共有トークン→
     高コサイン。**多言語意味は模さない**(実プロバイダの責務。テストは決定性のみ検証)。
   - `__init__` から再エクスポート。
2. **`packages/py-core` — 融合の純関数**(`alinea_core/search/fusion.py`)
   - `reciprocal_rank_fusion(ranked_lists, *, k=60, weights=None)` — スケール非依存・決定的
     (同点は key 昇順)。
   - `blend_lexical_semantic(lexical, semantic, ...)` — ドメイン向けの薄いラッパ。
   - `cosine_similarity(a, b)` / `rank_by_similarity(query, candidates, *, top_k)` — pgvector が
     入るまでの Python 側 ANN 代替(および実装検証用)。
   - `search/__init__.py` から再エクスポート。
3. **`CoreSettings.semantic_search_enabled: bool = False`** — フラグの受け皿(既定 off)。

**あえてやらないこと**(すべて §3 の判断待ち): 実 `EmbeddingProvider` 実装(OpenAI/Google)、
`models.yaml`/`routing.yaml` への埋め込みモデル/タスク追加、pgvector 拡張・Docker イメージ変更・
埋め込みテーブルの Alembic、worker のインデクシングジョブ、`search.py` の融合配線。

第一スライスは **後続の全経路が組み立てられる型と純ロジックの土台** であり、それ自体では
検索挙動を一切変えない(フラグ off、配線なし)。

## 8. テスト戦略

- `packages/llm/tests/test_embeddings.py`: Fake の決定性(同一入力→同一ベクトル)、L2 正規化、
  バッチ長一致、空入力の縮退、共有トークンのコサイン > 無関係のコサイン、`EmbeddingProvider`
  への `isinstance`(runtime_checkable)適合。
- `packages/py-core/tests/test_fusion.py`: RRF の順位計算(既知入力の手計算一致)、スケール非依存
  (スコアを定数倍しても順位不変)、重み付け、同点の決定的タイブレーク、`cosine_similarity` の
  既知値、`rank_by_similarity` の順序と `top_k`、空リストの縮退、両リスト空 → 空。
- すべて **決定的・DB 非依存・ネットワーク非依存**。`uv run pytest apps/api packages -q` で回帰確認。

## 9. ロールアウト計画(参考。判断後)

1. D1/D2 判断 → pgvector イメージ + Alembic(拡張 + `paper_embeddings`)。
2. `OpenAIEmbeddingProvider` 実装 + `embedding` タスクのルート/モデル登録。
3. worker: 論文粒度インデクシング(rebuild フック + 全件バックフィル)。
4. `search.py`: フラグ on 時に query 埋め込み → ANN → RRF 融合を配線。
5. 「似た論文」エンドポイント。
6. コスト実測 → ブロック粒度(D3 第二段)。

## 10. Open Questions(ユーザーへ)

- Q1(D1): 既定埋め込みプロバイダは OpenAI `text-embedding-3-small`(1536d)で良いか。
  BYOK を埋め込みにも効かせるか。
- Q2(D2): pgvector 同梱の自前 Docker イメージ(`docker/db/Dockerfile`)を新設して良いか。
  それとも当面 `REAL[]` + 全走査で規模を凌ぐか。
- Q3(D3): 初手は論文粒度(abstract)のみで良いか。ブロック粒度はコスト実測後で良いか。
- Q4(D4): 融合は RRF(重み付き)で確定して良いか。既定重みは lexical=semantic=1.0 で良いか。
- Q5: 埋め込みコストをクォータ管理(§ D1c)に含めるか、当面運営バッチ扱いにするか。
