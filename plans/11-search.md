# 11. 全文検索・横断検索 実装計画(PGroonga 完全設計)

> **対象読者と前提**: 本書は「Alinea — 論文読解ワークベンチ」の全文検索(横断検索 4e / 検索ドロップダウン 1e / 論文内検索 / 語彙帳内検索 / ライブラリのフィルタ検索)の実装計画である。対象読者は apps/api(FastAPI)・apps/web(Next.js)・apps/worker の実装者。機能仕様の正は [docs/06-library.md](../docs/06-library.md) §9(横断検索)・[docs/09-nonfunctional.md](../docs/09-nonfunctional.md) §1/§7.2(性能・検索制約)。テーブル・カラムは [plans/02-data-model.md](02-data-model.md)、エンドポイント形式は [plans/03-api.md](03-api.md) §15、UI トークン・コンポーネントは [plans/08-design-system.md](08-design-system.md) に一致させる。**PGroonga のトークナイザ・クエリ・ハイライト設計は本書が正**であり、plans/02 §4.14 の物理インデックス節は本書 §2.2 の内容に更新する(plans/02 §4.14 が明示的に委譲)。

## 1. 全体方針(確定事項)

- **検索対象(横断検索)**: docs/06 §9 のとおり **本文(原文・訳文)/ メモ / 注釈(コメント)/ チャット履歴 / 記事 + 書誌(タイトル・アブスト・アブスト訳)** の 6 系統。語彙帳は横断検索の対象ではなく、語彙帳画面(4d)内の検索(`GET /api/vocab?q=`)専用のインデックスを別途持つ(§2.2 (9))。
- **アーキテクチャ決定: 検索専用の非正規化テーブルは作らない。各実体テーブルに PGroonga インデックスを直接張る。** 理由: (a) plans/02 §4.14 が既にこの構成で確定している。(b) PGroonga インデックスは行の INSERT/UPDATE に同期追随するため、別テーブルへの二重書き込み(とその整合性バグ)が不要になる。(c) 検索スコープのユーザー絞り込みは `library_items` への JOIN で表現でき、個人開発規模(単一 VPS・数十〜数百ユーザー)では十分速い(§10)。
  - ⚠ plans/01-architecture §3.4 の `search_entries` 非正規化テーブル案はこの決定で**不採用**(§11 の修正要求 R-4)。
- **言語判定はしない(確定)**: クエリの日本語/英語を判定せず、**常に原文側・訳文側(および全ヒット源)の両面を検索し、ヒットした源で区別する**。理由: 「EMA teacher」のような英語クエリは訳文「EMA 教師(EMA teacher)」にもヒットすべきで(4e 逐語)、CJK 有無による判定は英語混じり日本語クエリ(「EMA 教師」)で誤爆する。判定を捨てれば分岐もバグ余地も消える。4e フッタ文言「日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)」は、判定の説明ではなく**結果としてそうなる**ことのユーザー向け説明として維持する。
- **同一ブロック同一視**: 同一 `(revision_id, block_id)` の原文ヒットと訳文ヒットは 1 件に統合し `matched_in: ["source","translation"]` で源を併記する(plans/03 §15.1・4e「原文ヒットと同一視」)。§3.4。
- **エンドポイント**: `GET /api/search`(全結果 4e)/ `GET /api/search/preview`(1e ドロップダウン)/ `GET /api/revisions/{revision_id}/search`(論文内検索)/ `GET /api/vocab?q=`(語彙帳内)/ `GET /api/library-items?...`(構造化フィルタ)。パス・スキーマは plans/03 に一致(本書で確定する差分は §11 に集約)。
- **性能目標**: 横断検索・ライブラリ全文検索 **p50 1 秒 / p95 3 秒**(docs/09 §1)、プレビュー **p50 300ms**(plans/03 §15.2)。設計は §10。

## 2. PGroonga 物理設計

### 2.1 トークナイザ・ノーマライザの決定

**決定: 「英語テキスト列= `TokenBigram` + `TokenFilterStem`」「日本語テキスト列= `TokenMecab`」の 2 系統に使い分ける。ノーマライザは全インデックス共通で `NormalizerNFKC150`。**

| 系統 | 対象列 | tokenizer | token_filters | 根拠 |
|---|---|---|---|---|
| 英語(原文) | `block_search_index.source_text` / `papers.title` / `papers.abstract` | `TokenBigram` | `TokenFilterStem` | TokenBigram は連続アルファベットを 1 単語トークンとして切るため英語には実質「単語分割」として働く。`TokenFilterStem` で英語ステミング(`stabilizes`→`stabilize`)を実現し、docs/09 §7.2「英語ステミング対応」を満たす |
| 日本語(訳文・個人資産) | `translation_units.text_ja` / `notes.title,body_md` / `annotations.body` / `chat_messages.text_plain` / `article_blocks.text_plain` / `papers.abstract_ja` / `vocab_entries.term,meaning_short,meaning_long` | `TokenMecab` | なし | docs/09 §7.2「日本語形態素解析対応」。MeCab は連続アルファベットも 1 語として切るため、日本語テキスト中の英語語(「EMA 教師(**EMA teacher**)」)に英語クエリがそのままヒットし、日英クロス(§3.4)が成立する |

- 理由(TokenMecab を日本語側に採用): 形態素解析は docs/09 §7.2 の明示制約。TokenBigram(2-gram)比で誤ヒット(「京都」⊂「東京都」型)が減り、スニペットの語境界も自然になる。未知語(「整流フロー」等)は MeCab が名詞連結として分割するため取りこぼしは実用上問題にならない。
- 理由(NormalizerNFKC150): 全角/半角・大文字/小文字・合成文字を NFKC 150 規則で正規化し、「ＥＭＡ」「ema」でも「EMA」にヒットさせる。plans/02 §4.14 の暫定値 `NormalizerAuto` から更新する(§11 R-1)。
- v2: 訳文側への同義語展開(`TokenFilterSynonym`)とクエリ翻訳による相互検索(docs/06 §9.2「M3」)。v1 では実装しない。

### 2.2 完全 DDL(plans/02 §4.14 をこの内容に差し替える)

Alembic 初期リビジョン `0001_initial` の PGroonga 節として、テーブル作成(plans/02 §4)の後に上から順に実行する。

```sql
-- =====================================================================
-- PGroonga 全文検索インデックス(正: plans/11-search.md §2.2)
-- 前提: CREATE EXTENSION pgroonga;(plans/02 §4.1)
--       groonga-tokenizer-mecab / TokenFilterStem プラグイン導入済み(§2.4)
-- =====================================================================

-- (1) 本文・原文(英語)— 横断検索/論文内検索の原文面
CREATE INDEX pgroonga_block_search_index_source_text
    ON block_search_index USING pgroonga (source_text)
    WITH (tokenizer     = 'TokenBigram',
          normalizers   = 'NormalizerNFKC150',
          plugins       = 'token_filters/stem',
          token_filters = 'TokenFilterStem');

-- (2) 本文・訳文(日本語)— 横断検索/論文内検索の訳文面
CREATE INDEX pgroonga_translation_units_text_ja
    ON translation_units USING pgroonga (text_ja)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

-- (3) メモ(タイトル+本文 Markdown)
CREATE INDEX pgroonga_notes_body
    ON notes USING pgroonga (title, body_md)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

-- (4) 注釈 — 検索対象は kind='comment' の body のみ。
--     決定: quote(原文引用スナップショット)は索引しない。本文原文と同一文字列で
--     あり、(1) のヒットと二重に出るだけのため(担当範囲どおり「注釈=コメント」)。
CREATE INDEX pgroonga_annotations_text
    ON annotations USING pgroonga (body)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

-- (5) チャット履歴(content JSONB から導出した平文)
CREATE INDEX pgroonga_chat_messages_text
    ON chat_messages USING pgroonga (text_plain)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

-- (6) 記事(article_blocks の平文)
CREATE INDEX pgroonga_article_blocks_text
    ON article_blocks USING pgroonga (text_plain)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

-- (7) 書誌・英語面(タイトル+アブスト原文)
CREATE INDEX pgroonga_papers_biblio_en
    ON papers USING pgroonga (title, abstract)
    WITH (tokenizer     = 'TokenBigram',
          normalizers   = 'NormalizerNFKC150',
          plugins       = 'token_filters/stem',
          token_filters = 'TokenFilterStem');

-- (8) 書誌・日本語面(アブスト訳)
CREATE INDEX pgroonga_papers_biblio_ja
    ON papers USING pgroonga (abstract_ja)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');

-- (9) 語彙帳(4d ヘッダ「語彙を検索」= GET /api/vocab?q= 用。横断検索 4e の対象外)
CREATE INDEX pgroonga_vocab_entries_text
    ON vocab_entries USING pgroonga (term, meaning_short, meaning_long)
    WITH (tokenizer = 'TokenMecab', normalizers = 'NormalizerNFKC150');
```

