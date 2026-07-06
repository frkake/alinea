# 07. AI 機能群 実装計画(チャット / 要約 / 記事 / 概要図 / 解説図 / 語彙 / 提案)

> 対象読者と前提
> 本書は「訳読 / YAKUDOKU — 論文読解ワークベンチ」の AI 機能群(読解チャット・要約・記事モード・全体概要図・解説図・語彙 AI 生成・ステータス/読了提案・usage 計測)を実装するエンジニア(apps/api / apps/worker / apps/web)向けの完全実装設計である。機能仕様の正は docs/05(チャット)・docs/07(概要図と記事)・docs/11(語彙帳)・docs/06 §2/§3(提案・読了)・docs/09 §1/§3(性能・コスト)。基盤は既存計画書に従う: エンドポイント・SSE 契約= [plans/03](03-api.md)、DDL・JSONB スキーマ= [plans/02](02-data-model.md)、LLM/画像プロバイダ抽象化層(`packages/llm` = `yakudoku_llm`)= [plans/04](04-llm-providers.md)、ジョブ基盤・ストレージ= [plans/01](01-architecture.md)、デザイントークン= [plans/08](08-design-system.md)。本書は識別子(テーブル・エンドポイント・タスク名)を再発明せず、それらに一致させる。基盤計画書間の記述が食い違う箇所は §12 の「⚠ 基盤への追加要求」に列挙し、本書ではどちらを正としたかを明記する。

## 1. 全体像

### 1.1 機能 × LLM タスク × 実行面の対応表

LLM タスク名は plans/04 §8 の **8 種固定**(`translation` / `retranslation_escalation` / `chat` / `summary` / `article` / `overview_figure_dsl` / `vocab` / `explainer_image`)。本書が実装するのは `chat` / `summary` / `article` / `overview_figure_dsl` / `vocab` / `explainer_image` の 6 タスクを使う機能群である(翻訳系 2 タスクは plans/05 翻訳計画の管轄)。

| 機能 | LLM タスク(plans/04) | 実行面 | 呼び出し形 | ジョブ kind(DB `jobs.kind`、plans/02 §4.13) |
|---|---|---|---|---|
| 読解チャット(送信・再生成・定型アクション) | `chat` | apps/api(SSE 中継。例外的に API 内で LLM ストリーミング) | `LLMRouter.run_stream()` | ジョブ化しない |
| まとめてメモ化(スレッド要約) | `summary` | apps/api(同期実行・全体タイムアウト 15 秒) | `run(mode="structured")` | ジョブ化しない(本書決定: `jobs.kind` の値域に該当 kind が無く、入力 ≤12k トークン・出力 ≤2k で p95 15 秒に収まるため API 同期実行とする。plans/03 §10.5 は API 同期実行(201 `{ note: Note }`)へ修正済み — §12-⚠7) |
| 取り込み時 ✦3行要約 | `summary` | apps/worker(bulk。ingest ジョブの `translating_abstract` 段内) | `run(mode="structured")` | `ingest`(既存パイプライン内) |
| 詳細要約(チャット定型) | `chat` | apps/api | チャットと同一 | ジョブ化しない |
| 記事生成・指示つき再生成 | `article`(+`overview_figure_dsl`+`explainer_image`) | apps/worker(interactive) | `run(mode="structured")` | `article` |
| 記事ブロック書き直し・再生成 | `article` | apps/worker(interactive) | `run(mode="structured")` | `article`(`payload.op='block_rewrite'`) |
| 全体概要図(DSL 生成→SVG レンダリング) | `overview_figure_dsl` | apps/worker(interactive) | `run(mode="structured")` | `figure`(`payload.figure_kind='overview'`) |
| 解説図(ラスター) | `explainer_image` | apps/worker(interactive) | `ImageRouter.run()` | `figure`(`payload.figure_kind='explainer'`) |
| 語彙 AI 生成・再生成 | `vocab` | apps/worker(interactive) | `run(mode="structured")` | `vocab` |
| ステータス提案・読了提案 | LLM 不使用 | apps/api(同期判定) | — | ジョブ化しない |

- API が返す `Job.kind`(plans/03 §1.7: `article_generate` / `article_block_rewrite` / `overview_figure` / `explainer_figure` / `vocab_generate` 等)は、DB の `jobs.kind` + `payload` からの**導出値**とする。対応: `article`+`op='generate'`→`article_generate` / `article`+`op='block_rewrite'`→`article_block_rewrite` / `figure`+`figure_kind='overview'`→`overview_figure` / `figure`+`figure_kind='explainer'`→`explainer_figure` / `vocab`→`vocab_generate`。
- ジョブ stage は plans/02 §4.13 の値域を正とする: `article`: `queued → collecting_sources → generating → rendering → complete`、`figure`(overview): `queued → generating_dsl → rendering_svg → complete`、`figure`(explainer): `queued → generating_image → complete`、`vocab`: `queued → generating → complete`(plans/01 §4 の別名 stage は §12-⚠4 で統一を要求)。
- 既定モデルチェーン(plans/04 routing.yaml を正、再掲): chat=`[claude-opus-4-8, gpt-5.5, gemini-3.5-flash]` effort=medium / summary=同チェーン effort=low / article=`[claude-opus-4-8, gpt-5.5]` effort=high / overview_figure_dsl=同 effort=high / vocab=`[claude-haiku-4-5, gpt-5.4-mini, gemini-3.5-flash]` effort=none / explainer_image=`[gemini-3.1-flash-image, grok-imagine-image, gpt-image-2]`。

### 1.2 コード配置(確定)

```
apps/api/app/services/chat/
  context_builder.py      # §2.2 文脈ビルダー
  stream_pipeline.py      # §2.4 モデル出力→SSE→DB 変換
  evidence.py             # §2.5 根拠マーカー検証・display 導出
  quick_actions.py        # §2.7 定型アクションのプロンプトテンプレート
  prompts.py              # §2.6 システムプロンプト定数
apps/api/app/services/suggestions.py   # §8 ステータス提案・読了提案(同期判定)
apps/api/app/services/quota.py         # §9 クォータ事前チェック
apps/worker/yakudoku_worker/tasks/
  generate_article.py         # §4(kind='article')
  generate_overview_figure.py # §5(kind='figure', overview)
  generate_explainer_figure.py# §6(kind='figure', explainer)
  generate_vocab.py           # §7(kind='vocab')
apps/worker/yakudoku_worker/prompts/
  summary.py / article.py / overview.py / explainer.py / vocab.py  # プロンプト定数(§3〜§7)
packages/figures/            # 配布名 yakudoku-figures / import 名 yakudoku_figures
  src/yakudoku_figures/overview_svg.py   # §5.4 SVG 決定的レンダラ(純関数・依存なし)
  src/yakudoku_figures/wrap.py           # §5.4.3 決定的折返し
  tests/test_overview_svg_golden.py      # ゴールデン(バイト同一)テスト
```

- 決定: SVG レンダラは Python 共有パッケージ `packages/figures` に置く(plans/02 §3.9「SVG レンダラ(packages 側)」に一致)。使用者は apps/worker のみだが、apps/api のテスト・将来のプレビュー生成から import できるよう worker 内に閉じない。`packages/llm` と同じ uv workspace パス依存。
- プロンプト定数はすべて Python モジュール内の文字列定数(i18n しない・DB に置かない)。プロンプト変更=デプロイ。ただしモデル ID・チェーンは DB(plans/04 §15)。

## 2. 読解チャット

### 2.1 データフロー全体

```
POST /api/chat/threads/{thread_id}/messages(plans/03 §10.3)
  → クォータ事前チェック(§9)→ user メッセージ INSERT(status='complete')
  → assistant メッセージ INSERT(status='streaming')
  → context_builder.build()(§2.2)
  → LLMRouter.run_stream("chat", build, user_id=...)(plans/04 §9)
  → stream_pipeline(§2.4): モデル生テキスト → 検証済み SSE イベント(start/delta/evidence/done/error)
  → 完了時: assistant メッセージ確定 UPDATE(content=ChatContentJson, evidence_anchors, status='complete',
             provider/model)+ text_plain 導出 + MeterHook 記録(plans/04 §10)
```

- SSE イベント契約は **plans/03 §10.3 が正**(`start` / `delta` / `evidence` / `done` / `error` の 5 種。plans/01 §3.3 の `message.delta` 系イベント名は使わない — §12-⚠6)。
- 切断時の回復: `done` 前に切れたら worker 側処理は無い(API 内ストリーム)ため、LLM ストリームは最後まで消費してメッセージを確定保存する(`asyncio.shield` でレスポンス切断と分離)。クライアントは再接続せず `GET /api/chat/threads/{id}/messages` で確定結果を取る(plans/03 §1.9)。
- ストリーミング開始後のプロバイダ障害はフォールバックしない(plans/04 §9.1 規則5)。`event: error` 送出+メッセージを `status='error'`+`error` 付きで保存する(P3: 失敗回答も履歴に残す)。

### 2.2 文脈ビルダー(context_builder.py)

docs/05 §3 の規定「構造化ドキュメント全体(原文)。超過時は全セクション要約+関連セクション全文+選択周辺全文」を次のとおり確定する。**訳文は文脈に入れない(原文を正とする)**。

#### 2.2.1 トークン予算(確定値)

| 要素 | 配置 | 予算(トークン) | 備考 |
|---|---|---|---|
| system[0] 静的プリアンブル(§2.6) | system、`cache_hint=True` | 実測 ≤3,000(固定文字列) | リリース単位で不変 → プロンプトキャッシュ第1境界(plans/04 §13) |
| system[1] 論文文脈 | system、`cache_hint=True` | **全文モード ≤60,000 / 圧縮モード ≤38,000** | リビジョン単位で不変 → キャッシュ第2境界 |
| system[2] 注釈・メモ | system(キャッシュ境界なし) | ≤4,000 | 設定 `chat.include_annotations_and_notes=true`(既定)のときのみ |
| 会話履歴(messages) | messages | ≤12,000 | 新しい方から採用。超過分は古い順に丸ごと落とす(要約しない) |
| 今回のユーザーメッセージ+選択周辺全文 | messages 末尾 | ≤6,000 | 選択周辺 = アンカーブロック±2ブロックの原文全文 |
| 画像(図の説明時) | messages 末尾の image パート | 1枚=1,600 トークン換算・最大2枚 | plans/04 §14 の見積り値 |
| 出力 `max_output_tokens` | — | 8,192 | routing.yaml `chat` の値 |

- 判定は `LLMProvider.count_tokens()`(plans/04 §14)ではなく、コスト削減のため **ローカル見積り**(tiktoken `o200k_base`、`packages/llm` の `estimate_tokens_o200k`)で行う。見積り誤差は安全帯 2,048(plans/04 §14)で吸収する。
- **全文モード**: 論文コンテキスト(§2.2.2 の形式)全体の見積りが 60,000 トークン以下なら全文を入れる(標準的な論文は 15k〜30k で収まる)。
- **圧縮モード**(60,000 超のとき)の配分: 全セクション要約 ≤8,000 / 関連セクション全文 3 本 ≤24,000(1 本 8,000 で切詰め)/ 選択アンカーのあるセクション全文(関連 3 本に含まれなければ追加)≤6,000。

#### 2.2.2 論文コンテキストの形式(モデルに渡す本文表現)

`document_revisions.content` から次の平文形式に展開する(展開関数は `context_builder.render_blocks()`。**この形式が根拠マーカーの語彙になる**):