- インデックス数は **9 本**(plans/02 §4.14 の 7 本から、書誌の 2 分割+語彙帳の追加で +2)。多カラム指定(3)(7)(9)は「同一トークナイザで検索する列の束」であり、クエリは列ごとに `列 &@~ :pq` を OR で書く(§3.2。PGroonga は多カラムインデックスの各列条件に同一インデックスを使う)。
- 書誌を 2 本に分割した理由: `title`/`abstract`(英語)と `abstract_ja`(日本語)でトークナイザが異なるため。1 本のインデックスに 1 つのトークナイザしか指定できない。

### 2.3 PostgreSQL 設定

`postgresql.conf`(dev/prod 共通。docker-compose の `command` で指定):

```
shared_preload_libraries = 'pgroonga_wal_resource_manager'
pgroonga.enable_wal = on
```

- 決定: `pgroonga.enable_wal = on` を有効にする。理由: PGroonga インデックスは既定では WAL 非対応で、クラッシュ後に索引破損 → 全 REINDEX が必要になる。単一 VPS 運用(plans/01 §8)ではクラッシュ復旧の自動化が必須。
- 破損時の手動復旧手順(運用 Runbook): `REINDEX INDEX CONCURRENTLY pgroonga_<name>;` を §2.2 の 9 本に対して順に実行する(サービス無停止)。

### 2.4 依存パッケージ(デプロイ前提条件)

dev の DB イメージは plans/02 §7 の `groonga/pgroonga:latest-debian-16` を拡張し、MeCab トークナイザを追加する。`infra/db/Dockerfile`:

```dockerfile
FROM groonga/pgroonga:latest-debian-16
RUN apt-get update \
 && apt-get install -y --no-install-recommends groonga-tokenizer-mecab \
 && rm -rf /var/lib/apt/lists/*
```

- `TokenFilterStem` は groonga 本体同梱プラグイン(`token_filters/stem`)であり追加パッケージ不要。MeCab 辞書は groonga-tokenizer-mecab 既定の IPADIC を使う(辞書カスタマイズはしない)。
- 起動時ヘルスチェック(apps/api の `readyz` に追加): `SELECT pgroonga_command('tokenizer_list');` の結果に `TokenMecab` が含まれることを検証し、含まれなければ readiness を落とす(黙って TokenBigram にフォールバックしない。P3)。
- 本番(マネージド PG を使う場合)も同条件が前提。満たせない場合は自前 PG コンテナで運用する(plans/01 §8 の単一 VPS 構成では自前コンテナが既定)。

## 3. 検索クエリパイプライン

処理は 5 段: **入力正規化 → PGroonga クエリ構築 → 源別ヒット取得(両面同時)→ 同一ブロック統合・Q/A ペア統合 → グループ化・整形**。実装は `apps/api/src/alinea_api/services/search_service.py` に集約する。

### 3.1 入力正規化とクエリ構築

1. `q` を受け取り、前後空白 trim・連続空白を半角 1 個に圧縮。空文字(空白のみで trim 後に空になる場合を含む)・201 字以上は **422** `validation_error`(plans/03 §15.1)。
2. **エスケープ(確定)**: SQL 内で `pgroonga_query_escape(:q)` を通し、演算子は **`&@~`(クエリ構文検索)** を使う。エスケープ後は Groonga クエリ構文の特殊文字(`( ) " \ : - + ~ * < >` 等)がすべてリテラル化され、**空白区切りの複数語は AND 検索**になる(`EMA teacher` = 「EMA」AND「teacher」)。
   - 決定: v1 はフレーズ検索(`"..."`)・OR・除外(`-`)などのクエリ演算子を**提供しない**(全入力をリテラル扱い)。理由: デザインに演算子 UI/ヘルプが存在せず、エスケープ一律のほうが「黙って壊れない」。v2: 引用符フレーズのみ解釈。
3. ハイライト用キーワード配列は `pgroonga_query_extract_keywords(pgroonga_query_escape(:q))` で SQL 側から取得する(§5)。

### 3.2 源別ヒット取得 SQL(完全形)

バインド変数: `:user_id`(UUID)/ `:q`(text)/ `:style`(text。ユーザー設定の既定翻訳スタイル `users.settings->'translation'->>'style'`、未設定は `'natural'`)。以下の CTE 群を 1 文で実行する(ファセット集計用。ページ取得は §3.5)。