```
# 論文コンテキスト(revision rev_01JZ…)
## [sec-2-2|§2.2] Reflow: Straightening the paths
[blk-3-p2-a1f9|§2.2 ¶1] The X_t in rectified flow is a linear interpolation of ...
[blk-3-eq5-77c2|式(5)] $$\min_v \int_0^1 \mathbb{E}\|(X_1-X_0)-v(X_t,t)\|^2 dt$$
[blk-3-fig2-90aa|図2] (figure) Caption: The paths of rectified flow ...
```

- 各ブロックの行頭に `[block_id|表示位置]` を付ける。表示位置は `block_search_index` の `section_label` / `paragraph_ordinal` / `element_label` から §2.5.2 の規則で導出した文字列。
- `equation` は LaTeX を `$$...$$` で、`figure` / `table` はキャプション原文を、`code` は先頭 20 行を入れる。`reference_entry` は文脈に含めない(トークン節約。引用議論はセクション本文の citation 表記で足りる)。
- 圧縮モードのセクション要約は `## [sec-2-2|§2.2] Reflow: ...\n(要約) このセクションでは…` 形式(要約は日本語 ≤400 トークン/セクション)。

#### 2.2.3 セクション要約のキャッシュ(圧縮モード用)

- 生成: `summary` タスクで一括生成する(structured 出力 `{"summaries": [{"section_id": "...", "summary": "..."}]}`。入力はセクション本文を 30,000 トークンずつのチャンクに分けて複数回呼ぶ)。生成タイミング(決定): 圧縮モードに該当する論文への**最初のチャット送信時に API 内で同期生成**する(そのメッセージのみ初回トークンが遅れることを許容。事前生成ジョブは作らない)。
- 保存: **Redis** キー `chatctx:secsum:{revision_id}`(JSON、TTL 30 日)。リビジョンは不変なのでユーザー間で共有できる。Redis 消失時は次回チャットで再生成(派生データ。DB スキーマは増やさない — 決定)。
- 計測: 生成コストは要約生成をトリガーしたユーザーの `usage_records`(task='summary'、job_id なし)に記録(plans/04 §10.1 の共有コスト帰属規則と同じ)。`chat_messages` クォータも LLM 呼び出し 1 回につき 1 消費(§9.2)。

#### 2.2.4 関連セクションの選定(圧縮モード)

1. 質問文(`content` + 選択アンカーの `quote`)を PGroonga で `block_search_index.source_text` に対して検索(`WHERE revision_id = :rev` スコープ、`pgroonga_score` 使用)。
2. `section_path` の第1階層ごとにスコアを合算し、上位 3 セクションを「関連セクション全文」とする。
3. `context_anchors` が指すセクションは無条件で全文に含める(上位 3 に入っていなければ 4 本目として追加)。
4. 日本語クエリで原文ヒットが 0 件の場合は `translation_units.text_ja`(表示解決済みセット)を検索し、ヒットブロックの `block_id` からセクションへ逆引きする(文脈に入れるのは常に原文)。

#### 2.2.5 注釈・メモの文脈表現(system[2])

```
# ユーザーの注釈・メモ(参考。回答の根拠は本文のみ)
- ハイライト(疑問) [blk-3-p2-a1f9|§2.2 ¶1] "causalizes the paths of linear interpolation"
- コメント [blk-4-p1-...|§3.1 ¶1] "batch size の記載が見当たらない"
- メモ: (タイトル) 本文の冒頭 500 文字…
```

新しい順に採用し 4,000 トークンで打ち切る。設定 `chat.include_annotations_and_notes=false` なら system[2] 自体を省く。

#### 2.2.6 会話履歴

- アクティブスレッドの `chat_messages` を新しい順に走査し、`text_plain`(assistant は根拠チップを「(§2.1 ¶4)」表記に展開済みの平文)を 12,000 トークンに収まる分だけ古い方向へ採用。採用分を時系列順に `messages` に並べる。
- `status='error'` のメッセージは履歴に含めない。aside(論文外の知識/推測)は `【論文外の知識】…` のラベル付き平文として含める。

### 2.3 モデル出力形式(確定)

**決定: 根拠チップのインライン記法は `[[evidence:block_id]]`**(モデル出力面)。「論文外の知識」「推測」は**タグ付きブロック**で出力させる。

| 要素 | モデル出力書式 | 例 |
|---|---|---|
| 根拠マーカー | `[[evidence:blk-3-eq5-77c2]]`(文中、主張の直後) | `…最小二乗回帰です [[evidence:blk-3-eq5-77c2]] [[evidence:blk-3-p2-a1f9]]。` |
| セクション粒度の根拠 | `[[evidence:sec-2-2]]`(該当段落を特定できない場合のみ) | |
| 論文外の知識 | `<outside_knowledge>` 〜 `</outside_knowledge>`(行単位の独立ブロック) | |
| 推測 | `<speculation>` 〜 `</speculation>`(同上) | |
| 数式 | `$...$` / `$$...$$`(KaTeX 互換) | |
| 表 | Markdown テーブル(「実験設定の整理」で使用) | |

- 変換の3層(全レイヤの対応を固定する):
  1. **モデル層**: `[[evidence:ID]]` + タグ
  2. **API/SSE 層**(plans/03 §10.3): `[[ev:n]]` トークン + `evidence` イベント(`ref: n`)+ `MessageBlock.type: "aside"`
  3. **DB 層**(plans/02 §3.5 ChatContentJson): `⟦A:n⟧` プレースホルダ + `segments[].type: outside_knowledge | speculation`、`evidence_anchors[n]` に AnchorJson
  - API の GET(plans/03 §10.2)は DB の `⟦A:n⟧` を `[[ev:n]]` に置換して返す(1 対 1 変換。`n` は共通)。

### 2.4 ストリーム変換パイプライン(stream_pipeline.py)

モデルの `text_delta` 列を SSE イベント列に変換する状態機械。**保留バッファ方式**: マーカー/タグが delta 境界で分断されても誤送出しないよう、末尾最大 48 文字([[evidence:…]] の最長 40 文字+余裕)を保留してから確定分のみ送出する。

```python
MARKER_RE = re.compile(r"\[\[evidence:((?:blk|sec)-[A-Za-z0-9-]+)\]\]")
OPEN_TAGS = {"<outside_knowledge>": "outside_knowledge", "<speculation>": "speculation"}
CLOSE_TAGS = {"</outside_knowledge>", "</speculation>"}
HOLDBACK = 48  # 文字

class StreamPipeline:
    """モデル生テキスト → plans/03 §10.3 SSE イベント + ChatContentJson 蓄積。"""
    def __init__(self, validator: EvidenceValidator):
        self.block_index = 0          # 現在の MessageBlock index
        self.block_type = "markdown"  # markdown | aside
        self.aside_label = None
        self.evidence: list[AnchorRef] = []   # ref は 1 起点
        self.segments: list[Segment] = []     # DB 保存用(⟦A:n⟧ 形)
        self.buf = ""

    async def feed(self, delta: str) -> AsyncIterator[SseEvent]:
        self.buf += delta
        emit, self.buf = self._split_safe(self.buf)   # 保留 48 文字を残して確定分を切り出す
        for token in self._tokenize(emit):            # text / marker / open_tag / close_tag
            match token:
                case Text(t):
                    yield SseDelta(self.block_index, self.block_type, self.aside_label, t)
                case Marker(block_id):
                    anchor = self.validator.resolve(block_id)     # §2.5
                    if anchor is None:
                        continue                                   # 実在しない参照はトークンごと除去
                    ref = self._ref_for(anchor)                    # 同一アンカーは同じ ref を再利用
                    yield SseDelta(self.block_index, self.block_type, None, f"[[ev:{ref}]]")
                    if ref == len(self.evidence):                  # 初出のみ evidence イベント
                        yield SseEvidence(ref, anchor.display, anchor)
                case OpenTag(label):
                    self._close_block(); self.block_type, self.aside_label = "aside", label
                case CloseTag():
                    self._close_block(); self.block_type, self.aside_label = "markdown", None
```

規則(確定):

1. `delta` イベントは `block_index` 昇順・同一 index 内で text 連結(plans/03 §10.3 と同一)。aside ブロックの初回 delta にのみ `label` を含める。
2. `evidence` イベントは該当 `[[ev:n]]` を含む delta の**直後**に、同一 `n` につき 1 回だけ送る。
3. タグの入れ子・不整合(`</speculation>` が来たが aside 中でない等)は**タグを無視して本文として扱わない**(黙って捨てる。ログ warn)。ストリーム終端で aside が閉じていなければ自動で閉じる。
4. 空ブロック(open→close 間にテキスト無し)は SSE にも DB にも残さない。
5. `done` イベントの `finish_reason` は `LLMResponse.stop_reason`(plans/04 §3 の `end | max_tokens | stop_sequence | content_filter`)から写像する: `end` → `stop`、それ以外の 3 値はそのまま。`max_tokens` 打ち切り時も回答は成立として保存する(UI 側で示す追加装飾はしない — デザインに存在しないため)。
6. DB 確定時、`text_plain` は segments を結合し `⟦A:n⟧` を `(表示位置)` 表記(例 `(§2.1 ¶4)`)へ展開した平文とする(横断検索・履歴文脈用)。

### 2.5 根拠検証と表示表記の導出(evidence.py)

#### 2.5.1 実在検証

- チャット開始時に `block_search_index` から `(block_id, block_type, section_label, paragraph_ordinal, element_label, section_path)` を **リビジョン単位で一括ロード**し(1 論文 ≤800 行、Redis キャッシュ `bsi:{revision_id}` TTL 1 時間)、`EvidenceValidator.resolve()` は辞書引きのみで判定する(SSE 中に DB を叩かない)。
- `blk-…` は完全一致で検証。`sec-…` は同リビジョンのセクション ID 集合で検証し、アンカーはセクション見出しブロックへの参照(`start`/`end`/`quote` = null)として作る。
- 実在しない ID は `[[evidence:…]]` トークンごと除去する(docs/05 §5)。除去が発生した件数は構造化ログ(`event: "evidence_dropped"`)に記録する。
- 1 メッセージの根拠上限 **24 個**。超過分は除去(異常出力対策。プロンプト §2.6 はモデルに 20 個までと指示し、検証側は +4 の余裕を持つ — 意図的な差)。

#### 2.5.2 display 導出(全機能共通の正)

`AnchorRef.display`(plans/03 §1.7)は保存せず、`block_search_index` から決定的に導出する(plans/02 §3.1):

| block_type | display | 例 |
|---|---|---|
| `equation`(element_label あり) | `element_label` | `式(5)` |
| `figure` / `table` | `element_label` | `図2` / `表1` |
| `paragraph` / `list` / `quote` / `theorem` / `algorithm` / `footnote`(paragraph_ordinal あり) | `{section_label} ¶{paragraph_ordinal}` | `§2.1 ¶4` |
| `heading` / セクション参照 | `section_label` | `§3.2` |
| ユーザー選択質問のコンテキストチップ(element_label があるブロック) | `{element_label} · {section_label}` | `式(5) · §2.1`(1a 逐語) |

### 2.6 チャットシステムプロンプト(完全形)

`apps/api/app/services/chat/prompts.py` の `CHAT_SYSTEM_PREAMBLE`(system[0]、逐語):