```sql
WITH params AS (
  SELECT pgroonga_query_escape(:q) AS pq
),
-- ユーザーの検索スコープ: ライブラリ各行が「いま見ている」リビジョン
-- (adopt-revision 前は旧リビジョンのまま = P6「勝手に切り替えない」と整合)
my_items AS (
  SELECT li.id AS library_item_id,
         li.paper_id,
         COALESCE((li.reading_position->>'revision_id')::uuid,
                  p.latest_revision_id)                    AS revision_id
  FROM library_items li
  JOIN papers p ON p.id = li.paper_id
  WHERE li.user_id = :user_id
),
-- (a) 本文・原文(英語面)
hit_body_source AS (
  SELECT mi.library_item_id, b.revision_id, b.block_id,
         pgroonga_score(b.tableoid, b.ctid)::numeric AS score,
         dr.created_at AS hit_at
  FROM block_search_index b
  JOIN my_items mi ON mi.revision_id = b.revision_id
  JOIN document_revisions dr ON dr.id = b.revision_id
  WHERE b.source_text &@~ (SELECT pq FROM params)
),
-- (b) 本文・訳文(日本語面)。表示解決(plans/02 §5.2)の勝者ユニットのみ対象
--     = personal フォークで上書きされた block の shared 旧訳にはヒットさせない
hit_body_translation AS (
  SELECT mi.library_item_id, ts.revision_id, tu.block_id,
         pgroonga_score(tu.tableoid, tu.ctid)::numeric AS score,
         tu.updated_at AS hit_at
  FROM translation_units tu
  JOIN translation_sets ts ON ts.id = tu.set_id
  JOIN my_items mi ON mi.revision_id = ts.revision_id
  WHERE tu.text_ja &@~ (SELECT pq FROM params)
    AND ts.style = :style
    AND (ts.scope = 'shared' OR ts.user_id = :user_id)
    AND tu.set_id = (
      SELECT u2.set_id
      FROM translation_units u2
      JOIN translation_sets s2 ON s2.id = u2.set_id
      WHERE s2.revision_id = ts.revision_id
        AND s2.style = :style
        AND (s2.scope = 'shared' OR s2.user_id = :user_id)
        AND u2.block_id = tu.block_id
      ORDER BY (s2.scope = 'personal') DESC
      LIMIT 1
    )
),
-- (c) 同一ブロック同一視(§3.4): FULL OUTER JOIN で 1 行に統合
hit_body AS (
  SELECT COALESCE(s.library_item_id, t.library_item_id) AS library_item_id,
         COALESCE(s.revision_id,     t.revision_id)     AS revision_id,
         COALESCE(s.block_id,        t.block_id)        AS block_id,
         COALESCE(s.score, 0) + COALESCE(t.score, 0)    AS score,
         (s.block_id IS NOT NULL)                       AS matched_source,
         (t.block_id IS NOT NULL)                       AS matched_translation,
         GREATEST(COALESCE(s.hit_at, '-infinity'),
                  COALESCE(t.hit_at, '-infinity'))      AS hit_at
  FROM hit_body_source s
  FULL OUTER JOIN hit_body_translation t
    ON t.revision_id = s.revision_id AND t.block_id = s.block_id
),
-- (d) メモ
hit_note AS (
  SELECT n.library_item_id, n.id AS note_id,
         pgroonga_score(n.tableoid, n.ctid)::numeric AS score,
         n.created_at AS hit_at
  FROM notes n
  JOIN library_items li ON li.id = n.library_item_id AND li.user_id = :user_id
  WHERE n.title   &@~ (SELECT pq FROM params)
     OR n.body_md &@~ (SELECT pq FROM params)
),
-- (e) 注釈(コメントのみ。§2.2 (4) の決定)
hit_annotation AS (
  SELECT a.library_item_id, a.id AS annotation_id, a.anchor,
         pgroonga_score(a.tableoid, a.ctid)::numeric AS score,
         a.created_at AS hit_at
  FROM annotations a
  JOIN library_items li ON li.id = a.library_item_id AND li.user_id = :user_id
  WHERE a.kind = 'comment'
    AND a.body &@~ (SELECT pq FROM params)
),
-- (f) チャット: メッセージ単位でヒットさせ、Q/A ペアに正規化して 1 件に統合
--     (4e のスニペットは「Q: … — A: …」形式。ペアキー=直前のユーザーメッセージ id)
hit_chat_raw AS (
  SELECT th.library_item_id, m.thread_id, m.id AS message_id, m.role,
         pgroonga_score(m.tableoid, m.ctid)::numeric AS score,
         m.created_at AS hit_at
  FROM chat_messages m
  JOIN chat_threads  th ON th.id = m.thread_id
  JOIN library_items li ON li.id = th.library_item_id AND li.user_id = :user_id
  WHERE m.text_plain &@~ (SELECT pq FROM params)
),
hit_chat AS (
  SELECT library_item_id, thread_id,
         MIN(message_id) AS message_id,        -- 遷移先=ペア内でヒットした先頭メッセージ
         SUM(score)      AS score,
         MAX(hit_at)     AS hit_at
  FROM (
    SELECT r.*,
           COALESCE(
             CASE WHEN r.role = 'user' THEN r.message_id
                  ELSE (SELECT m2.id FROM chat_messages m2
                        WHERE m2.thread_id = r.thread_id
                          AND m2.role = 'user' AND m2.id < r.message_id
                        ORDER BY m2.id DESC LIMIT 1)
             END, r.message_id) AS pair_key
    FROM hit_chat_raw r
  ) x
  GROUP BY library_item_id, thread_id, pair_key
),
-- (g) 記事
hit_article AS (
  SELECT ar.library_item_id, ab.article_id, ab.id AS article_block_id,
         pgroonga_score(ab.tableoid, ab.ctid)::numeric AS score,
         ab.updated_at AS hit_at
  FROM article_blocks ab
  JOIN articles       ar ON ar.id = ab.article_id
  JOIN library_items  li ON li.id = ar.library_item_id AND li.user_id = :user_id
  WHERE ab.text_plain &@~ (SELECT pq FROM params)
),
-- (h) 書誌(タイトル・アブスト・アブスト訳)。ヒット源としては「本文」に合流(§4)
hit_biblio AS (
  SELECT mi.library_item_id,
         pgroonga_score(p.tableoid, p.ctid)::numeric AS score,
         (COALESCE(p.title    &@~ (SELECT pq FROM params), false)
          OR COALESCE(p.abstract &@~ (SELECT pq FROM params), false))
                                                     AS matched_source,  -- abstract が NULL でも false に確定
         COALESCE(p.abstract_ja &@~ (SELECT pq FROM params), false)
                                                     AS matched_translation,
         p.created_at AS hit_at
  FROM papers p
  JOIN my_items mi ON mi.paper_id = p.id
  WHERE p.title       &@~ (SELECT pq FROM params)
     OR p.abstract    &@~ (SELECT pq FROM params)
     OR p.abstract_ja &@~ (SELECT pq FROM params)
),
-- 統合ヒット集合(source は plans/03 §15.1 の SearchHit.source + 内部値 'biblio')
hits AS (
  SELECT library_item_id, 'body'::text AS source, score, hit_at,
         jsonb_build_object('revision_id', revision_id, 'block_id', block_id,
                            'matched_source', matched_source,
                            'matched_translation', matched_translation) AS ref
  FROM hit_body
  UNION ALL
  SELECT library_item_id, 'note', score, hit_at,
         jsonb_build_object('note_id', note_id) FROM hit_note
  UNION ALL
  SELECT library_item_id, 'annotation', score, hit_at,
         jsonb_build_object('annotation_id', annotation_id, 'anchor', anchor)
  FROM hit_annotation
  UNION ALL
  SELECT library_item_id, 'chat', score, hit_at,
         jsonb_build_object('thread_id', thread_id, 'message_id', message_id)
  FROM hit_chat
  UNION ALL
  SELECT library_item_id, 'article', score, hit_at,
         jsonb_build_object('article_id', article_id,
                            'article_block_id', article_block_id)
  FROM hit_article
  UNION ALL
  SELECT library_item_id, 'biblio', score, hit_at,
         jsonb_build_object('matched_source', matched_source,
                            'matched_translation', matched_translation)
  FROM hit_biblio
)
-- 総件数・源別ファセット・論文数(「12 件 · 3 論文」)
SELECT count(*)                                                     AS total,
       count(*) FILTER (WHERE source IN ('body', 'biblio'))         AS facet_body,
       count(*) FILTER (WHERE source IN ('note', 'annotation'))     AS facet_notes,
       count(*) FILTER (WHERE source = 'chat')                      AS facet_chat,
       count(*) FILTER (WHERE source = 'article')                   AS facet_article,
       count(DISTINCT library_item_id)                              AS paper_count
FROM hits;
```

「論文で絞る」ファセット(件数降順・上位 20 件に確定):

```sql
SELECT h.library_item_id, p.title, count(*) AS count
FROM hits h
JOIN library_items li ON li.id = h.library_item_id
JOIN papers p         ON p.id = li.paper_id
GROUP BY h.library_item_id, p.title
ORDER BY count DESC, p.title ASC
LIMIT 20;
```

ファセット絞り込み(`source` / `library_item_id` クエリパラメータ)は `hits` の直後に WHERE を挿す:

```sql
-- source=notes の例(plans/03 §15.1: notes=メモ・注釈)
WHERE h.source IN ('note', 'annotation')
-- source=body は IN ('body','biblio')、chat / article はそのまま等値
-- library_item_id 指定時: AND h.library_item_id = :library_item_id
```

### 3.3 スコアリング

- 各行のスコアは `pgroonga_score(tableoid, ctid)`(既定アルゴリズム=マッチ数)。**追加の重み付けはしない**(決定。デザインの「並び: 関連度」以上の仕様が存在せず、調整はデータが貯まってから)。
- 本文の統合ヒットは原文スコア+訳文スコアの和(§3.2 (c))。チャットは Q/A ペア内の和(§3.2 (f))。
- グループ(論文)のスコア `group_score` = グループ内ヒットの **MAX(score)**。並び順: `sort=relevance`(既定)は `group_score DESC, group_at DESC, library_item_id ASC`、`sort=recency` は `group_at DESC, library_item_id ASC`(`group_at` = グループ内 `MAX(hit_at)`)。グループ内は `score DESC, hit_at DESC`。
- 4e の「並び: 関連度 ▾」ドロップダウンの選択肢は **「関連度」(`relevance`)/「新しい順」(`recency`)の 2 つ**に確定(plans/03 §15.1 の enum と 1:1)。

### 3.4 日英クロスと同一ブロック同一視

- 原文面(`block_search_index.source_text`)と訳文面(`translation_units.text_ja`)を**常に両方**検索し(§1 の決定)、同一 `(revision_id, block_id)` は FULL OUTER JOIN で 1 行に統合する(§3.2 (c))。
- `matched_in` の値: 原文のみ=`["source"]` / 訳文のみ=`["translation"]` / 両方=`["source","translation"]`。
- スニペットの採用面(確定): `matched_in` に `source` を含むなら**原文スニペット**(`snippet_lang: "en"`)、`translation` のみなら**訳文スニペット**(`snippet_lang: "ja"`)。1 ヒット 1 スニペット(plans/03 §15.1 の型どおり)。クライアントは `matched_in` が 2 要素のときソースバッジを「本文 · 原文」「本文 · 訳文」の 2 個並べて描画する(4e の見た目を 1 ヒットで再現。§4)。
- 件数は統合後に数える(重複カウントしない。docs/06 §9.2)。統合ヒットのメタ 2 行目に相当する説明(「同一ブロックの訳文 — 原文ヒットと同一視」)は、クライアントが `matched_in.length === 2` のとき `display` の後ろに固定文言で付す。
- 書誌ヒットも同形式: `title`/`abstract` ヒット=`matched_source`、`abstract_ja` ヒット=`matched_translation`、両方なら 1 件のまま併記。

### 3.5 論文単位グループ化とページング SQL

`GET /api/search` のグループページ取得(§3.2 の `hits` CTE に続けて実行。`:limit` = グループ数、既定 10・最大 20):

```sql
, groups AS (
  SELECT h.library_item_id,
         MAX(h.score)  AS group_score,
         MAX(h.hit_at) AS group_at,
         count(*)      AS hit_count
  FROM hits h
  -- (ファセット絞り込みの WHERE はここに入る)
  GROUP BY h.library_item_id
),
page AS (
  SELECT g.*
  FROM groups g
  -- keyset(sort=relevance 時。ORDER BY の 3 キーと同一の展開述語 — 方向が混在するため行値比較は使わない)
  WHERE (:cursor_score IS NULL)                       -- 先頭ページ
     OR  g.group_score < :cursor_score
     OR (g.group_score = :cursor_score AND g.group_at < :cursor_at)
     OR (g.group_score = :cursor_score AND g.group_at = :cursor_at
         AND g.library_item_id > :cursor_library_item_id)
  -- sort=recency 時は group_score 項を除いた同形(group_at < :cursor_at OR (= AND id >))
  ORDER BY g.group_score DESC, g.group_at DESC, g.library_item_id
  LIMIT :limit + 1                                    -- +1 で next_cursor 判定
)
SELECT p.library_item_id, p.group_score, p.hit_count,
       ranked.source, ranked.score, ranked.hit_at, ranked.ref
FROM page p
JOIN LATERAL (
  SELECT h.source, h.score, h.hit_at, h.ref,
         row_number() OVER (ORDER BY h.score DESC, h.hit_at DESC) AS rn
  FROM hits h
  WHERE h.library_item_id = p.library_item_id
  -- (同じファセット WHERE)
) ranked ON ranked.rn <= 5                            -- グループ内の返却は上位5件
ORDER BY p.group_score DESC, p.group_at DESC, p.library_item_id,
         ranked.rn;
```

- **決定**: グループ内の返却ヒットは**上位 5 件**、グループ総数は `hit_count` で返す(4e のグループヘッダ「7 件」表示。表示 3 件+残りはグループヘッダの件数から全件表示に展開— クライアントは 5 件超のとき末尾に「他 n 件」行を出し、クリックで「論文で絞る」ファセット適用に切り替える)。
- カーソル: plans/03 §1.5 の形式 `base64url({"k": <ソートキー値>, "id": "<library_item_id>"})`。決定: `sort=relevance` 時の `k` は 2 要素配列 `[group_score, group_at(ISO 8601)]`(ORDER BY のタイブレーク `group_at` まで keyset に含めないと同スコア境界で行が重複/欠落するため)。`sort=recency` 時は `k` = `group_at`(ISO 8601)単値。
- グループヘッダ用の `LibraryItemSummary` と記事メタ(§6.1)は `page.library_item_id` で別クエリ一括取得(N+1 にしない)。

## 4. ヒット源バッジのマッピング表(確定)

コンポーネントは plans/08 §5 の `SourceBadge`(h16px / padding 0 6px / radius 3px / font 9.5px 700)。色トークンは plans/08 §2 の `--pr-src-*`。