```
あなたは論文読解ワークベンチ「訳読」の読解アシスタントです。ユーザーは「論文コンテキスト」に与えられた 1 本の論文を日本語で深く読解しています。あなたの役割は答えを述べることではなく、原文のどこを読めばよいかまで案内することです。

## 回答の原則
1. 回答は必ず論文コンテキストの原文を根拠にする。原文に書かれていないことを本文として断定しない。
2. 本文に根拠がある主張には、主張の直後に根拠マーカーを付ける。書式は [[evidence:ブロックID]]。ブロックIDは論文コンテキストの各行頭に [ブロックID|位置] の形で示されている。
   - 可能な限り段落・数式・図表の粒度で特定する。段落を特定できない場合のみセクションID([[evidence:sec-2-2]] の形)を使う。
   - 1 つの主張につきマーカーは最大 3 個。回答全体で最大 20 個。
3. 論文本文に由来しない一般知識・実装慣行・周辺文献の内容を補う場合は、その部分だけを独立した段落として <outside_knowledge> と </outside_knowledge> で囲む。
4. 本文から断定できない推論・仮説を述べる場合は、その部分だけを独立した段落として <speculation> と </speculation> で囲む。
5. 論文に記載のない事実を問われたら「この論文には記載がありません」と明示する。推測で埋めない(仮説を述べる場合は 4 に従う)。
6. 回答は日本語。文体は常体(だ・である)ではなく、です・ます調。数式は $...$ または $$...$$(KaTeX 互換)。表が適切な場合は Markdown テーブルを使う。長い回答には ### 見出しを使ってよい。
7. 出力に上記以外の独自マーカー・脚注記法・URL・免責文を含めない(免責は UI が表示する)。

## 論文メタデータ
タイトル: {title}
著者: {authors_short} / 発表: {venue_year} / arXiv: {arxiv_id}
```

- system[1] = `# 論文コンテキスト…`(§2.2.2)、system[2] = 注釈・メモ(§2.2.5)。system[0] と system[1] に `cache_hint=True`(plans/04 §13)。
- `LLMRequest.prompt_cache_key = f"chat:{revision_id}"`(OpenAI 用)。
- effort=medium / max_output_tokens=8192 / timeout_s=120(routing.yaml)。temperature 系は型レベルで存在しない(plans/04 §3)。

### 2.7 定型アクション(QuickAction)のプロンプト完全形

UI 配置(docs/05 §7): 常設チップ 5 種=入力欄上のチップ行。入力候補 2 種=入力ボックスのフォーカス時・入力途中に候補ポップアップ(上 2 件固定)として表示し、選択で送信。導線アクション 3 種(`detailed_summary` / `explain_equation` / `explain_figure`)は発生箇所(3行要約カード・数式ブロック・図ポップオーバー)から送信される。いずれも `POST …/messages` の `quick_action` に列挙値(plans/03 §10.2 の `QuickAction`)を入れ、`content` は空文字で送る。サーバーは以下のテンプレートを **user メッセージ本文として**展開する(表示上もこのテキストが白カードに出る)。

| quick_action | user メッセージ本文(逐語テンプレート) |
|---|---|
| `summary_3line` | `この論文を次の 3 行で要約してください。①課題(何が問題か) ②手法(どう解いたか) ③結果(何がどれだけ良くなったか)。各行は 80 文字以内で、行ごとに根拠マーカーを付けてください。` |
| `beginner_explain` | `この論文を、この分野の前提知識がない読者に向けて解説してください。必要な前提概念(既存手法・用語)を先に短く補ってから、提案手法のアイデアを比喩や具体例を交えて説明してください。前提知識の補いは <outside_knowledge> ブロックに分離してください。` |
| `contributions_limits` | `この論文について次の 3 点を整理してください。### 主張されている貢献(箇条書き・各項目に根拠マーカー) ### 明示されている限界(論文自身が認めている制約) ### 暗黙の限界(本文の実験設定・仮定から読み取れるが明示されていない制約 — こちらは <speculation> ブロックで)。` |
| `experiment_setup` | `この論文の実験設定を Markdown 表で整理してください。列: 実験 / データセット / ベースライン / 評価指標 / 主要ハイパーパラメータ。表の下に、本文に記載が見つからなかった項目を「記載なし」として列挙してください。各行に根拠マーカーを付けてください。` |
| `implementation_points` | `この論文を再実装するために必要な情報を抽出してください。### 構成要素(モデル・入出力) ### 学習手順(損失・最適化・スケジュール) ### 擬似コード(Python 風、コードブロックで) ### 本文から読み取れない実装判断(<speculation> ブロックで代替案を提示)。根拠マーカーを付けてください。` |
| `expert_summary`(入力候補) | `この分野の研究者向けに、この論文の技術的要点を 300 文字程度で要約してください。新規性が既存手法のどこを変えた点にあるかを中心に。根拠マーカーを付けてください。` |
| `related_work_position`(入力候補) | `この論文が関連研究の中でどこに位置づくかを整理してください。本文の関連研究セクションで言及されている系譜(根拠マーカー付き)と、本文外の一般知識による補足(<outside_knowledge> ブロック)を分けて説明してください。` |
| `detailed_summary`(導線: 1b 3行要約カード「詳細要約 →」・1g「✦ 要約をメモに保存」) | `この論文の詳細要約を作成してください。セクション構成に沿って、### 見出し(§番号付き)ごとに 2〜4 文で要約し、各見出しの要約に根拠マーカーを付けてください。全体で 600〜1,200 文字。最後に「結論と限界」を 2 文で。` |
| `explain_equation`(導線: 数式ブロック「✦ この式を説明」) | `この式が何を意味するか、各記号の意味と式全体が最小化/表現しているものを直感的に説明してください。`(+`context_anchors[0]`=数式ブロック全体参照) |
| `explain_figure`(導線: 図ポップオーバー「✦ この図を説明」) | `この図が何を示しているか、軸・凡例・読み取るべきポイントを説明してください。`(+`context_anchors[0]`=図ブロック全体参照。図画像を `ContentPart.from_image_bytes()` で messages に添付 — docs/05 §4「図は画像としてモデルに渡す」。画像は S3 `figures/{paper_id}/{revision_id}/{block_id}.png` から取得) |

#### 2.7.1 要約系の再利用(「同一内容を再生成しない」— docs/05 §7)

- 対象: `summary_3line` と `detailed_summary` のみ(他アクションは毎回生成)。
- 規則: `POST …/messages` で該当 quick_action を受けたとき、**同一スレッド内**に「同じ quick_action の user メッセージへの `status='complete'` の assistant 回答」が存在し、かつその回答の `evidence_anchors[].revision_id` がすべて現行リビジョンと一致する場合(アンカーが 0 件の回答はリビジョン照合なしでリプレイ対象とする — 決定)、LLM を呼ばず保存済み回答を SSE で**リプレイ**する(`start` → 保存済み各ブロックを 1 delta ずつ → `evidence` → `done`)。クォータ・usage は消費しない。
- 明示的な「再生成」(`POST /api/chat/messages/{id}/regenerate`)はこのキャッシュを迂回して必ず生成する。
- 取り込み時の ✦3行要約カード(`papers.summary_lines`)とチャットの「3行要約」チップは**別の生成物**(前者は Paper 共有資産・根拠チップなし、後者は個人チャット・根拠チップ付き)。相互に再利用しない(決定。表示面・粒度・所有が異なるため)。

### 2.8 再生成・メモ昇格・まとめてメモ化

- **再生成**(plans/03 §10.4): 旧回答は残し、新しい user(content 編集時)+ assistant メッセージを追記する。文脈ビルダーは再生成対象より**前**の履歴のみを使う(直前の失敗回答を文脈に入れない)。
- **↑ メモに保存**: web が `POST /api/library-items/{id}/notes { source_message_id }` を呼ぶ(plans/03 §9)。サーバーは `content_md` を次の変換で複写する: markdown セグメントの `⟦A:n⟧` → `(§2.1 ¶4)` 表記+アンカーを `notes.anchors` に複写 / aside セグメント → `> **論文外の知識**: …` / `> **推測**: …` の blockquote。「コピー」ボタンの Markdown 変換もこの規則と同一(web 側実装、`packages/api-client` にユーティリティ `chatMessageToMarkdown()` を同梱)。
- **まとめてメモ化**(docs/05 §8): `POST /api/chat/threads/{thread_id}/summarize-to-note`。API 内同期実行(§1.1 の決定): スレッド全 `text_plain`(≤12,000 トークン、超過時は古い方を落とす)を `summary` タスクに渡し、structured 出力 `{"title": string(≤40字), "body_md": string}` を得て `notes` に INSERT(`anchors` = スレッド内 assistant メッセージの evidence_anchors の和集合・重複排除・最大 30 件)。成功時は 201 `{ note: Note }`。失敗時(チェーン全滅・API 側全体タイムアウト 15 秒超過)は 502 `provider_error`(RFC7807)を返し、`notes` には何も作らない(部分結果を保存しない — 決定)。プロンプト(user、逐語): `以下のチャットスレッドを、後から読み返せる 1 つのメモに整理してください。結論が出た論点 → 未解決の疑問 の順に、Markdown の箇条書き中心で。原文の位置表記((§2.1 ¶4) 形式)は本文中に残してください。`

## 3. 3行要約・詳細要約

### 3.1 ✦3行要約(取り込み時自動・Paper 共有資産)

- **タイミング**: ingest パイプラインの `translating_abstract` 段(docs/02 §5.1)。アブスト訳と並行して 1 回だけ生成する。**保存先: `papers.summary_lines`(JSONB、`["…","…","…"]`)**(plans/02 §4.3)。Paper 共有資産のため public 論文では最初の取り込みユーザーの 1 回のみ生成し、以後の取り込みでは再生成しない。
- タスク: `summary`(chain=[claude-opus-4-8, …] / effort=low / structured / max_output_tokens=2048)。**3行要約+提案タグは ingest 時に 1 呼び出しで生成する(plans/05 §11.1 と同一契約)**。`suggested_tags` のマージ(arXiv カテゴリ ∪ 共起タグ ∪ LLM 提案)と `library_items.suggested_tags` への保存は plans/05 §11.1 の管轄。
- 入力: タイトル+アブストラクト原文+イントロダクション先頭 2,000 トークン+結論セクション全文(合計 ≤8,000 トークン)。イントロダクション=本文最初のトップレベルセクション、結論=見出しが `conclusion` / `discussion` / `summary` に前方一致(大文字小文字無視)する最後のトップレベルセクション(見つからなければ本文最終セクション)— 決定。
- JSON Schema(`name: "summary_3line_v1"`):

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["summary_lines", "suggested_tags"],
  "properties": {
    "summary_lines": {
      "type": "array", "minItems": 3, "maxItems": 3,
      "items": { "type": "string", "maxLength": 60 }
    },
    "suggested_tags": {
      "type": "array", "maxItems": 5,
      "items": { "type": "string", "maxLength": 30 }
    }
  }
}
```

- プロンプト(system、逐語):

```
あなたは学術論文の要約者です。与えられた論文を次の 3 行で要約してください。
1 行目: 課題(この論文が解こうとしている問題)
2 行目: 手法(提案アプローチの核心)
3 行目: 結果(主要な成果。数値は本文にあるものだけを使う)
各行は日本語 60 文字以内。行頭に番号や記号を付けない(表示側が ① ② ③ を付ける)。本文にない数値・主張を作らない。
あわせて、この論文の主題を表す提案タグを suggested_tags に最大 5 件挙げてください(英語小文字の短い名詞。例: distillation, solver)。
```

- 検証: 各行の数値トークン(`[0-9][0-9.,×^%]*`)が入力素材の正規化テキストに部分一致で存在することを確認。不一致があれば修正指示付きで 1 回だけ再生成し、なお不一致なら**数値を含む行はそのまま採用せず数値部を除去した行に修正はしない — 生成失敗として `summary_lines = NULL`** のまま進める(カードは要約なしで表示。P3: 誤数値を出すより出さない)。失敗は ingest ジョブの処理ログに `{"event":"summary_failed","reason":"number_mismatch"}` を記録。パイプラインは失敗させない(部分成功)。

### 3.2 詳細要約

- 独立ジョブを持たない。**チャットの `detailed_summary` 定型アクション**(§2.7)として生成し、`chat_messages` に保存される。再利用規則は §2.7.1。
- 導線: (a) 訳文モード 1b の 3行要約カード「詳細要約 →」→ チャットタブを開きメインスレッドへ `quick_action=detailed_summary` を送信。(b) 読了フロー 1g「✦ 要約をメモに保存」→ 同送信(既存回答があればリプレイ)→ 完了後に自動で `POST …/notes { source_message_id }`(§2.8)を実行し「メモに保存しました」トーストを出す。

## 4. 記事生成(記事モード 1h)

### 4.1 生成トリガーとジョブ

- 初回生成: `POST /api/library-items/{id}/article { preset, include_math? }`(plans/03 §19.2)→ `jobs(kind='article', payload={"op":"generate","preset":…,"include_math":…})`。初回トリガーの UI は (a) 記事モードを開いたときの生成 CTA、(b) 読了フロー 1g「記事モードで読み返す →」(docs/07 §2.7)。
- 指示つき再生成: `POST /api/articles/{article_id}/regenerate { instruction?, preset?, include_math? }` → 同 kind、`payload.op='regenerate'`。
- `include_math` の既定(プリセット属性、確定): `beginner=false` / `implementer=true` / `researcher=true` / `reading_group=false`(plans/03 §19.2 と一致。implementer が true なのは、U2 の目的が損失関数・学習手順の正確な再現であるため)。
- 性能目標: 初回生成 p50 30 秒 / p95 90 秒(docs/09 §1)。stage は `queued → collecting_sources → generating → rendering → complete`。

### 4.2 入力素材の収集(stage=collecting_sources)

素材は docs/07 §2.2 の「訳文・メモ・チャット履歴」+疑問ハイライト(「議論したい点」の由来)。収集関数 `collect_article_sources(library_item_id)` の確定仕様:

| 素材 | 取得元 | 形式・上限 |
|---|---|---|
| 書誌+ライセンス | `papers` | タイトル/著者/venue/年/arXiv ID/`license` と図表転載可否(docs/09 §5.2 マトリクス判定結果) |
| ✦3行要約 | `papers.summary_lines` | そのまま |
| 訳文本文 | plans/02 §5.2 の表示解決クエリ(personal 優先)で全セクション | `## [sec-…|§n] 見出し` + 段落ごと `[blk-…|§n ¶m] 訳文` の形式(§2.2.2 と同形・ただし**訳文**)。**≤50,000 トークン**。超過時は §2.2.3 のセクション要約(原文由来)で後方セクションから置換 |
| 数式 | `document_revisions.content` | `include_math=true` のときのみ、番号付き数式の LaTeX を該当位置に挿入 |
| 図表リスト | `GET figures` 相当の内部クエリ | `[blk-…|図2] キャプション訳 … (転載: 可/不可)` の一覧 |
| メモ | `notes` | `- (note_id) タイトル: 本文先頭 1,000 文字` 新しい順 ≤6,000 トークン |
| 注釈 | `annotations`(kind=highlight/comment) | `- (ann_01…)[色ラベル] [blk-…|§n ¶m] "引用" (コメント: …)` ≤4,000 トークン。**color='question'(疑問)の行に `★疑問` を付ける** |
| チャット履歴 | 全スレッドの `chat_messages.text_plain` | `[スレッド: メイン] あなた: … / アシスタント: …` 新しい順 ≤10,000 トークン |
| 未翻訳セクション | `translation_units` に無い block | 原文で補う(訳文が無い部分を捨てない) |

### 4.3 記事構造 JSON スキーマ(モデル出力、完全形)

`name: "article_v1"`(draft 2020-12)。モデルは `attribution`(出典)ブロックを出力**しない**(サーバーが自動挿入・削除不可 — docs/07 §2.3)。全体概要図もブロックに含めない(`articles` とは別系統の `overview_figures`、§5)。

```json
{
  "$id": "https://yakudoku.app/schemas/article_v1.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["title", "blocks"],
  "properties": {
    "title": { "type": "string", "maxLength": 60 },
    "blocks": {
      "type": "array", "minItems": 8, "maxItems": 60,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["type"],
        "properties": {
          "type": { "enum": ["heading", "paragraph", "quote_source",
                              "figure_embed", "explainer_figure", "discussion"] },
          "heading":  { "type": "object", "additionalProperties": false,
                        "required": ["level", "text"],
                        "properties": { "level": { "enum": [2, 3] },
                                        "text": { "type": "string", "maxLength": 60 } } },
          "markdown": { "type": "string", "maxLength": 4000 },
          "quote":    { "type": "object", "additionalProperties": false,
                        "required": ["block_id", "text_en"],
                        "properties": { "block_id": { "type": "string", "pattern": "^blk-" },
                                        "text_en": { "type": "string", "maxLength": 400 } } },
          "figure":   { "type": "object", "additionalProperties": false,
                        "required": ["block_id", "caption_ja"],
                        "properties": { "block_id": { "type": "string", "pattern": "^blk-" },
                                        "caption_ja": { "type": "string", "maxLength": 300 } } },
          "explainer": { "type": "object", "additionalProperties": false,
                        "required": ["slot", "image_brief_en", "caption_ja"],
                        "properties": { "slot": { "enum": [0, 1] },
                                        "image_brief_en": { "type": "string", "maxLength": 500 },
                                        "caption_ja": { "type": "string", "maxLength": 300 } } },
          "discussion": { "type": "object", "additionalProperties": false,
                        "required": ["items"],
                        "properties": { "items": {
                          "type": "array", "minItems": 2, "maxItems": 6,
                          "items": { "type": "object", "additionalProperties": false,
                            "required": ["text", "origin"],
                            "properties": {
                              "text": { "type": "string", "maxLength": 200 },
                              "origin": { "enum": ["ai", "user_highlight"] },
                              "annotation_id": { "type": "string" } } } } } },
          "evidence": { "type": "array", "maxItems": 4,
                        "items": { "type": "string", "pattern": "^(blk|sec)-[A-Za-z0-9-]+$" } }
        }
      }
    }
  }
}
```

- 根拠は**ブロック単位の配列 `evidence`**(チャットと違いインラインマーカーは使わない — 1h のチップはブロック/図フッタ単位のため)。
- type ⇄ content フィールドの対応はサーバー検証で必須化する(JSON Schema では条件付き必須を表現しない — 決定): `heading`→`heading` / `paragraph`→`markdown` / `quote_source`→`quote` / `figure_embed`→`figure` / `explainer_figure`→`explainer` / `discussion`→`discussion`。対応フィールドを欠くブロックは破棄しログ記録(`discussion` 欠落は下記の再試行規則に従う)。
- `discussion` ブロックは記事内に**ちょうど 1 個**(サーバー検証。0 個なら AI 発案 2 項で自動補完はせず**生成失敗として再試行 1 回**、なお欠落ならジョブ失敗)。`origin='user_highlight'` の項目は `annotation_id` 必須(素材一覧の `ann_…` を書き戻させる)。
- `explainer_figure` ブロックは**最大 2 個・slot 重複不可**(スキーマ+サーバー検証。docs/07 §1.3「追加 2 枚程度」の確定値 = 最大 2 枚)。

### 4.4 生成プロンプト(完全形)

system(`ARTICLE_SYSTEM`、逐語。`{…}` は展開変数):

```
あなたは論文読解ワークベンチ「訳読」の記事構成者です。ユーザーが読み終えた論文について、ユーザー自身の読解の痕跡(訳文・メモ・注釈・チャットでの議論)を素材に、ブログ風の読み物(記事)を JSON で構成します。

## 構成の原則
1. 記事は与えられた素材だけから構成する。本文にない主張・数値を作らない。各ブロックの根拠を evidence 配列に、素材中の [ブロックID|位置] の ブロックID で示す(段落粒度まで特定する)。
2. 定型の論文要約ではなく、ユーザーのメモ・チャットで議論された論点を軸に再構成する。ユーザーが引っかかった箇所(★疑問 の注釈)は必ず取り上げる。
3. タイトルは「{論文の通称} を読む: {核心を一言で}」の型を目安にした日本語(60 文字以内)。
4. 文体は常体(だ・である)。段落は 3〜6 文。専門用語の初出には短い言い換えを添える。
5. quote_source ブロックの text_en は、指定した block_id の原文から一語一句そのまま抜き出す(改変・省略記号の追加をしない)。印象的な原文を 1〜3 箇所引用する。
6. figure_embed は素材の図表リストで「転載: 可」の図だけを指定する。「転載: 不可」の図に触れたい場合は本文で言及するに留める(埋め込まない)。
7. explainer_figure(解説図)は最大 2 個。image_brief_en には描いてほしい概念図・比喩の視覚的内容を英語で書く。文字・数字・数式を画像に含める指示を書かない(重要な情報はすべて caption_ja に書く)。
8. discussion(議論したい点)ブロックをちょうど 1 個、記事の末尾近くに置く。項目は 2〜6 個。★疑問 の注釈に由来する項目は origin を user_highlight とし、その annotation_id を書く。それ以外は origin を ai とする。
9. 数式の扱い: {include_math が true: 「重要な数式は $$...$$(KaTeX)で本文に含めてよい」 / false: 「数式を使わず、言葉と比喩で説明する」}
10. 出典・免責・生成日は書かない(システムが付与する)。

## 章立ての骨子(この順序・粒度を目安に heading を置く)
{preset の章立て — §4.7 の定義を逐語展開}
```

user メッセージ = §4.2 の素材一式(書誌 → 3行要約 → 訳文本文 → 図表リスト → メモ → 注釈 → チャット履歴 の順)。再生成時は末尾に:

```
## これまでの指示履歴
{instructions_history を古い順に列挙}
## 今回の指示(最優先)
{instruction}
## 現在の記事(参考。指示に関係ない部分の構成は維持してよい)
{現行 article_blocks の平文ダンプ}
```

- タスク: `article`(chain=[claude-opus-4-8, gpt-5.5] / effort=high / structured / max_output_tokens=32000 / timeout_s=300)。

### 4.5 サーバー後処理(stage=generating の後半〜rendering)