| API `source` | `matched_in` | 4e バッジ文言 | 1e ドロップダウン文言 | SourceBadge variant | 背景 / 文字トークン | スニペット書体(`snippet_lang`) | 遷移リンク文言 |
|---|---|---|---|---|---|---|---|
| `body` | `["source"]` | 本文 · 原文 | 本文でヒット | `body` | `--pr-src-body-bg` / `--pr-src-body-fg` | `'Source Serif 4',Georgia,serif`(`en`) | 該当位置へ →(1e: 該当位置へジャンプ →) |
| `body` | `["translation"]` | 本文 · 訳文 | 本文でヒット | `body` | 同上 | `var(--pr-jp,'Noto Serif JP'),serif`(`ja`) | 該当位置へ → |
| `body` | 2 要素 | 本文 · 原文 と 本文 · 訳文 の 2 バッジ並記 | 本文でヒット | `body` ×2 | 同上 | 原文側(`en`。§3.4) | 該当位置へ → |
| `body`(書誌。`display:"書誌"`) | 面に応じ同上 | 本文 · 原文 / 本文 · 訳文 | 本文でヒット | `body` | 同上 | ヒット面に応じ `en`/`ja` | 該当位置へ → |
| `note` | `null` | メモ | あなたのメモ | `note` | `--pr-src-note-bg`(rgba(101,148,113,0.16)) / `--pr-src-note-fg`(#4C7458) | UI 既定(IBM Plex Sans JP) | メモを開く → |
| `annotation` | `null` | メモ | あなたのメモ | `note` | 同上 | UI 既定 | 該当位置へ → |
| `chat` | `null` | チャット | チャット履歴 | `chat` | `--pr-src-chat-bg`(rgba(110,90,126,0.14)) / `--pr-src-chat-fg`(#6E5A7E) | UI 既定 | スレッドを開く → |
| `article` | `null` | 記事 | 記事 | `article` | `--pr-src-article-bg`(#F1EFE9) / `--pr-src-article-fg`(#777B81) | UI 既定 | 記事モードで開く → |

- 決定: 注釈(コメント)ヒットのバッジは 4e ファセット「メモ・注釈」の束ねに合わせて **「メモ」表記・`note` バリアント**を使う(デザインに注釈専用バッジが存在しないため)。遷移のみ本文位置ジャンプ(§7)。
- `display` フィールド(位置メタ)の組み立て規則(サーバー側。plans/03 §15.1 の `display`):
  - 本文: `§{section_label} {セクション見出しテキスト}` + 品質 B で `page` があれば ` · p.{page}`(例 `§3.2 Training via Distillation · p.5`)。セクション見出しテキストは同一 revision の `block_search_index` から `block_type='heading' AND section_path = ヒットの section_path` の `source_text` を一括取得して付す。statement 型ブロックは `element_label`(`式(5)`/`図2`/`表1`)を優先。
  - 書誌: 固定文字列 `書誌`。
  - メモ: `メモ · {M/D}` + `notes.anchors[0]` があれば ` · 根拠: {AnchorRef.display}`(例 `メモ · 6/20 · 根拠: §2.3`)。
  - 注釈: `注釈 · {M/D} · {AnchorRef.display}`。
  - チャット: `{スレッド名} · {M/D}`(メインスレッドは「メインスレッド」。例 `メインスレッド · 6/28`)。
  - 記事: `「{直前 heading ブロックの text}」セクション`(例 `「なぜ直線なのか」セクション`)。heading が無い先頭ブロックは `記事冒頭`。
- グループヘッダ(4e): サムネ 24×32px + タイトル + 著者・会議(`LibraryItemSummary.paper`)+ ステータスピル(`StatusPill`。記事のみのグループには出さない)+ 右端 `{hit_count} 件`。記事のみのグループはヘッダを `記事: {articles.title}` + `記事(自動構成) · {M/D}` 表記に切り替える(§6.1 の `article` フィールドで判定)。

## 5. スニペット生成とハイライト

### 5.1 サーバー側生成(確定手順)

スニペットは**返却するヒットに対してのみ**生成する(全結果=最大 20 グループ×5 件、プレビュー=3 件)。ページ確定後にヒット源ごとの対象テキストへ次を適用:

```sql
-- 例: 本文原文ヒット 1 件のスニペット
SELECT pgroonga_snippet_html(
         b.source_text,
         pgroonga_query_extract_keywords(pgroonga_query_escape(:q)),
         300                                   -- 断片幅 300 バイト(UTF-8。日本語≈100字/英語≈300字)
       ) AS snippets                           -- text[](最大3断片)
FROM block_search_index b
WHERE b.revision_id = :revision_id AND b.block_id = :block_id;
```

- 採用は `snippets[1]`(先頭断片)のみ。断片が本文の先頭/末尾に接していない場合の省略記号「…」は**アプリ層で常に前後に付す**(4e の見た目)。`pgroonga_snippet_html` は対象テキストを HTML エスケープして返すため XSS 安全。
- `pgroonga_snippet_html` はキーワードを `<span class="keyword">` で包む。アプリ層で決定的置換を行い最終形にする:
  - `<span class="keyword">` → `<mark class="alinea-search-hit">`
  - `</span>` → `</mark>`
- **`<mark>` クラスは `alinea-search-hit` に確定**(plans/08 §5 HighlightMark 節の決定に一致)。CSS(packages/tokens 隣接の ui 層):

```css
mark.alinea-search-hit {
  background: rgba(196, 148, 50, 0.30);   /* 琥珀 30%(4e 実測) */
  color: inherit;
  border-radius: 2px;
  padding: 0 1px;
}
```

- ⚠ plans/03 §15.1 の記載 `<mark class="hit">` は `<mark class="alinea-search-hit">` へ修正する(§11 R-3)。
- 源別の対象テキスト: 本文原文=`source_text` / 本文訳文=勝者ユニットの `text_ja` / メモ=`body_md`(決定: `body_md &@~ :pq` が false のときのみ `title` を対象にする — 判定はスニペット生成クエリ内で同条件を再評価)/ 注釈=`body` / チャット=ヒットメッセージの `text_plain` / 記事=`text_plain` / 書誌=ヒットした列(`title` → `abstract` → `abstract_ja` の優先順。各列の `&@~` 再評価で先頭のヒット列を選ぶ)。
- **チャットの Q/A 形式(確定)**: スニペットは `Q: {ユーザー側断片} — A: {アシスタント側断片}` に組み立てる。ヒットした側は上記スニペット関数の出力、相手側はペアメッセージ `text_plain` の先頭 60 文字(ハイライトなし。60 字超は「…」)。ペアの片側が存在しない場合はある側のみ(`Q:` / `A:` プレフィックスは維持)。
- スニペット上限: 1 ヒット 1 断片・HTML 込み最大 500 文字(超過はアプリ層で切り詰め+`…`)。

### 5.2 クライアント側の書体切替

`snippet_lang` に応じて 4e 実測どおり: `en`=`'Source Serif 4',Georgia,serif` / `ja`=`var(--pr-jp,'Noto Serif JP'),serif` / チャット・メモ・注釈・記事(`snippet_lang` は常に `ja` を返すが書体は UI 既定)= IBM Plex Sans JP。スニペット共通スタイル: font-size 11.5px / line-height 1.7 / color #33373C(4e 実測。ドロップダウンは #3C4046)。

## 6. 検索 API(plans/03 §15 の実装確定)

### 6.1 GET /api/search(全結果画面 4e)

パス・クエリ・レスポンス型は plans/03 §15.1 のとおり(再掲しない)。本書で確定する実装事項:

- レート制限: `GET /api/search*` は **60 回/分/ユーザー**(plans/03 §1.8 の表)。
- `q` 1〜200 字。1 字でも実行する(API 制約は plans/03 §15.1 のまま。発火抑制はクライアント §6.3)。
- ファセット件数(`facets.source` / `facets.papers`)は**絞り込み前の全ヒット集合**に対して計算する(ファセット UI の件数が選択によって消えない)。`total` / `paper_count` も同様に絞り込み前。`groups` のみ絞り込み後。
- **⚠ レスポンス型への追加(§11 R-2)**: 4e の描画に必要な次の 2 フィールドを `groups[]` に追加する。
  ```ts
  groups: {
    library_item: LibraryItemSummary;
    hit_count: number;                 // グループ内総ヒット数(「7 件」)
    article: { article_id: string; title: string; generated_at: string } | null;
                                       // 記事ヒットを含む場合のみ。記事のみのグループは
                                       // ヘッダを「記事: {title}」「記事(自動構成) · M/D」表記に切替
    hits: SearchHit[];                 // 上位5件(§3.5)
  }[];
  ```
- **⚠ SearchHit.target の viewer anchor を nullable に(§11 R-2)**: 書誌ヒットは特定ブロックを持たないため `{ kind: "viewer"; library_item_id: string; anchor: AnchorRef | null }` とし、`anchor: null` は「論文の先頭を開く」。
- `SearchHit.source` に `biblio` は**追加しない**。書誌ヒットは `source: "body"` / `display: "書誌"` で返す(4e ファセット「本文(原文・訳文)」に合算される仕様表現)。

### 6.2 GET /api/search/preview(1e ドロップダウン)

- クエリ: `q` のみ。`limit` は **3 固定**(plans/03 §15.2)。
- 実装: §3.2 の `hits` を `ORDER BY score DESC, hit_at DESC, library_item_id ASC LIMIT 3` で平坦に取り(3 キーで決定的順序)、`total` は同 CTE の `count(*)`。グループ化しない。
- レスポンスの各 item は `SearchHit & { library_item: { id, title } }`(plans/03 §15.2)。
- 性能目標 p50 300ms。プレビューはファセット・論文別集計を**行わない**(total と 3 件のみ)ことで達成する。

### 6.3 クライアント挙動(1e ドロップダウン)

- 起動: 検索ボックスクリックまたは **⌘K**(docs/06 §8.1)。コンポーネントは plans/08 §5 の `SearchBox`(w460 フォーカスリング付き)+`Popover`(width 560 / caret なし / `--z-dropdown`=6 / top 48px・left 230px 相当のアンカー配置)。
- **デバウンス 250ms に確定**(plans/01 §3.4 の値を踏襲)。加えて**正規化後 2 文字未満では発火しない**(決定: 1 文字クエリはヒットが膨大でプレビューの意味がないため。直前の結果があればそれを保持して表示し続け、まだ一度も結果を取得していなければ結果リスト・フッタを描画しない=ヘッダとヒントのみ)。進行中リクエストは AbortController でキャンセルし、常に最後の入力の結果だけを表示する。
- 表示: ヘッダ「「{q}」の結果 {total} 件」+右「本文・訳文・メモ・チャット・記事を横断」/ 結果 3 件(1 件目がキーボード選択状態 `--pr-bg-hover`)/ フッタ「すべての結果を表示({total} 件)→」。
- キー操作: ↑↓ で選択移動、Enter で選択ヒットの遷移先(§7)へ、フッタ選択時 Enter または フッタクリックで `/search?q={q}` へ、**esc で閉じる**(「esc で閉じる」ヒント表示)。
- 全結果画面のルートは `apps/web/src/app/(app)/search/page.tsx`。URL: `/search?q={q}&source={all|body|notes|chat|article}&library_item_id={li_…}&sort={relevance|recency}`(パラメータ名は API と同一。省略時は API 既定と同じ)。ファセット・ソート変更は URL クエリを書き換えて再フェッチ(ブラウザバックで状態復元)。
- 4e トップバー右の「◷」は**通知アイコン**(4a と同一コンポーネント。docs/06 §7 の定義に従う。検索履歴機能ではない — 決定)。
- 空結果: `EmptyState`(plans/08)で「「{q}」に一致する結果はありませんでした」+補足「日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)」を表示する。

### 6.4 GET /api/revisions/{revision_id}/search(論文内検索 `/`)

plans/03 §6.7 の型のまま。実装は §3.2 の (a)(b)(c) を `my_items` の代わりに固定 `revision_id`(+当該ユーザーの style 解決)で実行し、`ORDER BY (ヒットブロックの position) ASC LIMIT :limit`(既定 50・最大 100 — plans/03 §1.5 の基本値に確定)。スニペット・`<mark>` は §5 と同一。書誌・メモ等は対象外(本文のみ)。

### 6.5 GET /api/vocab?q=(語彙帳内検索)

plans/03 §11.1 の `q` パラメータの実装。`q` は一覧クエリの WHERE 条件の 1 つであり、§11.1 の他パラメータ(`kind` / `due` / `library_item_id`)・`sort`(既定 `added_at` 降順 = DB 列 `created_at`)・`cursor`/`limit`(既定 50・最大 100)とそのまま AND で組み合わせる:

```sql
SELECT v.*
FROM vocab_entries v
WHERE v.user_id = :user_id
  AND (v.term          &@~ pgroonga_query_escape(:q)
    OR v.meaning_short &@~ pgroonga_query_escape(:q)
    OR v.meaning_long  &@~ pgroonga_query_escape(:q))
  -- (§11.1 の kind / due / library_item_id 条件をここに AND で追加)
ORDER BY v.created_at DESC, v.id;   -- 一覧の既定順を維持(スコア順にしない — 決定)
```

## 7. 源別遷移先 URL 規則(確定)

ビューアのルートは `apps/web/src/app/(app)/papers/[itemId]/page.tsx`(plans/00)。クエリパラメータで位置・パネル・ハイライトを指定する。**この規則が「該当位置へ →」系リンクの唯一の正**であり、`SearchHit.target` からクライアントが決定的に URL を組み立てる。

| 源 | `target.kind` | 遷移先 URL | 挙動 |
|---|---|---|---|
| 本文(原文/訳文/両方) | `viewer` | `/papers/{library_item_id}?block={block_id}&hl={q}` | 現在の表示モード(`reading_position.view_mode`)を維持したまま該当ブロックへスクロール。PDF モード中は `block_search_index` の `page`+`bbox` で紙面位置へ(品質 A で bbox が無い場合は構造化ビューに切替えてスクロール) |
| 書誌 | `viewer`(`anchor: null`) | `/papers/{library_item_id}` | 論文の先頭を開く |
| 注釈 | `viewer` | `/papers/{library_item_id}?block={anchor.block_id}&panel=annotations&annotation={annotation_id}&hl={q}` | 該当ブロックへスクロール+サイドパネル「注釈」タブを開き該当注釈行を選択状態に |
| メモ | `note` | `/papers/{library_item_id}?panel=notes&note={note_id}&hl={q}` | サイドパネル「メモ」タブを開き該当メモへスクロール・`hl` 語をハイライト |
| チャット | `chat` | `/papers/{library_item_id}?panel=chat&thread={thread_id}&message={message_id}&hl={q}` | サイドパネル「チャット」タブ+該当スレッドに切替、該当メッセージへスクロール |
| 記事 | `article` | `/papers/{library_item_id}?view=article&article_block={article_block_id}&hl={q}` | 記事モードに切替、該当記事ブロックへスクロール |

パラメータ仕様(ビューア側の解釈。plans/09-screens/viewer-shell.md ビューアシェル計画の実装対象):

- `view`: `translation | parallel | source | pdf | article`。**本文ヒットでは付けない**(決定: ユーザーの現行モードを尊重する。P6)。記事ヒットのみ `view=article` を強制。
- `block`: ドキュメントブロック ID(`blk-…`)。`article_block`: 記事ブロック ID。`note` / `annotation` / `thread` / `message`: 各 API ID(`note_…` / `ann_…` / `th_…` / `msg_…`)。
- `hl`: 検索クエリの URL エンコード値(最大 200 字)。ビューアは**遷移先ブロック(またはパネル内該当項目)の中だけ**で、`hl` を空白分割した各語を大文字小文字非依存の文字列一致で `<mark class="alinea-search-hit">` に包む。ステミング一致(例: `stabilize` クエリが `stabilizes` にヒット)で文字列一致が 0 件の場合はマークなし(ブロックフラッシュのみ)。`hl` はページ内遷移で保持せず、次のナビゲーションで消える。
- スクロール表現(確定): 対象ブロックを `scrollIntoView({ block: "center" })` し、背景を `--pr-bg-hover`(#FAF9F5)→透明へ **2,000ms** の ease-out でフラッシュする。
- 決定: `orphaned = true` の注釈(リアンカー失敗。plans/02 §4.7)も検索対象に含める。遷移時は `block`(= `anchor.block_id`)が現リビジョンに存在しない場合ブロックスクロールをスキップし、サイドパネル「注釈」タブの該当行選択のみ行う(「未配置」表示は注釈パネル側の既存仕様)。
- パラメータ処理後も URL は書き換えない(ブラウザバックで検索結果に戻れる)。

## 8. ライブラリのフィルタ検索(構造化フィルタ)

横断検索(PGroonga)とは別系統の、`GET /api/library-items`(plans/03 §5.1)の SQL 実装。

### 8.1 一覧クエリ完全形

バインド: `:quick`(text。クエリ未指定時は `'all'` をバインド)/ `:statuses`(text[]。空配列=未指定)/ `:tags`(text[])/ `:collection_id`(uuid|NULL)/ `:quality`(text|NULL)/ `:years`(int[])/ `:q`(text|NULL)。DB 列挙値は plans/02(`to_read/read_soon/reading/finished/revisit/on_hold`)、API 列挙値は plans/03 §1.6(`planned/up_next/reading/done/reread/on_hold`)で、ルーター層が 1:1 変換する。

```sql
SELECT li.*, p.title, p.authors, p.venue, p.published_on, p.arxiv_id,
       dr.quality_level
FROM library_items li
JOIN papers p ON p.id = li.paper_id
LEFT JOIN document_revisions dr
       ON dr.id = COALESCE((li.reading_position->>'revision_id')::uuid,
                           p.latest_revision_id)
WHERE li.user_id = :user_id
  -- クイックフィルタ(docs/06 §1 の合成。quick と status は積集合 — plans/03 §5.1)
  AND (:quick = 'all'
       OR (:quick = 'unread'      AND li.status IN ('to_read', 'read_soon'))
       OR (:quick = 'in_progress' AND li.status IN ('reading', 'on_hold'))
       OR (:quick = 'done'        AND li.status = 'finished')
       OR (:quick = 'recheck'     AND li.status = 'revisit'))
  -- 属性フィルタ 5 種(同一属性内 OR・属性間 AND — plans/03 §5.1)
  AND (cardinality(:statuses) = 0 OR li.status = ANY (:statuses))
  AND (cardinality(:tags)     = 0 OR li.tags && :tags)          -- GIN(plans/02 §4.6)
  AND (:collection_id IS NULL OR EXISTS (
         SELECT 1 FROM collection_entries ce
         WHERE ce.library_item_id = li.id
           AND ce.collection_id = :collection_id))
  AND (:quality IS NULL OR dr.quality_level = :quality)
  AND (cardinality(:years) = 0
       OR EXTRACT(YEAR FROM p.published_on)::int = ANY (:years))
  -- q = 書誌の簡易絞り込み(タイトル・著者の部分一致。PGroonga は使わない — 決定:
  -- ユーザーの行数(数十〜数百)に対する ILIKE で十分で、入力途中の部分語に強い)
  AND (:q IS NULL
       OR p.title ILIKE '%' || :q || '%'
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(p.authors) a
                  WHERE a->>'name' ILIKE '%' || :q || '%'))
ORDER BY /* §8.2 のソート式 */, li.id
LIMIT :limit + 1;   -- keyset カーソル(plans/03 §1.5)
```

### 8.2 ソートキー対応表(API → SQL)

plans/03 §5.1 の `sort` 値と SQL 式の対応。未設定値(「—」表示)は**昇順・降順とも常に末尾**(plans/03 の決定)。

| API `sort` | SQL 式 | NULL 処理 |
|---|---|---|
| `updated_at`(既定) | `li.updated_at` | なし |
| `added_at` | `li.added_at` | なし |
| `title` | `p.title COLLATE "C"` | なし |
| `deadline` | `li.deadline` | `NULLS LAST`(asc/desc とも) |
| `priority` | `CASE li.priority WHEN 'high' THEN 0 WHEN 'mid' THEN 1 WHEN 'low' THEN 2 END` | `NULLS LAST` |
| `reading_time` | `li.total_active_seconds` | なし(0 既定) |
| `comprehension` | `li.understanding` | `NULLS LAST` |

- 列ヘッダソート(1e「論文 ↑」)は `sort=title&order=asc` に対応(同一 API)。
- keyset カーソル条件: `(ソート値, li.id)` の行値比較(§3.5 と同形)。

### 8.3 保存フィルタの条件 JSON(確定)

**決定: `saved_filters.conditions` / `.sort` の格納形式は plans/03 §5.14 の `SavedFilterConditions` 型と完全同一の JSON とする**(API⇄DB で変換しない。クエリパラメータ語彙と 1:1):

```json
{
  "name": "cs.CV の未読",
  "conditions": {
    "quick": "unread",
    "status": ["planned"],
    "tags": ["cs.CV"],
    "collection_id": "col_01JZK3F8Q2W7",
    "quality": "A",
    "years": [2023, 2024]
  },
  "sort": { "key": "updated_at", "order": "desc" }
}
```

- 各キーは省略可(省略=その属性で絞らない)。`status` の値は **API 列挙**(`planned/up_next/reading/done/reread/on_hold`)で保存する(設定エクスポート/インポート時にそのまま通用させるため)。
- ⚠ plans/02 §3.8 は `collection_ids`(配列)/`quality`(配列)/`sort.dir` としており、plans/03 §5.14(単数 `collection_id` / 単数 `quality` / `sort.order`)と食い違う。**plans/03 を正**とし plans/02 §3.8 を修正する(§11 R-5)。1e の属性ドロップダウンは単一選択であり単数形が UI と一致する。
- 適用(`GET /api/library-items?filter_id=sf_…`): サーバーが `conditions`/`sort` を §8.1 のバインドに展開し、リクエストに明示されたクエリが同名項目を上書きする(plans/03 §5.1)。
- サイドバー「保存フィルタ」の件数バッジは `GET /api/saved-filters` 応答の `count`(リクエスト時に §8.1 の WHERE で `COUNT(*)` を実行する導出値。保存しない — plans/02 §1.5)。

## 9. インデックス更新タイミング(フック一覧)

PGroonga インデックスは行書き込みに同期追随するため、「インデックス更新」=「検索対象カラムの書き込み」である。**すべてドメイン書き込みと同一トランザクション内で行い、DB トリガ・非同期インデクサは使わない**(決定。更新経路をアプリ層 1 箇所に集約し、検索遅延ゼロ・整合性検証不要にする)。

| # | タイミング(トリガとなる操作) | 書き込み先(検索対象列) | 平文導出 |
|---|---|---|---|
| 1 | ingest ジョブの構造化段(`jobs.kind='ingest'`, `stage='structuring'` 完了時。apps/worker) | `block_search_index` を revision 単位で DELETE→INSERT(`document_revisions` INSERT と同一 Tx) | `block_to_plain()` |
| 2 | アブスト訳生成(`stage='translating_abstract'`) | `papers.abstract_ja` UPDATE | なし |
| 3 | 翻訳ユニット確定(translation ジョブのバッチ commit / 再翻訳採用 `POST /api/translation-units/{unit_id}/proposal/accept` / 手動編集 `PUT /api/translation-units/{unit_id}`) | `translation_units.text_ja` INSERT/UPDATE | `inline_to_plain(content_ja)` |
| 4 | メモ作成・編集(notes API の POST/PATCH。「↑メモに保存」「✦要約をメモに保存」経由を含む) | `notes.title` / `notes.body_md` | なし(生 Markdown を索引) |
| 5 | 注釈コメント作成・編集(annotations API) | `annotations.body`(`quote` は生成列で自動) | なし |
| 6 | チャットメッセージ確定(ユーザー送信時、およびアシスタント SSE `message.completed` での確定保存。`status='error'` の行も本文があれば索引される) | `chat_messages.text_plain` | `chat_content_to_plain(content)` |
| 7 | 記事の生成・指示つき再生成・ブロック書き直し(article ジョブの rendering 段) | `article_blocks.text_plain`(記事単位で DELETE→INSERT、ブロック書き直しは該当行 UPDATE) | `article_block_to_plain(type, content)` |
| 8 | 語彙エントリ保存・AI 生成完了(vocab ジョブ)・フィールド編集 | `vocab_entries.term` / `.meaning_short` / `.meaning_long` | なし |
| 9 | 新リビジョン採用(`POST /api/library-items/{id}/adopt-revision`) | **追加書き込みなし**。`block_search_index` はリビジョン単位で共存し、検索側が `reading_position` の COALESCE で新リビジョンを見る(§3.2 `my_items`)。注釈等のアンカー書き換えは plans/02 §5.3 | — |
| 10 | 各種削除 | FK `ON DELETE CASCADE` で索引行ごと消える(plans/02 §1.3) | — |

### 9.1 平文導出関数(単一実装を api / worker で共有)

配置: `apps/api/src/alinea_core/document/plaintext.py`(apps/worker は `alinea_core` を workspace 依存で参照 — plans/00 の構成)。**同一入力→同一出力の純関数**とし、プロパティテスト対象(plans/00 C9)。

```python
def inline_to_plain(inlines: list[InlineJson]) -> str: ...
    # text→そのまま / math→LaTeX ソース文字列 / citation→"[12]" 形式
    # / ref→表示ラベル("式(5)" 等) / code→コード文字列 / 他は子要素を再帰連結。
    # 連続空白は 1 個に圧縮。

def block_to_plain(block: BlockJson) -> str: ...
    # block.inlines(または type 別の本文フィールド)を inline_to_plain で連結。
    # figure/table はキャプションのみ。equation は LaTeX ソース。

def chat_content_to_plain(content: ChatContentJson) -> str: ...
    # segments[].md を種別問わず順に連結(outside_knowledge / speculation も検索対象)。
    # ⟦A:n⟧ プレースホルダを除去 → strip_markdown()。

def article_block_to_plain(type_: str, content: dict) -> str: ...
    # heading→text / paragraph→strip_markdown(md) / quote_source→text_en
    # / figure_embed→caption_ja / discussion→items[].md 連結
    # / explainer_figure→''(キャプションは explainer_figures 側) / attribution→''

def strip_markdown(md: str) -> str: ...
    # 強調記号 **/*/_/` を除去、リンク [text](url)→text、見出し # 除去、
    # ⟦A:n⟧ 除去、改行→空白、連続空白圧縮。
```

## 10. 性能設計(docs/09 §1: p50 1 秒 / p95 3 秒)

### 10.1 クエリ側の上限(確定値)

| 項目 | 値 |
|---|---|
| 各源 CTE の候補上限 | なし(件数・ファセットは正確値が仕様のため全件集計。ユーザー資産は JOIN で絞られ、想定規模は §10.2) |
| グループ返却 | 1 ページ最大 20 グループ × 5 ヒット(§3.5) |
| スニペット生成 | 返却ヒット分のみ(最大 100 件+相手側チャット 60 字取り)。1 断片 300 バイト |
| プレビュー | 3 件+ count のみ。ファセット計算なし(§6.2) |
| 「論文で絞る」ファセット | 上位 20 論文 |
| レート制限 | 60 回/分/ユーザー(plans/03 §1.8) |

### 10.2 規模見積もりと成立根拠

1 ユーザーの想定上限: ライブラリ 500 本 × 平均 600 ブロック = 原文 30 万行、訳文同数、メモ・注釈・チャット・記事・語彙で +5 万行。PGroonga の転置索引探索はこの規模で数十 ms 級であり、支配項は (a) `my_items` JOIN、(b) ファセット集計、(c) スニペット生成。(a) は `block_search_index` の `uq_block_search_index_rev_block`(revision_id 先頭)と `my_items` のハッシュ結合、(b) は §3.2 の単一 CTE スキャンで 1 パス、(c) は対象行の主キー取得(`uq_*` 一意インデックス)+関数適用のみ。p50 1 秒に対し十分な余裕を持つ。多ユーザーで共有 `block_search_index` のヒットが他ユーザー分を含む点は、PGroonga 側のマッチ後に JOIN で落ちる(マッチ行数が問題になる規模=数百万行到達時は v2 で `tenant` 列パーティションを検討)。

### 10.3 計測・受け入れ検証

- 全検索 API に `search_duration_seconds`(histogram。ラベル: `endpoint=search|preview|in_paper|vocab`)を計測し、Grafana で p50/p95 を常時表示(plans/01 §9.4 の監視構成)。閾値アラート: **p95 > 6 秒(目標 3 秒の 2 倍。plans/01 §9.4 のアラート規則「p95 目標の 2 倍超」に統一)が 15 分継続**。
- シードデータ(Rectified Flow ほか。plans/02 §7)に対する pytest 統合テストで、`EXPLAIN (ANALYZE)` の各源 CTE が **PGroonga インデックススキャンになっていること**(`Seq Scan` が現れないこと)をアサートする。
- ベンチマーク基準(CI ではなく手動 Runbook): 30 万ブロック合成データで `GET /api/search?q=EMA teacher` が warm cache p50 < 500ms。

## 11. ⚠ 基盤への追加・修正要求(集約)

本書の設計確定に伴う、既存基盤計画書への反映事項。**別名は発明していない**(すべて既存識別子の変更・追記)。

- **R-1(plans/02-data-model §4.14)**: 同節の暫定 DDL を本書 §2.2 の 9 本に差し替える(plans/02 自身が「11-search を正とし本節を更新する」と規定)。差分: 全インデックスにトークナイザ/ノーマライザ明示(`NormalizerAuto`→`NormalizerNFKC150`)、`pgroonga_papers_biblio` を `pgroonga_papers_biblio_en` / `pgroonga_papers_biblio_ja` に分割、`pgroonga_annotations_text` の対象列を `(quote, body)`→`(body)` に縮小、`pgroonga_vocab_entries_text` を追加。§2.3 の `pgroonga.enable_wal = on` と §2.4 の `groonga-tokenizer-mecab` を plans/02 §7 のデプロイ前提条件に追記。
- **R-2(plans/03-api §15.1)**: `groups[]` に `hit_count: number` と `article: { article_id; title; generated_at } | null` を追加。`SearchHit.target` の `kind: "viewer"` の `anchor` を `AnchorRef | null` に変更(書誌ヒット= null)。
- **R-3(plans/03-api §15.1)**: スニペットの mark クラス表記 `<mark class="hit">` を `<mark class="alinea-search-hit">` に修正(plans/08 §5 の決定と統一)。
- **R-4(plans/01-architecture §3.4)**: 同節の `search_entries` 非正規化テーブル・CJK 言語判定・`GET /api/v1/search`(offset ページング)の記述を、本書の設計(実体テーブル直接索引 / 判定なし両面検索 / `GET /api/search` + cursor。plans/03 §1.1 の「/api プレフィックス・バージョニングなし」)に合わせて書き換える。250ms デバウンスの記述は本書 §6.3 と一致しており維持。
- **R-5(plans/02-data-model §3.8)**: `SavedFilterConditions` の `collection_ids`/`quality`(配列)/`sort.dir`/`sort.key` の語彙(`reading_seconds`/`understanding`)を、plans/03 §5.14・§5.1 の API 語彙(単数 `collection_id`・単数 `quality`・`sort.order`・`reading_time`/`comprehension`)に統一する(§8.3 の決定)。
- **R-6(plans/08-design-system §2.4/§5)**: `ReadingStatus` TS 型の値 `read_next` が plans/03 §1.6 の API 列挙 `up_next` と不一致。plans/03 を正として `up_next` に統一する(検索グループヘッダの `StatusPill` は `LibraryItemSummary.status` をそのまま受けるため)。

## 12. 受け入れ基準

- [ ] §2.2 の 9 インデックスが `0001_initial` で作成でき、`SELECT pgroonga_command('tokenizer_list')` に `TokenMecab` が含まれる環境でのみ readyz が成功する
- [ ] 英語クエリ「EMA teacher」が原文 `source_text` と訳文「EMA 教師(EMA teacher)」の両方にヒットし、同一ブロックは `matched_in: ["source","translation"]` の 1 件に統合される(S5・4e)
- [ ] 日本語クエリ(例「蒸留」)が訳文・メモ・チャット・記事にヒットし、ヒット源バッジが §4 の表どおりの文言・色トークンで表示される
- [ ] 英語ステミング(クエリ `stabilize` → 本文 `stabilizes`)が原文面で機能する(docs/09 §7.2)
- [ ] `pgroonga_query_escape` により `(` `"` `-` 等を含むクエリが 500 にならずリテラル検索される
- [ ] 検索スニペットの `<mark class="alinea-search-hit">` が琥珀 rgba(196,148,50,0.30) で描画され、HTML インジェクションが不可能(スニペットはエスケープ済み)
- [ ] 4e で「すべて / 本文(原文・訳文)/ メモ・注釈 / チャット履歴 / 記事」ファセットと「論文で絞る」が件数付きで機能し、結果サマリ「「{q}」の結果 {n} 件 · {m} 論文」が正確
- [ ] 源別遷移(§7 の URL 規則)で、本文=該当ブロックへスクロール+フラッシュ、チャット=該当スレッド・メッセージ、メモ=メモパネル、記事=記事モードの該当ブロックに到達する
- [ ] 1e ドロップダウンが 250ms デバウンス・2 文字以上で発火し、上位 3 件+「すべての結果を表示({n} 件)→」を表示、esc で閉じる
- [ ] personal フォークで再翻訳したブロックは、フォーク後の訳文にのみヒットする(shared の旧訳にヒットしない)
- [ ] adopt-revision 前は旧リビジョン、適用後は新リビジョンの本文が検索される(勝手に切り替わらない)
- [ ] 保存フィルタ(§8.3 の JSON)で保存した条件が `filter_id` 適用で再現され、サイドバーに導出件数が表示される
- [ ] 横断検索 p50 1 秒 / p95 3 秒、プレビュー p50 300ms を計測ダッシュボードで確認できる(docs/09 §1)
- [ ] シードデータへの統合テストで全源 CTE が PGroonga インデックススキャンで実行される(Seq Scan なし)