1. **スキーマ検証**: `yakudoku_llm` の structured 検証(plans/04 §12)+ Pydantic `ArticleV1`。
2. **根拠検証**: 全 `evidence` を §2.5.1 と同一の検証で解決。実在しない ID は除去。`quote_source.block_id` が実在しない場合はブロックごと破棄(ログ記録)。
3. **原文引用の逐語検証**: `text_en` を該当ブロックの `source_text` と空白正規化で部分一致照合。一致しなければ `difflib.SequenceMatcher` で最良一致部分文字列(ratio ≥ 0.8)に**置換**、それ未満はブロック破棄+ログ。
4. **ライセンス判定**(docs/07 §2.5): `papers.license` → `figure_reuse` 判定(plans/03 §6.1 の値)。`forbidden` の図を指す `figure_embed` は `figure_link_card`(plans/03 §19.1 の content 形)へ**変換**: `{ figure_display: "図2", message: "原論文の図2を参照(ライセンス上、転載できません)" }`。転載可の場合は `credit`(`出典: {authors_short}, *{title}* (arXiv:{arxiv_id})` — タイトルは Markdown イタリック)と `license_badge`(例 `CC BY 4.0 — 転載可`)を自動付記。`allowed_nd` はキャプション訳を図と視覚分離するフラグ `caption_separated: true` を content に付ける。
5. **出典ブロック自動挿入**: 末尾に `type='attribution'`、content = `{"text": "出典: {著者全員}. \"{title}.\" {venue}. arXiv:{arxiv_id} · ライセンス {license表示名}"}`、`locked: true`(API 層で書き直し 403 — plans/03 §19.5)。
6. **DB 書き込み**: `articles` upsert(初回 INSERT / 再生成は `version+1`・`generated_at`・`instructions_history` 追記)+ `article_blocks` 全置換(position 0 起点)。`text_plain` を導出して横断検索(PGroonga `pgroonga_article_blocks_text`)に乗せる。
7. **版スナップショット**(§4.6)を S3 に保存。
8. **図の生成**(stage=rendering): 初回生成時のみ全体概要図 v1 を生成(§5 の DSL 生成→SVG レンダリングを同一ジョブ内で実行)。`explainer_figure` ブロックの各 slot について解説図を生成(§6)。**再生成時**: 概要図は再生成しない(独自の「✦ 書き直し指示」導線を持つため — 決定)。解説図は `image_brief_en` から構築したプロンプト文字列が現行版の `explainer_figures.prompt` と一致すれば既存画像を再利用し、変わった slot のみ新版を生成する(コスト最適化 — 決定)。
9. 免責文言は保存しない。`Article.disclaimer` は API 応答時に `訳文・メモ・チャット履歴から自動構成 · {generated_at:%Y-%m-%d} · 元の論文とは別物です — 根拠チップから原文へ` を決定的に組み立てる(1h 逐語)。

### 4.6 版管理(記事)

- DB は**現行版のみ**保持(plans/02 §4.11 の決定)。plans/03 §19.4 の版一覧・復元を成立させるため、**版スナップショットを S3 に保存する**(決定):
  - キー: `renders/articles/{article_id}/v{version}.json`(plans/01 のキーレイアウトへの追加 — §12-⚠5)。
  - 内容: `{ "version": n, "generated_at": …, "preset": …, "include_math": …, "instruction": …, "title": …, "blocks": [ArticleBlock 完全形(検証済み evidence 含む)] }`。
  - 書き込みタイミング: 記事の生成・再生成完了時に**その版**を保存(現行版も常にスナップショットあり)。ブロック単位書き直し(§4.8)は版を進めないため、書き直し後に現行版スナップショットを**上書き更新**する。
- `GET /api/articles/{id}/versions` = スナップショットのメタ一覧(S3 List ではなく `articles.instructions_history` と別に **`articles` 行に `versions_meta JSONB`(`[{version, generated_at, preset, instruction}]`)を持たせず**、スナップショット JSON の先頭メタを S3 GET で読む実装は遅いため、Redis キャッシュ `article:versions:{article_id}` に保存完了時に追記し、ミス時は S3 走査で再構築する — 決定)。
- 復元(`POST …/versions/{version}/restore`): スナップショットを読み、`article_blocks` を全置換+`articles.version = 現行+1` として新スナップショットを保存(plans/03 の「指定版を最新版として複製」)。

### 4.7 構成プリセット 4 種の章立て定義(プロンプト逐語)

`preset` ごとに §4.4 の `{章立ての骨子}` に展開する文字列(docs/07 §2.6 の表の確定形):

| preset | 章立て骨子(逐語) |
|---|---|
| `beginner`(初学者向け) | `1. 背景となる前提知識(この論文を読むのに必要な概念を補う) 2. 何が課題か 3. 提案のアイデア(比喩と図を中心に、数式なしで) 4. 何がうれしいか(結果と意味) 5. 議論したい点。専門用語には初出で注釈を添える。` |
| `implementer`(実装者向け) | `1. TL;DR(3 行) 2. 手法の構成要素(入出力・モデル) 3. 学習手順・損失・ハイパーパラメータ(Markdown 表で) 4. 実装の落とし穴(本文の記述から予想される注意点) 5. 再現チェックリスト(箇条書き) 6. 議論したい点。擬似コードと表を積極的に使う。` |
| `researcher`(研究者向け) | `1. 位置づけ(何が新しいか、既存手法との差分) 2. 手法の核心(主要な数式を含む詳細記述) 3. 実験の批判的読解(設定の妥当性・ベースラインの選び方) 4. 限界と展望 5. 議論したい点。` |
| `reading_group`(輪読会向け) | `発表フロー順に構成する: 1. 背景 2. 課題 3. 手法 4. 実験 5. 議論の論点。「議論したい点」を最も厚く構成し(4〜6 項目)、発表中に問いかけられる形の疑問文にする。` |

### 4.8 ブロック書き直し・指示なし再生成(ホバーツールバー)

- API: `POST /api/articles/{article_id}/blocks/{block_id}/rewrite { instruction? }`(plans/03 §19.5)。`jobs(kind='article', payload={"op":"block_rewrite","block_position":n,"instruction":…})`。記事 `version` は変えない。
- プロンプト: system は `ARTICLE_SYSTEM` の原則 1〜7・9 を再利用した縮約版 `ARTICLE_BLOCK_SYSTEM` + 出力スキーマは §4.3 の `blocks.items` 単体(`name: "article_block_v1"`)。user:

```
## 記事の全体構成(見出しのみ)
{現行記事の heading 一覧}
## 前後のブロック(参考)
{対象の直前・直後ブロックの平文}
## 書き直し対象ブロック
{対象ブロックの JSON}
## 根拠に使える素材(対象ブロックの evidence が指す原文+関連セクション)
{§2.2.2 形式の原文抜粋 ≤8,000 トークン}
## 指示
{instruction あり: instruction / なし: 「内容の主旨を保ったまま、より読みやすく書き直してください。」}
```

- 後処理は §4.5 の 1〜4 を単ブロックに適用し、`article_blocks` の該当行を UPDATE(`origin` 維持)。完了時 `jobs.result = {"block": ArticleBlock}`(plans/03)。`type` の変更は許可しない(検証で拒否 → 再試行 1 回 → 失敗)。
- 「根拠を表示」は API 呼び出し不要(ブロックが `evidence` を保持。web はチップ一覧をポップオーバー表示し、クリックでビューアの該当位置へ)。
- 「議論したい点」ブロックの書き直しでも `origin='user_highlight'` 項目の `annotation_id` 対応は保持させる(素材に注釈一覧を含め、検証で `user_highlight` 項目の annotation_id 実在を確認。消えた場合は旧項目を保持する)。

## 5. 全体概要図(OverviewFigure)

### 5.1 図データ DSL の JSON スキーマ(完全形)

TS 型は plans/03 §20.1 `OverviewFigureDsl` が正(`layout: "flow-3"` / `tone`。plans/02 §3.9 の `flow3`/`emphasis` 形は §12-⚠3 で統一を要求)。JSON Schema(`name: "overview_figure_dsl_v1"`):

```json
{
  "$id": "https://yakudoku.app/schemas/overview_figure_dsl_v1.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["layout", "cards", "connectors"],
  "properties": {
    "layout": { "const": "flow-3" },
    "cards": {
      "type": "array", "minItems": 3, "maxItems": 3,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["role", "label", "heading", "body", "tone"],
        "properties": {
          "role":    { "enum": ["problem", "proposal", "result"] },
          "label":   { "type": "string", "maxLength": 24 },
          "heading": { "type": "string", "maxLength": 36 },
          "body":    { "type": "string", "maxLength": 80 },
          "tone":    { "enum": ["neutral", "accent", "green"] }
        }
      }
    },
    "connectors": {
      "type": "array", "minItems": 2, "maxItems": 2,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["from", "to"],
        "properties": { "from": { "enum": [0, 1] }, "to": { "enum": [1, 2] } }
      }
    },
    "evidence": {
      "type": "array", "maxItems": 4,
      "items": { "type": "string", "pattern": "^(blk|sec)-[A-Za-z0-9-]+$" }
    }
  }
}
```

- 固定制約(サーバー検証): `cards[0].role='problem'`(tone=neutral)/ `cards[1].role='proposal'`(tone=accent)/ `cards[2].role='result'`(tone=green)。connectors は `[{from:0,to:1},{from:1,to:2}]` 固定。`label` の定型: 課題=`課題`、提案=`提案 — {手法名大文字}`(例 `提案 — RECTIFIED FLOW`)、結果=`結果`。
- `footer`(plans/03 の `dsl.footer`)はモデル出力に含めず、サーバーが検証後に付与する: `{"generated_by": "✦ AI 生成 · 訳読", "date": "2026-07-06"}`(date = 生成時の JST 日付)。DB `overview_figures.dsl` には footer 込みで保存する(SVG の決定的導出に必要な全情報を DSL に閉じるため)。
- `evidence` はサーバーで AnchorJson[] に解決して `overview_figures.evidence_anchors` に保存(検証は §2.5.1 と同一)。

### 5.2 生成プロンプト(完全形)

タスク: `overview_figure_dsl`(chain=[claude-opus-4-8, gpt-5.5] / effort=high / structured / max_output_tokens=4096)。system(`OVERVIEW_SYSTEM`、逐語):

```
あなたは論文の「全体概要図」の図データ作成者です。課題 → 提案 → 結果 の 3 カードで論文の骨格を表す JSON を出力します。

## 規則
1. すべての記述は与えられた素材(論文本文)に根拠があること。本文にない主張・数値を書かない。数値(FID などのスコア)は素材から正確に転記する。
2. 文字数制限: label 24 / heading 36 / body 80 文字以内。読み手が 5 秒で骨格を掴める密度にする。
3. label は次の定型に従う: 1 枚目 "課題" / 2 枚目 "提案 — {手法名を大文字英語で}" / 3 枚目 "結果"。
4. heading は名詞止めまたは体言止めの 1 文。body は補足 1〜2 文(常体)。
5. evidence には各カードの根拠となるブロックID(素材の行頭 [ID|位置])を合計 2〜4 個選ぶ(課題・提案・結果それぞれの出所)。
6. JSON のみを出力する。
```

user = 素材: 3行要約(`papers.summary_lines`)+アブスト訳+イントロ先頭 2,000 トークン+結論セクション全文(いずれも §3.1 と同じ特定規則)+リビジョン内の全 `table` ブロックのキャプション原文(文書順・最大 10 件)。合計 ≤12,000 トークン、§2.2.2 の `[ID|位置]` 付き形式。**書き直し時**は末尾に:

```
## 現在の図データ
{現行 DSL の JSON}
## 書き直し指示(最優先)
{instruction}
```

- **数値照合チェック**(docs/07 §1.2): 出力の heading/body から数値トークン(`[0-9][0-9.,×^%]*`)を抽出し、素材の正規化テキストに部分一致するか検証。不一致 → エラー内容を添えて再試行(最大 2 回)→ なお不一致ならジョブ `failed`(error=`数値照合エラー: "4.85" が本文に見つかりません`)。既存版は保持される(P3: 誤った図で上書きしない)。

### 5.3 生成タイミングと版管理

- 初回: 記事初回生成ジョブの rendering 段で v1 を生成(§4.5-8)。記事が存在しない状態で概要図単体は生成しない(1h のとおり記事内要素)。
- 書き直し: `POST /api/articles/{article_id}/overview-figure/rewrite { instruction? }` → `jobs(kind='figure', payload={"figure_kind":"overview","instruction":…})`。完了で `overview_figures` に新行(`version = 現行+1`、`is_current` 付替え、`instruction` 保存)。旧版行は削除しない(plans/02: 全版保持)。
- 版復元: `POST …/versions/{version}/restore` → 指定版の行を `is_current=true` に付け替える(新行は作らない — DSL・SVG とも不変オブジェクトのため。plans/03 の応答は付け替え後の `OverviewFigureRef`)。
- SVG 保存: `renders/overview/{article_id}/v{version}.svg`(plans/01)→ `overview_figures.svg_storage_key`。配信・ダウンロードは `GET /api/overview-figures/{figure_id}/versions/{version}/svg`(`?download=true` で `Content-Disposition: attachment; filename="yakudoku-overview-{arxiv_id または paper_id}-v{version}.svg"`)。

### 5.4 SVG レンダラ仕様(`yakudoku_figures.overview_svg` — 決定的レンダリング)

**要件**: 同一 DSL から常に**バイト同一**の SVG(docs/09 §8)。1h のピクセル仕様を正とする。

#### 5.4.1 レイアウト定数(1h §2.5 の値をそのまま採用)

```python
# packages/figures/src/yakudoku_figures/overview_svg.py
CANVAS_W       = 718.0   # 記事本文カラム 760 − 枠線 2 − 図ブロック内余白 0(SVG は図本体+フッタ)
PAD_X, PAD_Y   = 20.0, 18.0          # 図本体 padding: 18px 20px
ARROW_ZONE_W   = 32.0                # 矢印列: padding 0 8px + グリフ幅 16
CARD_FLEX      = (1.0, 1.2, 1.0)     # 課題 / 提案 / 結果
CARD_PAD_X, CARD_PAD_Y = 14.0, 12.0  # カード padding: 12px 14px
CARD_RADIUS    = 8.0
CARD_TOP_BAR_H = 3.0                 # border-top 3px
CARD_GAP       = 7.0                 # ラベル/見出し/本文の縦 gap
LABEL_FS, LABEL_LH   = 9.5, 13.0     # font-weight 700 / letter-spacing 0.6
HEADING_FS, HEADING_LH = 12.0, 19.2  # line-height 1.6 / font-weight 600
BODY_FS, BODY_LH     = 10.5, 17.33   # line-height 1.65(小数第2位まで)
ARROW_FS       = 16.0                # 「→」 color ARROW_COLOR
FOOTER_H       = 30.0                # フッタ帯(border-top 1px + padding 7px 12px 相当)
FOOTER_FS      = 9.5
CHIP_H, CHIP_FS, CHIP_PAD_X, CHIP_GAP = 15.0, 9.0, 6.0, 6.0   # 根拠チップ
FONT_UI    = "'IBM Plex Sans JP', sans-serif"
```

#### 5.4.2 色トークン(tokens.css / 1h の値。CSS 変数+フォールバックで埋め込む)

**決定**: SVG 内の色は `var(--pr-a, #3E5C76)` 形式(CSS カスタムプロパティ+既定アクセントのフォールバック)で出力する。理由: (a) SVG バイト列がユーザーのアクセント設定に依存せず**決定的**、(b) アプリ内では `<svg>` をインライン展開するためページの `--pr-a` を継承しアクセント連動、(c) 単体ダウンロード時は既定スレートブルーで正しく表示される。

| 用途 | 値(逐語) |
|---|---|
| キャンバス背景 | `#FFFFFF` / フッタ帯背景 `#FBFAF7` / フッタ上罫線 `#F0EDE4` |
| neutral カード | 枠 `#E2DFD5` / 上バー `#B0ACA2` / 背景 `#FBFAF7` / ラベル `#8A8E94` |
| accent カード | 枠 `var(--pr-am, rgba(62,92,118,0.32))` / 上バー `var(--pr-a, #3E5C76)` / 背景 `#FFFFFF` / ラベル `var(--pr-a, #3E5C76)` |
| green カード | 枠 `#E2DFD5` / 上バー `#659471` / 背景 `#FBFAF7` / ラベル `#4C7458` |
| 見出し文字 | `#1E2227` / 本文文字 `#5B6067` / 矢印 `#B0B4BA` |
| フッタ文字 | `#9A9EA4` / 根拠チップ 枠 `var(--pr-am, rgba(62,92,118,0.32))` 文字 `var(--pr-a, #3E5C76)` |

これらの定数は `packages/tokens/css/tokens.css`(plans/08 §2.1。TS 側の正も同ファイル — plans/08 §2.4)の値と一致することを pytest(`test_tokens_match.py` — tokens.css をパースして比較)で検証する。

#### 5.4.3 決定的テキスト折返し(`wrap.py`)

フォントメトリクスに依存しない**文字幅推定**で折返しを行う(環境非依存=決定的):

- 文字幅: 全角(East Asian Width が `F/W/A`)= `1.0 × font_size`、半角英数記号 = `0.55 × font_size`、半角スペース = `0.30 × font_size`。
- 折返し: 貪欲法。CJK 文字間は任意箇所で改行可、ラテン語列(連続する `[A-Za-z0-9.,()\-]+`)は語単位、行幅超過する単語は強制分割。禁則: 行頭に `、。)」』` を置かない(前行末に追い出す)。
- 行数上限: label 1 行(超過は末尾 `…` 切詰め)/ heading 3 行 / body 4 行(超過切詰め+`…`)。

#### 5.4.4 レイアウト計算と SVG 構造

```
card_area_w = CANVAS_W − 2·PAD_X − 2·ARROW_ZONE_W          # = 614
unit        = card_area_w / sum(CARD_FLEX)                   # = 191.875
card_w[i]   = round2(unit × CARD_FLEX[i])                    # 191.88 / 230.25 / 191.88(端数は中央カードで吸収し合計=614)
text_w[i]   = card_w[i] − 2·CARD_PAD_X
content_h[i]= LABEL_LH + CARD_GAP + len(heading_lines)·HEADING_LH + CARD_GAP + len(body_lines)·BODY_LH
card_h      = max(content_h) + 2·CARD_PAD_Y + CARD_TOP_BAR_H
svg_h       = PAD_Y + card_h + PAD_Y + FOOTER_H
```

SVG 要素の出力順(固定): `<svg>`(`xmlns` / `width="718"` / `height` / `viewBox` / `font-family`)→ 背景 rect → カード×3(各: 枠 rect(rx=8)→ 上バー rect(上辺のみ角丸 = path)→ label text → heading text 行ごと `<text>` → body text 行ごと)→ 矢印 text ×2(カード間中央、垂直中央)→ フッタ(罫線 line → 帯 rect → 左テキスト → `根拠:` → チップ(rect+text)×N)。

- フッタ左テキスト = `{dsl.footer.generated_by} · {dsl.footer.date}`(= `✦ AI 生成 · 訳読 · 2026-07-06`)。チップは `evidence_anchors` の display(`§1` `§2.2` `表1`)を右詰めで並べる(はみ出す場合は先頭側から省略)。
- **決定的シリアライズ規則**: (1) 属性は要素種別ごとに固定順で手書きテンプレート出力(汎用 XML ライブラリの属性順に依存しない)。(2) 数値は `format(round(x, 2), 'g')`(最大小数 2 桁・末尾ゼロなし)。(3) 文字列は XML エスケープ(`& < > "`)のみ。(4) 改行 `\n`・インデント 2 スペース固定。(5) 乱数・タイムスタンプ・UUID を含めない(日付は DSL 由来)。(6) `<?xml?>` 宣言なし・コメントなし。
- ゴールデンテスト: 1h の Rectified Flow 概要図に対応する DSL(フィクスチャ `tests/fixtures/overview_rectified_flow.json` として `packages/figures` に同梱)から生成した SVG のバイト列を `tests/golden/overview_rectified_flow.svg` と `assertEqual`。CI で常時検証(docs/09 §8「同一データ→バイト同一」)。

### 5.5 ラスター生成モード(設定オプション)

- 設定 `llm_routing.overview_figure_raster_mode = true`(plans/03 §17.1。既定 false)のとき、概要図ジョブは DSL 生成+SVG レンダリングを**通常どおり実行した上で**、追加で `explainer_image` チェーン(plans/04 §8 の決定: 独立タスクを作らない)によりラスター画像を生成し `overview_figures.render_mode='raster'` / `image_storage_key` / `provider` / `model` / `prompt` を記録する(docs/01 §10「ExplainerFigure と同じ provider/prompt/version を記録」)。
- ラスター用プロンプト: §6.2 の共通プリアンブル + `Concept: a three-stage flow diagram showing (1) {problem heading の英訳}, (2) {proposal heading の英訳}, (3) {result heading の英訳}, connected left to right by arrows. Abstract shapes only.`(3 見出しの英訳は、ラスター生成の直前に `summary` タスクの structured 呼び出し 1 回(出力 `{"en": ["…","…","…"]}`)で得る — 決定。usage は task='summary'・job_id=当該 figure ジョブで記録し、クォータは消費しない(job_id ありのため §9.2 の判別で自動的に除外される))(**カード本文テキストは画像に埋めない** — 文字は SVG/キャプション側の責務)。
- 表示: `raster_url` 非 null のとき記事ビューはラスターを表示し、「SVG ⤓」は常に SVG(正)をダウンロードする。画像 1 枚は images クォータを消費(§9)。

## 6. 解説図(ExplainerFigure・ラスター)

### 6.1 生成フロー

- **生成は記事生成・再生成に付随**(docs/07 §1.4。単体の新規作成 API なし)。枚数は**最大 2 枚**(§4.3 の slot 0/1)。記事ジョブの rendering 段で slot ごとに `ImageRouter.run("explainer_image", prompt, …)` を実行する。
- 単体再生成: `POST /api/explainer-figures/{figure_id}/regenerate { instruction? }`(plans/03 §20.2)→ `jobs(kind='figure', payload={"figure_kind":"explainer","slot":n,"instruction":…})`。instruction がある場合はプロンプトの Concept 節の後に改行 2 つを挟み、逐語テンプレート `Revision request — follow this Japanese instruction from the user: 「{instruction}」`(instruction は日本語のまま埋め込む・英訳しない)を追記する。新版 `version+1`・`is_current` 付替え(plans/02 §4.11)。
- 保存: PNG 正規化済みバイト(plans/04 §6.6)を `renders/explainer/{explainer_figure_id}/v{version}.png` に保存 → `explainer_figures.image_storage_key`。記事ブロック `explainer_figure` の content は `{"explainer_figure_id": "…"}`(plans/02)で、API 応答時に current 版の `image_url`(署名 URL 経由 `/api/assets/…`)と `caption` を解決する。
- 失敗時: 画像生成チェーン全滅なら該当ブロックを**画像なしプレースホルダ**(web 側で「解説図の生成に失敗しました · 再試行」カード)とし、記事ジョブ自体は成功させる(部分成功 — docs/09 §2)。失敗理由は処理ログへ。

### 6.2 画像プロンプト構成規則(確定)

```python
EXPLAINER_STYLE_PREAMBLE = (
    "Flat editorial illustration for a calm, scholarly reading app. "
    "Muted low-saturation palette: dusty slate blue (#3E5C76), warm beige (#F4F3EF), "
    "soft sage green (#659471), charcoal gray (#2B2E33) on an off-white background (#FBFAF7). "
    "Clean geometric shapes, thin lines, generous whitespace, subtle depth. "
    "Strictly NO text, NO letters, NO digits, NO formulas, NO labels, NO watermarks, NO logos."
)

def build_explainer_prompt(image_brief_en: str) -> str:
    return f"{EXPLAINER_STYLE_PREAMBLE}\n\nConcept to illustrate: {image_brief_en}"
```

- **重要テキストは画像に埋めない**(docs/07 §1.3): 禁止指示はプリアンブルで恒常化し、さらに §4.4 原則 7 で `image_brief_en` 自体に文字要求を書かせない(2 段防御)。用語・数値・結論はすべて `caption_ja` に置く。
- サイズ・品質: `size="1536x1024"` / `quality="standard"`(routing.yaml)。プロバイダ別マッピングは plans/04 §6.6。
- `explainer_figures.prompt` には最終プロンプト全文(プリアンブル込み)を保存する(再現性・§4.5-8 の再利用判定キー)。

## 7. 語彙 AI 生成

### 7.1 フロー

- `POST /api/vocab`(plans/03 §11.2)が `vocab_entries` を `generation_status='pending'` で INSERT →即 201 応答(読書を止めない)→ `jobs(kind='vocab', payload={"vocab_id": …, "fields": null})` を enqueue(interactive キュー。性能目標 p50 3 秒 / p95 10 秒 — docs/09 §1)。
- worker `generate_vocab`: LLM 呼び出し(§7.2)→ 対象フィールドを UPDATE → `generation_status='complete'`。失敗(チェーン全滅)時は `generation_status='failed'` + `generation_error`(語彙本体・文脈・出典は保存済みのまま — docs/11 §2 P3)。
- 再生成: `POST /api/vocab/{id}/regenerate { fields? }` → 同ジョブ、`payload.fields` = 指定フィールド(省略時は全フィールド)。**`edited_fields` に含まれるフィールドは常に生成対象から除外し、レスポンス JSON に含まれていても書き込まない**(二重防御。docs/11 §4 の編集保護)。

### 7.2 生成フィールド 9 種のプロンプト(完全形)

タスク: `vocab`(chain=[claude-haiku-4-5, gpt-5.4-mini, gemini-3.5-flash] / effort=none / structured / max_output_tokens=2048 / timeout_s=30)。

JSON Schema(`name: "vocab_content_v1"`。DB カラム plans/02 §4.8 と 1:1):

```json
{
  "type": "object", "additionalProperties": false,
  "required": ["kind", "pos_label", "ipa", "meaning_short", "meaning_long",
               "interpretation", "etymology", "mnemonic", "related_forms"],
  "properties": {
    "kind":           { "enum": ["word", "collocation", "idiom"] },
    "pos_label":      { "type": "string", "maxLength": 12 },
    "ipa":            { "type": "string", "maxLength": 60 },
    "meaning_short":  { "type": "string", "maxLength": 30 },
    "meaning_long":   { "type": "string", "maxLength": 200 },
    "interpretation": { "type": "string", "maxLength": 260 },
    "etymology":      { "type": "string", "maxLength": 200 },
    "mnemonic":       { "type": "string", "maxLength": 200 },
    "related_forms":  { "type": "string", "maxLength": 200 }
  }
}
```

system(`VOCAB_SYSTEM`、逐語):

```
あなたは学術英語の語彙学習コンテンツ作成者です。論文の文脈センテンスの中で使われた語彙について、日本語学習者向けの学習コンテンツを JSON で出力します。

## フィールドの書き方
- kind: 語彙の種別。単一語なら word、決まった語の組合せなら collocation、字面から意味が推測しにくい定型表現なら idiom。
- pos_label: 細かい分類ラベル(例: 句動詞 / 他動詞 / 副詞 / 形容詞 / 前置詞句)。
- ipa: 発音記号。スラッシュで囲む(例: /ˌbɔɪl ˈdaʊn tə/)。
- meaning_short: この文脈での語義の短形(一覧表示用、30 文字以内。例: 要するに〜に帰着する)。
- meaning_long: この文脈での語義の長形。辞書義の羅列ではなく、この文でどういう意味かを説明する。キーとなる訳語を **太字** にし、「この文では「…」」の形で文脈への当てはめを 1 文添える。
- interpretation: 解釈のしかた。次に似た表現に出会ったとき自力で読めるようになる「読み方の型」を解説する(例: 句動詞は動詞の物理イメージ+方向詞で読む、のような分解)。
- etymology: 語源メモ。語根と同族語を 1〜2 文で(例: boil ← ラテン語 bullīre(泡立つ)。bubble、ebullient と同族。)。
- mnemonic: 覚えるコツ。具体的なイメージ・場面による記憶フック(例: カレーを煮詰めるイメージ。枝葉が飛んで本質だけが残る。)。
- related_forms: よく出る形・近い表現。頻出パターンと類義表現をスラッシュ区切りで(例: it boils down to whether… / come down to(ほぼ同義)/ amount to)。

## 規則
- すべて日本語で書く(英語表現の例示部分を除く)。
- 文脈センテンスでの意味を最優先する。多義語でも文脈外の語義は書かない。
- 誇張・絵文字を使わない。落ち着いた学習ノートの文体。
```

user(逐語テンプレート):

```
語彙: {term}
文脈センテンス: {context_sentence}(対象語は {highlight.start}〜{highlight.end} 文字目)
出典: {paper_title} {section_label}
```

- 後処理: `kind` は保存時のユーザー未編集なら上書き(自動判定 — docs/11 §3)。`meaning_long` の `**太字**` は Markdown のまま保存し、詳細パネルが `<b>` 描画する。schema 検証は plans/04 §12(DeepSeek は fallback チェーン外なので常にネイティブ structured)。

## 8. ステータス提案・読了提案(LLM 不使用)

### 8.1 アクティブ秒数の計測(3 分ルールの実装)

**クライアント(apps/web、ビューア画面)**:

1. ビューアを開いたとき `client_session_id = crypto.randomUUID()` を生成し、以後この ID で upsert(冪等 — plans/03 §5.9)。
2. 1 秒ティックで `active_seconds` をインクリメントする条件: `document.visibilityState === 'visible'` **かつ** 直近 60 秒以内に入力イベント(`pointermove` / `pointerdown` / `keydown` / `wheel` / `scroll` / `touchstart`。250ms スロットル)がある。
3. 送信(ハートビート): **30 秒間隔**で `POST /api/library-items/{id}/reading-sessions { client_session_id, started_at, last_activity_at, active_seconds }`(累計値を送る=リトライ安全)。`visibilitychange(hidden)` / `pagehide` 時は `navigator.sendBeacon` で即時送信。
4. 設定 `reading.track_reading_time=false` のときは計測もハートビートも行わない(サーバー側も記録しない — plans/03 §5.9)。

**サーバー(suggestions.py — ハートビート API 内の同期判定。ジョブ化しない、plans/01 の決定)**:

```python
async def on_reading_heartbeat(item: LibraryItem, session_total: int) -> None:
    st = await get_user_settings(item.user_id)
    if st.reading.status_transition == "off":
        return
    # --- 「読んでいる」提案(3 分ルール) ---
    if item.status in ("to_read", "read_soon") and item.total_active_seconds >= 180:
        if st.reading.status_transition == "auto":
            await apply_status(item, "reading")    # 通知なし・suggestion_exists 判定もしない(下記)
        elif not await suggestion_exists(item.id, suggested_status="reading"):  # 「1 回だけ」
            # "suggest"(既定)
            await create_notification(item.user_id, kind="status_suggestion",
                payload={"library_item_id": item.id, "paper_title": item.paper.title,
                         "suggested_status": "reading", "reason": "read_3min"})
```

- **判定値**: `total_active_seconds >= 180`(3 分ちょうどを含む)。`total_active_seconds` はハートビートのトランザクション内で再集計済みの非正規化キャッシュ(plans/02 §1.5)。
- **「1 回だけ」の恒久化**(suggest モード): 同一 `library_item_id` × `suggested_status` の `status_suggestion` 通知が(既読を問わず)存在すれば再提案しない。auto モードは通知レコードを作らないため `suggestion_exists` 判定を行わず、`item.status` 条件だけで制御する(適用後は status が変わるので再適用されない。ユーザーが手動で to_read に戻して読み続けた場合の再適用は auto 設定の意図どおりの挙動とする — 決定。§8.2 の auto も同じ規則)。判定クエリは `notifications` の `payload->>'library_item_id'` 部分インデックスを追加せず、`(user_id, kind)` 絞り込み+アプリ層フィルタで行う(通知は少量)。
- 通知文言(4a 逐語): 「✦ {title} を 3 分以上読んでいます。**「読んでいる」にしますか?**」+「変更する / そのまま」+補足「ステータスは勝手に変わりません — 提案のみ(設定で変更可)」。2 択の消化は `POST /api/notifications/{id}/action`(plans/03 §16.4)。

### 8.2 読了提案(最終セクション付近)

判定は**読書位置保存 API**(`PUT /api/library-items/{id}/position`、plans/03 §5.8)内の同期処理:

```python
async def on_position_saved(item: LibraryItem, revision_id: str, block_id: str) -> None:
    st = await get_user_settings(item.user_id)
    if st.reading.status_transition == "off" or item.status != "reading":
        return
    # pos・total とも「本文ブロック」(in_progress_denominator=true のセクション内のブロック)のみで数える。
    # pos = 本文ブロックを block_search_index.position 順に並べたときの当該ブロックの序数(1 起点)。
    pos = await body_block_ordinal(revision_id, block_id)      # 本文ブロック外(参考文献・付録)なら None → return
    total = await body_block_count(revision_id)                # 本文ブロック総数
    in_last = await is_in_last_body_section(revision_id, block_id)
    if pos is not None and total > 0 and pos / total >= 0.90 and in_last:
        if st.reading.status_transition == "auto":
            await apply_status(item, "done")   # finished_at 自動記録 → 読了フロー 1g は開かない(自動適用時はモーダルを出さない — 決定。P6)
        elif not await suggestion_exists(item.id, suggested_status="done"):
            await create_notification(..., payload={..., "suggested_status": "done",
                                                    "reason": "reached_end"})
```

- **「最終セクション付近」の確定定義**: 先頭可視ブロックが本文最終セクション(`in_progress_denominator = true` の最後のトップレベルセクション — plans/03 §6.1 TocNode)内にあり、かつ本文内位置(`本文ブロック中の序数 / 本文ブロック総数`)≥ 0.90。
- 提案の適用(「変更する」)で `status='done'` になると、web は読了フロー 1g のモーダルを開く(通常のステータス変更と同一挙動)。

## 9. usage 計測とクォータ判定

### 9.1 計測(全機能共通)

- 計測は `packages/llm` の `MeterHook`(apps/api の `DbMeterHook`)経由で `usage_records` に 1 試行 1 行(plans/04 §10 を正とする。plans/02 §4.13 の同名テーブル定義との二重化は §12-⚠2)。
- 本書の機能と `usage_records.task` の対応: チャット・詳細要約=`chat` / 3行要約・セクション要約・まとめてメモ化=`summary` / 記事生成・ブロック書き直し=`article` / 概要図 DSL=`overview_figure_dsl` / 解説図・ラスター概要図=`explainer_image` / 語彙=`vocab`。`library_item_id` は必ず埋め、`job_id` はジョブ実行のもののみ埋める(API 同期実行のチャット・詳細要約・まとめてメモ化・セクション要約は job_id なし — §9.2 のカウンタ判別に使う)。
- ストリーミング(chat)は `end` イベントの `Usage` で成功行を記録。SSE 切断後も §2.1 の shield によりストリームを消費しきるため計測は欠落しない。

### 9.2 月次クォータ(確定値と判定)

クォータは `key_source='operator'` の行のみ集計(BYOK は非消費 — docs/09 §3.5)。集計期間は UTC ではなく **JST の暦月**(`date_trunc('month', created_at AT TIME ZONE 'Asia/Tokyo')`)。上限値(運営設定。環境変数ではなく `llm_task_routes` と同様に DB 設定テーブル `quota_limits(key TEXT PRIMARY KEY, monthly_limit INT)` で管理し、既定値をシードする — 決定。plans/02 に反映済み — §12-⚠8):

| カウンタ | 既定上限/月 | 消費イベント(1 消費の単位) | 超過時挙動 |
|---|---|---|---|
| `translation_papers` | 30 本 | 全文翻訳の開始(論文×スタイル。共有キャッシュヒットは非消費)— 翻訳計画の管轄 | ingest の翻訳段のみ `waiting_quota`(plans/03 §17.4) |
| `chat_messages` | 500 件 | チャット送信・再生成・定型アクション(リプレイ §2.7.1 は非消費)・まとめてメモ化・圧縮モードのセクション要約(§2.2.3。LLM 呼び出し 1 回=1 消費)。取り込み時 ✦3行要約(job_id あり)は非消費 | 429 `quota_exceeded` |
| `images` | 20 枚 | 解説図 1 枚・ラスター概要図 1 枚(生成成功時に消費) | 429 `quota_exceeded` |
| `article_generations` | 30 回 | 記事生成・指示つき再生成 = 1 / ブロック書き直し = 1 / 概要図書き直し(DSL)= 1 | 429 `quota_exceeded` |
| `vocab_generations` | 300 回 | 語彙の初回生成 = 1 / 再生成 = 1 | 429 `quota_exceeded` |

- **事前チェック**(`services/quota.py`): 生成を伴う POST(チャット送信 / regenerate / summarize-to-note / article / rewrite / overview rewrite / explainer regenerate / vocab / vocab regenerate)の冒頭で該当カウンタを判定し、超過なら 429 `quota_exceeded`(RFC7807。detail に「設定画面で自分の API キー(BYOK)を登録すると制限なく利用できます」を含める — plans/03 §1.4 の例文と同形)。該当プロバイダに BYOK が設定済み(チェーン先頭モデルのプロバイダ)なら判定をスキップする。
- カウンタ集計クエリ(chat_messages の例): `SELECT count(*) FROM usage_records WHERE user_id=:u AND key_source='operator' AND status='ok' AND (task = 'chat' OR (task = 'summary' AND job_id IS NULL)) AND created_at >= :month_start`(`task='summary' AND job_id IS NULL` = まとめてメモ化+セクション要約。取り込み時 ✦3行要約は ingest ジョブ内で job_id が入るため除外される)。`images` は `sum(image_count)`。
- `GET /api/settings/quota`(plans/03 §17.4)は上表 5 カウンタを返す — 応答スキーマへの `article_generations` / `vocab_generations` の追加は plans/03 に反映済み(§12-⚠1)。
- 事前チェックと消費記録の間の競合(同時リクエストで上限を 1〜2 件超える)は許容する(厳密な直列化はしない — 決定。上限は保護目的の概算で足りる)。

## 10. 性能・信頼性の実装ポイント(docs/09 §1 への割付)

| 目標 | 実装手段 |
|---|---|
| チャット初回トークン p50 5 秒 | 文脈ビルダーのローカルトークン見積り(count_tokens API を呼ばない)/ `bsi:{revision_id}` Redis キャッシュ / system プロンプトキャッシュ(2 呼び出し目以降)/ effort=medium |
| 記事初回生成 p50 30 秒 | 素材収集は SQL 一括(N+1 禁止)/ 記事 JSON と概要図 DSL・解説図 2 枚を**並行**生成しない(記事 JSON が explainer brief を決めるため直列: article → [overview_dsl ∥ explainer×2] の 2 段。後段は `asyncio.gather`) |
| 解説図 1 枚 p50 20 秒 | 既定 `gemini-3.1-flash-image` / quality=standard |
| 語彙生成 p50 3 秒 | `claude-haiku-4-5` / effort=none / interactive キュー(`max_jobs=10`) |
| 失敗の可視化(P3) | 全ジョブの `error` を UI 表示可能な日本語文で保存 / 語彙は本体保存+`generation_error`+再試行 / チャットはエラーメッセージを履歴に残す / 概要図の数値照合失敗は既存版を保持 |

## 11. 受け入れ基準

- [ ] チャット送信 SSE が plans/03 §10.3 の 5 イベント契約に従い、モデル出力の `[[evidence:blk-…]]` が検証済み `[[ev:n]]` + `evidence` イベントに変換される。実在しないブロック ID のマーカーは配信前に除去される
- [ ] `<outside_knowledge>` / `<speculation>` タグが `MessageBlock.type="aside"`(label 付き)として分離配信・保存され、UI の淡色ボックスに対応する
- [ ] 60,000 トークン超の論文で圧縮モード(全セクション要約+関連 3 セクション全文+選択周辺±2 ブロック)に切り替わり、コンテキスト超過エラーが発生しない
- [ ] 定型チップ 5 種+入力候補 2 種+導線 3 種が §2.7 のテンプレートどおりの user メッセージとして送信され、「実験設定の整理」で Markdown 表が返る
- [ ] `summary_3line` / `detailed_summary` の再送で保存済み回答がリプレイされ(LLM 非呼出・クォータ非消費)、「再生成」では必ず新規生成される
- [ ] 「↑ メモに保存」で根拠アンカーが `notes.anchors` に複写され、aside が blockquote 化された Markdown になる
- [ ] 取り込み時に `papers.summary_lines` に 3 行(各 ≤60 文字)が保存され、数値照合に失敗した場合は要約なしで取り込みが完了する(パイプラインは失敗しない)
- [ ] 記事生成が preset 4 種の章立て定義に従い、`discussion` ブロックがちょうど 1 個生成され、疑問ハイライト由来の項目に `origin='user_highlight'` + 実在する `annotation_id` が付く
- [ ] `attribution` ブロックがサーバー自動挿入され、`locked=true` で書き直し API が 403 を返す
- [ ] ライセンス `forbidden` の論文で `figure_embed` が `figure_link_card` に変換され、CC BY 4.0 では credit と license_badge が自動付記される
- [ ] 記事の版スナップショットが `renders/articles/{article_id}/v{n}.json` に保存され、`versions` 一覧と `restore` が機能する
- [ ] 同一 DSL から生成した SVG が常にバイト同一(ゴールデンテスト)で、アプリ内表示はアクセント連動・単体ダウンロードは既定色 #3E5C76 で表示される
- [ ] 概要図の書き直しで版が増え、`restore` で `is_current` が付け替わり前の版に戻せる。数値照合エラー時は既存版が保持されジョブが理由付きで失敗する
- [ ] 解説図が最大 2 枚(slot 0/1)生成され、プロンプトに文字禁止指示が含まれ、`explainer_figures.prompt/provider/model/version` が記録される。ラスター概要図モードで SVG が併存し「SVG ⤓」は常に SVG を返す
- [ ] 語彙保存後 p50 3 秒で 9 フィールド(kind/pos_label/ipa/meaning_short/meaning_long/interpretation/etymology/mnemonic/related_forms)が生成され、`edited_fields` のフィールドは再生成で上書きされない。生成失敗でも本体は保存され再試行できる
- [ ] `to_read`/`read_soon` の論文を累計 180 秒以上アクティブに読むと `status_suggestion(reason=read_3min)` 通知が**1 回だけ**生成され、設定 auto では通知なしで適用、off では何も起きない
- [ ] 本文最終セクション内かつ位置 ≥90% で `reason=reached_end` の「読んだ」提案が 1 回だけ出る
- [ ] BYOK 設定済みプロバイダではクォータ事前チェックがスキップされ、`usage_records` の `key_source='user'` 行がクォータ集計に入らない
- [ ] 5 カウンタ(translation_papers/chat_messages/images/article_generations/vocab_generations)の消費が §9.2 の単位で記録され、超過時に 429 `quota_exceeded`(BYOK 誘導文付き)が返る

## 12. ⚠ 基盤への追加要求

本書の設計が要求する、基盤計画書(plans/00〜04・08)への追加・修正。**別名を発明せず、以下の修正を基盤側に反映すること**:

1. **【反映済み】(plans/03 §17.4)**: `GET /api/settings/quota` の `usage` に `article_generations: { used, limit }` と `vocab_generations: { used, limit }` を追加(本書 §9.2 の 5 カウンタ制)— plans/03 に反映済み。
2. **【usage_records は反映済み】(plans/02 §4.13)**: `usage_records` の定義が plans/04 §10.1(`task` 9 値・`key_source`・`fallback_rank` あり)と二重定義になっていた。**plans/04 §10.1 を正**とし、plans/02 §4.13 の `usage_records`(`purpose` 6 値)定義を plans/04 の形に差し替える — 反映済み。あわせて BYOK テーブルも plans/04 §11.2 の `user_provider_keys` と plans/02 §4.2 の `byok_api_keys` が二重定義 — **plans/02 の `byok_api_keys` を正とし**(plans/03 §17.3 のエンドポイント `/api/settings/api-keys` と対応)、plans/04 §11 のテーブル名・パスを追随させる(決定)。
3. **⚠ 基盤への追加要求(plans/02 §3.9)**: `OverviewDslJson` の形(`layout: "flow3"` / `emphasis`)を plans/03 §20.1 の `OverviewFigureDsl`(`layout: "flow-3"` / `tone` / `footer`)に合わせて更新(本書 §5.1 のスキーマが正)。
4. **⚠ 基盤への追加要求(plans/01 §4)**: ジョブ stage 名(`generate_article: composing/figures`、`generate_overview_figure: dsl/render`)を plans/02 §4.13 の値域(`collecting_sources/generating/rendering`、`generating_dsl/rendering_svg`)に統一。
5. **⚠ 基盤への追加要求(plans/01 §ストレージ)**: S3 キーレイアウトに `renders/articles/{article_id}/v{version}.json`(記事の版スナップショット — 本書 §4.6)を追加。
6. **⚠ 基盤への追加要求(plans/01 §3.3)**: チャット SSE のイベント名(`message.delta` / `message.completed` / `message.failed`)と根拠記法(`[[anchor:…]]`)は plans/03 §10.3(`start/delta/evidence/done/error`、`[[ev:n]]`)が正 — plans/01 のシーケンス図を追随修正。
7. **【反映済み】(plans/03 §10.5)**: `summarize-to-note` は本書 §1.1 の決定により API 同期実行(`Response 201: { note: Note }`)へ変更(`jobs.kind` に `thread_summarize` 相当が存在しないため)— plans/03 に反映済み。
8. **【反映済み】(plans/02 §4.10 付近)**: 運営クォータ上限テーブル `quota_limits (key TEXT PRIMARY KEY, monthly_limit INT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())` の追加(本書 §9.2。シード: `translation_papers=30, chat_messages=500, images=20, article_generations=30, vocab_generations=300`)— plans/02 に反映済み。
9. **⚠ 基盤への追加要求(plans/04 §8)**: `summary` タスクの用途記述「✦3行要約と詳細要約の両方が使う」を修正。本書の決定では詳細要約はチャット定型アクション(task `chat`、§3.2)であり、`summary` の用途は「取り込み時 ✦3行要約・セクション要約(§2.2.3)・まとめてメモ化(§2.8)・用語自動抽出(plans/04 §8 既記)」。routing.yaml の `summary` コメントも同様に追随修正。
