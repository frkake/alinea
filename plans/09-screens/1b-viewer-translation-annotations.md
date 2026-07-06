# 画面 1b: ビューア 訳文+注釈パネル(対訳ポップ/選択メニュー)

> 対象読者と前提: 本書は「訳読 / YAKUDOKU — 論文読解ワークベンチ」の apps/web(Next.js 15 App Router + React 19 + TypeScript 5 + Tailwind CSS v4)実装者向けに、確定デザイン画面 1b(論文ビューア・訳文モード・ゆったり)をピクセル一致で実装するための完全仕様である。機能仕様は docs/04(ビューア)・docs/03(翻訳)・docs/11(語彙帳)を正、ピクセル値は抽出ファイル extract/1b.md を正とする。共通コンポーネント名は plans/08-design-system.md、API 名は plans/03-api.md、トークンは packages/tokens(plans/08 §2)のものを必ず使う。ビューア共通骨格(ヘッダ・目次レール/ペイン・サイドパネル枠・URL 契約・キーマップ登録・論文内検索・読書位置/時間フック)は plans/09-screens/viewer-shell.md が実装・仕様の正であり、本書での再掲はピクセル照合用の転記である(値が食い違う場合は viewer-shell.md を正とし本書を改訂する)。本書が所有するのは viewer-shell §11 の「1b」行 — `TranslationPane`・前回位置バナー・対訳ポップ・`t`/`b` の実処理・SelectionMenu の呼び出し・`AnnotationsTab` — である。本書に書かれた値・識別子・文言が実装の正であり、独自の解釈・丸めを禁止する。

## 1. 概要とルート

- **ルートパス(確定)**: `/papers/[itemId]`(`apps/web/src/app/(app)/papers/[itemId]/page.tsx`。viewer-shell §3.1 と同一)。`itemId` は `li_…` 形式。
  - 表示モードはクエリパラメータ `?mode=translation|parallel|source|pdf|article` で表現する。決定: **省略時は `last_position.mode`、それも無ければ `translation`**(viewer-shell §3.2 と同一)。理由: docs/04 §11(前回位置のデバイス間同期)と 1 ユーザー操作原則(P2)の両立。画面 1b は `mode=translation` の状態である。
  - サイドパネルの初期タブはクエリ `?panel=chat|notes|annotations|figures|resources|info` で指定できるが、**補助クエリであり初期化時に 1 回消費して `router.replace` で URL から除去する**(viewer-shell §3.1。タブ切替は URL に書かない)。省略時は `sessionStorage` 保存値 → 既定 `chat`(viewer-shell §6.3)。画面 1b は注釈タブがアクティブな状態である。
- **認証**: 必須(session Cookie)。ルートグループ `(app)` のレイアウトがセッション確認(`GET /api/auth/me`)を行い、未認証は `/login?next=/papers/{id}` へ `router.replace`(plans/01 §2.1。4d と同一機構)。
- **画面の役割**: ユーザーが最も長く滞在する読解画面の既定モード。日本語訳文を 720px・1 カラムの「ゆったり」レイアウトで読み、(1) 段落単位の対訳ポップで「この訳、合ってる?」を 1 秒で確認(P1)、(2) テキスト選択メニューで 4 色ハイライト・コメント・AI 質問・語彙追加・コピー、(3) サイドパネル「注釈」タブで注釈の一覧・フィルタ・エクスポート、(4) 前回位置バナーで読書の再開、を提供する。
- ビューア骨格(ヘッダ・目次レール・サイドパネル枠)は 1a/1c/2a/1h/5a と共通の `ViewerShell`(plans/09-screens/viewer-shell.md)であり、本書は訳文モード本文(`TranslationPane`)と注釈タブ(`AnnotationsTab`)、および 1b で初出の共通片(ResumeBanner・SelectionMenu の呼び出し・対訳ポップ)の仕様を確定させる。

## 2. データ要件

### 2.1 使用 API エンドポイント(plans/03 の名前)

| # | エンドポイント | 用途 | 取得/実行タイミング |
|---|---|---|---|
| 1 | `GET /api/library-items/{id}/viewer` | 初期化複合(書誌・目次・翻訳進捗・タブ件数・last_position) | ルート表示時に最初に 1 回 |
| 2 | `GET /api/revisions/{revision_id}/document?section_id=` | 構造化本文(セクション単位) | 初期表示: last_position のセクション(無ければ先頭)+前後 1 セクション。以後スクロールで先読み(§2.4) |
| 3 | `GET /api/revisions/{revision_id}/translations/{style}/units?section_id=` | 訳文ユニット(セクション単位) | #2 と同時に並列取得 |
| 4 | `GET /api/library-items/{id}/annotations` | 注釈一覧+件数 | 注釈タブ初回表示時(panel=annotations で初期表示なら初期化直後) |
| 5 | `POST /api/library-items/{id}/annotations` | ハイライト/コメント作成 | 選択メニューの色ドット・「コメント」確定時 |
| 6 | `PATCH /api/annotations/{annotation_id}` / `DELETE /api/annotations/{annotation_id}` | 色変更・コメント編集/削除 | 注釈カードの操作時 |
| 7 | `POST /api/translation-units/{unit_id}/retranslate` | 再翻訳・指示つき再翻訳 | 対訳ポップのフッタ操作時 |
| 8 | `GET /api/jobs/{job_id}/events` | 再翻訳ジョブの進捗 SSE | #7 の 202 受領後に接続 |
| 9 | `POST /api/translation-units/{unit_id}/proposal/accept` / `DELETE /api/translation-units/{unit_id}/proposal` | 再翻訳案の採用/破棄 | proposal 表示 UI の操作時 |
| 10 | `POST /api/vocab` | 「語彙に追加」 | 選択メニュー操作時(side="source" のみ) |
| 11 | `GET /api/revisions/{revision_id}/search?q=` | 論文内検索 | 検索ボックス入力 300ms デバウンス |
| 12 | `PUT /api/library-items/{id}/position` | 読書位置の自動保存 | 先頭可視ブロック変化から 5 秒デバウンス |
| 13 | `POST /api/library-items/{id}/reading-sessions` | 読書時間計測 | 60 秒間隔+`visibilitychange`(hidden)時。client_session_id で冪等 |
| 14 | `PATCH /api/library-items/{id}` | 読書ステータス変更(ヘッダピル) | ステータスドロップダウン選択時 |
| 15 | `GET /api/library-items/{id}/export/annotations` | 注釈 Markdown エクスポート | フッタ「⤓ Markdown エクスポート」クリック時(`<a download>` 直リンク) |
| 16 | `GET /api/revisions/{revision_id}/blocks/{block_id}` | 注釈カード→本文ジャンプ時の未ロードブロック解決 | 該当セクション未ロード時のみ |
| 17 | `POST /api/translation-sets/{set_id}/prioritize` | 表示セクションの優先翻訳繰り上げ | translation.status="partial" 中にセクションを開いた時 |
| 18 | `GET /api/library-items/{id}/jobs?active=true` | 進行中 translation_set ジョブの特定(§2.3) | `viewer.translation.status !== "complete"` の初期化直後に 1 回 |
| 19 | `POST /api/library-items/{id}/annotations`(kind='bookmark')/ `DELETE /api/annotations/{annotation_id}` | 現在セクションのブックマークトグル | キー `b`(§5.4) |

「詳細要約 →」はチャットタブへの定型導線(docs/04 §3)であり、チャットタブへ切替えて(viewer-store の `setPanel(true,'chat')`。以下、本書の「panel=chat 等へ切替」表記はすべてこの store action を指し、URL は書き換えない)QuickAction `detailed_summary`(plans/03 §10)を入力欄にプリセットする。API 呼び出しはチャット画面仕様(1a)に委譲。「✦ AIに質問」も同様に `panel=chat` + 選択アンカー付きプリセット。

### 2.2 TanStack Query キー設計(確定)

`apps/web/src/lib/query-keys.ts` に集約する。文字列リテラルの直書き禁止。

```ts
export const qk = {
  viewer:       (liId: string) => ['viewer', liId] as const,
  document:     (revId: string, sectionId: string) => ['document', revId, sectionId] as const,
  units:        (revId: string, style: 'natural' | 'literal', sectionId: string) =>
                  ['units', revId, style, sectionId] as const,
  annotations:  (liId: string) => ['annotations', liId] as const,
  paperSearch:  (revId: string, q: string) => ['in-paper-search', revId, q] as const, // viewer-shell §2.2 と同一キー
  block:        (revId: string, blockId: string) => ['block', revId, blockId] as const,
};
```

- `staleTime`: `document`・`block` は `Infinity`(リビジョンは不変。ETag 304 前提)。`units` は 60_000ms(再翻訳採用で明示 invalidate)。`viewer` は 30_000ms。`annotations` は 0(mutation で invalidate)。`paperSearch` は 30_000ms。
- mutation 後の invalidate 規則:
  - 注釈作成/更新/削除 → `qk.annotations(liId)` を invalidate + `qk.viewer(liId)`(タブ件数 `counts.annotations`)を invalidate。作成は楽観的更新(§5.6)。
  - proposal 採用/破棄 → `qk.units(revId, style, sectionId)` を invalidate。
  - ステータス変更 → `qk.viewer(liId)` を setQueryData で即時書き換え(楽観的)。

### 2.3 リアルタイム更新

- **再翻訳ジョブ**: `POST …/retranslate` → 202 `{ job_id }` → `GET /api/jobs/{job_id}/events` に `packages/api-client` の `sseFetch()` で接続。`event: done` で `qk.units(...)` を invalidate(proposal が入る)。`event: error` は Toast(§5.9)。SSE は対訳ポップを閉じても維持する(完了 Toast で通知)。
- **翻訳セット進捗**: `viewer.translation.status !== "complete"` の場合、`GET /api/library-items/{id}/jobs?active=true` で translation_set ジョブを特定し、その `GET /api/jobs/{job_id}/events` を購読。`progress` 受信ごとに `qk.viewer(liId)` の `translation.progress_pct` を setQueryData で更新し、翻訳済みになったセクションの `qk.units` を invalidate する。SSE 不通時のみ 5,000ms ポーリングにフォールバックする(plans/01 §5 の既定。viewer-shell §2.2 と同一)。
- それ以外(注釈・位置)はリアルタイム同期しない(単一ユーザー・単一画面操作のため)。

### 2.4 セクションの遅延ロード

本文は仮想化せず、**セクション単位の遅延マウント**とする。決定: 目次(`viewer.toc`)の全セクションのプレースホルダ(`min-height: 480px` の空 div、`data-section-id` 付き)を先に並べ、IntersectionObserver(`rootMargin: '1200px 0px'`)が近づいたセクションの `document` + `units` を取得して差し替える。理由: アンカー(注釈・検索・「続きから」)への `scrollIntoView` を単純化しつつ初期ペイロードを抑える(docs/09 §1 p50 2 秒)。ジャンプ先が未ロードの場合はロード完了後にスクロールする。

## 3. コンポーネント分解

### 3.1 コンポーネントツリー

■=1b 所有(`apps/web/src/components/viewer/translation/` 配下。AnnotationsTab のみ `components/viewer/panel/`)、◆=viewer-shell 所有(`components/viewer/` 直下。実装・仕様の正は viewer-shell.md — 本書の該当節はピクセル照合用転記)、□=共通(plans/08 §5・§6 の名前)。

```
◆ ViewerPage(page.tsx。Server Component: params 解決のみ)
└─ ◆ ViewerShell(client。1a/1b/1c/2a/1h/5a 共通骨格)
   ├─ ◆ ViewerHeader
   │  ├─ ◆ BackButton(‹)
   │  ├─ ◆ PaperTitle(1行省略)
   │  ├─ □ QualityBadge(A/B, 18px)
   │  ├─ □ StatusPill(interactive, md)+ □ Popover(ステータス6値, width 180)
   │  ├─ □ SegmentedControl(表示モード5値, md)
   │  ├─ ◆ StyleSelector(スタイル: 自然訳 ▾)+ □ Popover
   │  ├─ ◆ InPaperSearch(□ SearchBox variant='in-paper' + 結果 □ Popover。viewer-shell §7)
   │  └─ ◆ ViewerOverflowMenu(⋯)
   ├─ ◆ TocRail(折畳レール w44。☰ / □ BookmarkIcon / □ MagnifierIcon)
   │    ※☰ で ◆ TocPane(w232。viewer-shell §5.3)に展開 — 本書では折畳状態のみ描画
   ├─ ■ ViewerBody(本文領域 flex:1。TranslationPane が所有)
   │  ├─ □ ResumeBanner(前回位置バナー)
   │  └─ ■ TranslationPane(訳文モード本文 720px)
   │     ├─ ■ SectionLabel(アブストラクト — Abstract)
   │     ├─ ■ SummaryCard(✦3行要約。□ AiMark / □ AIBadge(generated)/ □ Card)
   │     ├─ ■ SectionHeading(1 はじめに — Introduction)
   │     ├─ ■ TranslatedParagraph × n(□ HighlightMark / ■ CitationLink / ■ ParallelToggleButton(対))
   │     ├─ ■ ParallelPop(対訳ポップ。□ Keycap / ■ RetranslateFooter / ■ ProposalReview)
   │     └─ □ SelectionMenu(+ ■ CopySubmenu)
   └─ ◆ SidePanel(注釈タブ時 w320。他タブ 340。viewer-shell §6.2)
      ├─ □ SidePanelTabs(6タブ+件数)
      └─ ■ AnnotationsTab(注釈タブ。components/viewer/panel/AnnotationsTab.tsx)
         ├─ ■ AnnotationFilterChips(□ FilterChip size='sm' × 5)
         ├─ ■ AnnotationList(■ AnnotationCard × n / □ EmptyState)
         └─ ■ AnnotationsFooter(未配置 n 件 / ⤓ Markdown エクスポート)
```

### 3.2 画面固有コンポーネントの props 型

`ViewerShell` の props は viewer-shell §1.3(`{ itemId, mode, children, leftPane? }`)が正であり、本書では再定義しない。1b 所有分:

```ts
// components/viewer/translation/TranslationPane.tsx
interface TranslationPaneProps {
  itemId: string;
  revisionId: string;
  style: 'natural' | 'literal';
  toc: TocNode[];                   // plans/03 §6.1
}

// components/viewer/translation/SummaryCard.tsx
interface SummaryCardProps {
  lines: string[];                  // LibraryItemSummary.summary_3line(要素3)
  onDetailedSummary: () => void;    // panel=chat + QuickAction 'detailed_summary'
}

// components/viewer/translation/TranslatedParagraph.tsx
interface TranslatedParagraphProps {
  block: Block;                     // 原文ブロック(type='paragraph')
  unit: TranslationUnit | null;     // null=未翻訳(原文+「翻訳中…」)
  annotations: Annotation[];        // このブロックにアンカーされた placed 注釈
  annotationNumbers: ReadonlyMap<string, number>; // annotation.id → 文書順連番
  popOpen: boolean;
  onTogglePop: () => void;
  onCitationClick: (refId: string) => void;      // panel=figures 参考文献展開(docs/04 §7)
  onAnnotationChipClick: (annotationId: string) => void; // 注釈タブ該当カードへ
}

// components/viewer/translation/ParallelPop.tsx
interface ParallelPopProps {
  paragraphDisplay: string;         // '¶2 / 1 Introduction'(サーバー導出 display から組立)
  sourceInlines: Inline[];          // 原文インライン列(citation/ref 含む)
  unit: TranslationUnit;            // proposal 判定に使用
  onClose: () => void;
  onRetranslate: (instruction?: string) => void; // instruction 有=指示つき再翻訳
  onProposalAccept: () => void;
  onProposalDiscard: () => void;
}

// components/viewer/panel/AnnotationsTab.tsx
// props なし(viewer-shell §6.5 の契約: タブ本体は useViewerStore() と useParams() から
// itemId / revisionId を取得する)。本文へのジャンプは viewer-store の
// requestScroll({ kind:'block', blockId }) 経由(強調は §5.7)。
type AnnFilter = 'all' | 'important' | 'question' | 'idea' | 'with_comment';

// components/viewer/panel/AnnotationCard.tsx
interface AnnotationCardProps {
  annotation: Annotation;           // plans/03 §8.1
  sectionDisplay: string;           // '§2.1 整流フロー'(anchor.display から)
  onClick: () => void;
}

// components/viewer/translation/SelectionController.tsx(SelectionMenu の配置・アクション束ね)
interface SelectionState {
  anchor: Anchor;                   // plans/03 §1.7。side は選択元から決定
  rect: DOMRect;                    // 選択範囲の境界(メニュー配置用)
  contextSentence: string | null;   // side='source' のみ。語彙用センテンス
}
interface SelectionControllerProps {
  itemId: string;
  revisionId: string;
  onAskAI: (anchor: Anchor, quote: string) => void; // panel=chat へ
}
```

クライアント状態: シェル横断状態(`tocOpen` / `panelOpen` / `activeTab` / `style` / `currentBlockId` / `pendingScrollTarget` / `searchOpen` 等)は viewer-shell §2.3 の `useViewerStore`(`stores/viewer-store.ts`)が正で、1b では再定義しない。1b 固有の UI 状態は別ストア `useTranslationPaneStore`(`apps/web/src/stores/translation-pane-store.ts`。決定)に置く:

```ts
interface TranslationPaneState {
  openPopBlockId: string | null;        // 対訳ポップを開いている段落(同時に1つ。排他)
  hoveredBlockId: string | null;        // 「対」ボタン表示対象
  selection: SelectionState | null;     // null=選択メニュー非表示
  annFilter: AnnFilter;                 // 既定 'all'
  bannerDismissed: boolean;             // sessionStorage 'yk-resume-dismissed:{liId}' と同期
}
```

## 4. レイアウト・スタイル完全仕様

出典: extract/1b.md(デザイン HTML `div#1b` 行 1472〜1625)。本節の値・文言が正。フォント指定のない要素は `--pr-font-ui`('IBM Plex Sans JP')。**アクセントトークンの表記規則(決定)**: 本書中の `var(--pr-a)` / `var(--pr-as)` / `var(--pr-am)` は抽出原文の生トークン表記であり、実装では必ず semantic エイリアス `var(--pr-acc)` / `var(--pr-acc-s)` / `var(--pr-acc-m)`(plans/08 §2。ダークで `--pr-ad` 系へ切替わる)を使う。生トークンの直書きは禁止(§6.1 最終項のダーク追随要件のため)。

### 4.0 デザイナー注記(フレーム外。実装対象外だが照合用に転記)

- バッジ「1b」: `a[href="#1b"]`、inline-flex 中央寄せ、min-width:32px、height:22px、背景 #2B2E33、文字 #FFFFFF、border-radius:6px、font-size:12px、font-weight:700、text-decoration:none。
- タイトル(太字)「論文ビューア — 訳文モード・ゆったり」: font-size:15px、font-weight:700、color:#1E2227。説明文(グレー)「対訳ポップ+選択メニュー+前回位置バナー / サイドパネル=注釈一覧」: font-size:12px、color:#777B81。3 要素の行: flex、align-items:baseline、gap:10px、margin-bottom:12px。ルート div: `id="1b"`、`data-screen-label="1b ビューア訳文ゆったり"`、width:1440px。
- HTML コメント(実装ヒント): 「折畳目次レール」「本文(訳文・1カラム)」「前回位置バナー」「3行要約カード」「段落: ホバーで対訳アイコン」「対訳ポップ(インライン展開)」「選択メニュー」「サイドパネル: 注釈一覧」。フレーム外の別置きバリエーション要素はこの画面にはない(選択メニュー等はすべてフレーム内に絶対配置)。

### 4.1 フレームとレイアウト構造

デザインフレーム: 1440×900px、background:#FBFAF7(`--pr-bg-app`)、border:1px solid #D6D3C9、border-radius:10px、box-shadow:0 20px 44px rgba(28,30,34,0.12)、overflow:hidden、flex 縦、color:#1E2227(`--pr-text`)、position:relative。実アプリではフレーム装飾(border/radius/shadow)を除きビューポート全面に描画する(plans/08 §7.1)。

```
┌────────────────────────────────────────────────────────── 1440 ──┐
│ ヘッダーバー h52 #FFFFFF border-bottom:1px #E6E3DA               │
│ ‹  タイトル  [A]  (● 読んでいる ▾)   [訳文|対訳|原文|PDF|記事] │
│                      スタイル: 自然訳▾  [🔍 この論文内を検索 /] ⋯│
├──44──┬───────────────── 本文領域(flex:1)──────────┬── 320 ──┤
│ 目次  │        ⟨前回位置バナー(浮遊・上中央)⟩        │サイド    │
│ レール │   ┌── 本文カラム 720px 中央寄せ ──┐         │パネル    │
│ #F7F6F2│   │ アブストラクト — Abstract      │         │#FFFFFF   │
│ ☰     │   │ [3行要約カード]                │         │タブ行    │
│ 🔖    │   │ 訳文段落(Abstract)            │         │フィルタ  │
│ 🔍    │   │ 1 はじめに — Introduction      │         │チップ行  │
│       │   │ 段落¶2(左に「対」ボタン)     │         │注釈カード│
│       │   │ [対訳ポップ + 選択メニュー]    │         │×5(縦) │
│       │   │ 段落¶3                        │         │フッター  │
└───────┴────────────────────────────────────┴──────────┘
```

- ヘッダ: height:52px、flex:none、background:#FFFFFF(`--pr-bg-card`)、border-bottom:1px solid #E6E3DA(`--pr-border-header`)、flex、align-items:center、gap:10px、padding:0 16px。
- 本体行: flex:1、display:flex、min-height:0。
- 折畳目次レール: width:44px、flex:none、background:#F7F6F2(`--pr-bg-pane`)、border-right:1px solid #E7E4DB(`--pr-border-pane`)、flex 縦・align-items:center、padding:12px 0、gap:14px。
- 本文領域: flex:1、min-width:0、position:relative、display:flex、justify-content:center、overflow:hidden(実装では内側スクロールコンテナに `overflow-y:auto` を持たせる。デザインは 1 画面切り出しのため hidden)。内側の本文カラム: width:720px、padding:64px 0 0、font-family:var(--pr-jp,'Noto Serif JP'),serif。
- サイドパネル: width:320px、flex:none、background:#FFFFFF、border-left:1px solid #E7E4DB、flex 縦。
- 1440px 超/未満のフルード規則は plans/08 §7.2 に従う(レール 44px 固定、本文カラム中央寄せ維持、アプリ最小幅 1200px)。
- **パネル幅と本文カラム幅の連動(viewer-shell §6.2 が正)**: 注釈タブ時のみパネル 320px+本文カラム 720px(本画面の状態)。他タブ時はパネル 340px+本文カラム 680px。`TranslationPane` が `panelWidth` を参照して切り替える。

### 4.2 ヘッダーバー(ViewerHeader, h=52px)

左から順に(gap:10px):

1. 戻る記号「‹」(BackButton): font-size:16px、color:#8A8E94(`--pr-text-icon`)、width:20px、text-align:center。クリックで `router.back()`(履歴が無ければ `/library` へ。決定)。
2. 論文タイトル: 「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」— font-size:13px、font-weight:600、max-width:330px、white-space:nowrap、overflow:hidden、text-overflow:ellipsis(1 行省略)。データ: `viewer.library_item.paper.title`。
3. 「A」バッジ = □ QualityBadge(level='A', size=18): 18×18px、inline-flex 中央、border-radius:4px、background:var(--pr-as)、color:var(--pr-a)、font-size:10.5px、font-weight:700。`title="品質レベルA: LaTeXソースから完全構造化"`。データ: `viewer.library_item.quality_level`。※抽出は「グループ/コレクション印」と注記するが、docs/04 §1 で品質バッジと確定済み(スタイル値は同一)。
4. 読書ステータスピル = □ StatusPill(size='md', interactive): inline-flex、gap:5px、height:24px、padding:0 9px、border:1px solid #DDD9CF(`--pr-border-control`)、border-radius:999px、font-size:11.5px、font-weight:500、background:#FFFFFF。内容: ドット(7×7px、border-radius:50%、background:var(--pr-a) ※reading=アクセント連動)+ テキスト「読んでいる」+「▾」(color:#9A9EA4、font-size:9px)。クリックで □ Popover(width 180、6 値)→ 選択で `PATCH /api/library-items/{id}`(`done` 選択時のみ PATCH 前に読了フローモーダル 1g を経由。viewer-shell §4.2-4)。
5. スペーサー(flex:1)。
6. 表示モードセグメント = □ SegmentedControl(size='md', 5 値): 外枠 background:#EFEDE6(`--pr-bg-muted`)、border-radius:7px、padding:2px、gap:2px。各セグメント height:24px、inline-flex 中央、padding:0 11px、border-radius:5px、font-size:11.5px。
   - 「訳文」(選択中): background:#FFFFFF、color:#1E2227、font-weight:600、box-shadow:0 1px 2px rgba(28,30,34,0.10)(`--pr-shadow-seg`)。
   - 「対訳」「原文」「PDF」「記事」(非選択): color:#5B6067(`--pr-text-sub`)、背景なし。
   - onChange で `?mode=` を書き換え(`parallel|source|pdf|article`)。
7. スタイルセレクタ(StyleSelector): 「スタイル: 自然訳」+「▾」(color:#9A9EA4、font-size:9px)。inline-flex、gap:5px、height:26px、padding:0 10px、border:1px solid #DDD9CF、border-radius:6px、font-size:11.5px、color:#3C4046(`--pr-text-mid`)。クリックで □ Popover(width 180 と決定)に「自然訳」「直訳」の 2 項目(font-size:12px、padding:8px 12px、選択中は color:var(--pr-acc)・weight 600)。「直訳」選択で TranslationSet 未生成なら `POST /api/revisions/{revision_id}/translations { style:"literal", priority_section_id: 表示中セクション }`。
8. 論文内検索 = □ SearchBox(variant='in-paper'): inline-flex、gap:6px、height:26px、padding:0 10px、background:#F1EFE9(`--pr-bg-inset`)、border-radius:6px、font-size:11.5px、color:#8A8E94、width:150px。内容: □ MagnifierIcon 11×11(viewBox 0 0 12 12、circle cx5 cy5 r3.6 stroke currentColor 幅 1.3+ハンドル M8 8→l2.6 2.6 stroke-linecap:round)+ プレースホルダ「この論文内を検索」+ 右端 □ Keycap「/」(margin-left:auto、border:1px solid #DAD7CD(`--pr-border-keycap`)、border-radius:3px、padding:0 4px、font-size:9.5px、background:#FFFFFF)。
9. オーバーフローメニュー「⋯」(◆ ViewerOverflowMenu。仕様の正は viewer-shell §4.2-9): font-size:15px、color:#5B6067、letter-spacing:1px。クリックで □ Popover(width 200、placement 'bottom-end'、caret なし)。項目 5 つ(viewer-shell の確定に従う。各行 h30px、padding 0 12px、font-size:11.5px、color:`--pr-text-mid`): 「サイドパネルを表示/隠す」「注釈 Markdown ⤓」「原文 PDF ⤓」(PDF なし論文では非表示)「再取り込み」「処理ログ」。

### 4.3 折畳目次レール(TocRail, w=44px)

上から縦に(gap:14px、padding:12px 0):

1. 「☰」(ハンバーガー/目次展開): font-size:13px、color:#5B6067。クリックで目次ペイン(w232、1a 仕様)へ展開。
2. □ BookmarkIcon(10×12、viewBox 0 0 10 12、path `M1 1h8v10L5 8.5 1 11V1z` fill:currentColor=下端 V 字のしおり形): color:#9A9EA4。クリックで目次ペインを展開しブックマーク行を強調(決定)。
3. □ MagnifierIcon(12×12、円 r3.6+ハンドル、stroke 幅 1.3): color:#9A9EA4。クリックでヘッダ検索ボックスへフォーカス(決定: `/` キーと同一挙動)。

### 4.4 前回位置バナー(□ ResumeBanner, 浮遊)

position:absolute、top:14px、left:50%、transform:translateX(-50%)、z-index:var(--z-banner)(=3)。flex、align-items:center、gap:12px。background:#FFFFFF、border:1px solid #DDD9CF、border-radius:999px(ピル形)、padding:7px 8px 7px 16px、box-shadow:0 8px 24px rgba(28,30,34,0.10)(`--pr-shadow-banner`)。

- テキスト: 「前回はここまで: **§3.1 実験** · 昨日 21:52」— font-size:12px、color:#3C4046。「§3.1 実験」は `<b>`(太字)、「· 昨日 21:52」は color:#9A9EA4。データ: `viewer.last_position.section_display` と `saved_at`(相対表記規則は §5.11)。
- ボタン「続きから ↓」: inline-flex、height:24px、padding:0 12px、border-radius:999px、background:var(--pr-a)、color:#FFFFFF、font-size:11.5px、font-weight:600。クリックで `last_position.block_id` へスクロール(§5.2)。
- 閉じる「×」: font-size:12px、color:#9A9EA4、padding:0 6px。

### 4.5 本文カラム(TranslationPane, 720px, Noto Serif JP)

1. **セクション見出しラベル**(SectionLabel): 「アブストラクト — Abstract」— font-family:'IBM Plex Sans JP'、font-size:12px、color:#9A9EA4、margin-bottom:8px。「— Abstract」部分は font-style:italic、font-family:'Source Serif 4',Georgia,serif(`--pr-font-en`)。データ: `TocNode.title_ja` + `title_en`。
2. **3行要約カード**(SummaryCard): background:#FFFFFF、border:1px solid #E2DFD5(`--pr-border-card`)、border-radius:10px、padding:16px 20px、margin-bottom:26px、font-family:'IBM Plex Sans JP'。
   - ヘッダー行(flex、align-items:center、gap:7px、margin-bottom:10px):
     - 「✦ 3行要約」: color:var(--pr-a)、font-size:11.5px、font-weight:700(✦ は □ AiMark)。
     - 「AI生成」バッジ = □ AIBadge(variant='generated'): height:15px、padding:0 5px、border:1px solid #DDD9CF、border-radius:3px、font-size:9px、color:#8A8E94、font-weight:600。
     - 「詳細要約 →」リンク: margin-left:auto、font-size:10.5px、color:var(--pr-a)。
   - 本文(flex 縦、gap:7px、font-size:13px、line-height:1.75、color:#33373C)。各行は flex、gap:9px、番号(color:#9A9EA4、font-weight:600)+本文。デモデータ(シード論文 arXiv:2209.03003):
     - 1 「2分布間を直線経路で結ぶODE(整流フロー)を、単純な最小二乗回帰で学習する生成モデルを提案。」
     - 2 「「reflow」の反復で輸送経路がほぼ直線化し、極少ステップ・1ステップでの生成が可能になる。」
     - 3 「CIFAR-10 の画像生成と画像間変換で、拡散モデルに匹敵する品質をより少ない計算量で達成。」
   - データ: `viewer.library_item.summary_3line`(要素 3。番号は表示側で付与)。
3. **アブストラクト訳文段落**: font-size:16.5px、line-height:2.15、color:#24272B(`--pr-text-body`)、margin-bottom:22px。デモ本文:
   「本研究では、2つの経験的に観測された分布 *π*₀, *π*₁ の間の輸送問題に対する驚くほど単純な学習手法、整流フロー(rectified flow)を提案する。整流フローは、*π*₀ と *π*₁ を可能な限り直線に近い経路で結ぶ常微分方程式(ODE)モデルであり、生成モデリングとドメイン転移の双方に統一的に適用できる。」
   - 「π」は `<i>`、添字 0/1 は `<sub>`。
   - 「整流フロー(rectified flow)」にグレー(用語)ハイライト = □ HighlightMark(color='term'): background:rgba(130,130,126,0.18)(`--pr-ann-term-bg`)、border-radius:2px、padding:0 1px。
4. **セクション見出し**(SectionHeading): 「1 はじめに — Introduction」— font-family:'IBM Plex Sans JP'、font-size:19px、font-weight:700、margin:30px 0 14px、color:#1E2227。「— Introduction」は color:#8A8E94、font-weight:400、font-size:14px、font-family:'Source Serif 4',Georgia,serif、font-style:italic。
5. **段落 ¶2(TranslatedParagraph。ホバー中想定・左に対訳アイコン)**: position:relative、font-size:16.5px、line-height:2.15、color:#24272B、margin-bottom:8px(ポップ展開中の下マージン。通常時は 22px。§5.3)。
   - 左マージンの「対」ボタン(ParallelToggleButton): position:absolute、left:-42px、top:6px、26×26px、border-radius:6px、border:1px solid #DDD9CF、background:#FFFFFF、color:var(--pr-a)、font-size:11px、font-weight:600、box-shadow:0 2px 6px rgba(28,30,34,0.08)(`--pr-shadow-float`)。ラベルは漢字「対」。
   - デモ本文: 「生成モデリングとドメイン転移は、いずれも「ある分布から別の分布への写像を見つける」問題として定式化できる。拡散モデルはこの写像を確率微分方程式(SDE)の解として構成するが、生成には多数の反復ステップを要する②。一方、GAN [8] は1ステップで生成できるが、学習が不安定である。」
     - 「拡散モデルは…反復ステップを要する」にオーカー(重要)ハイライト = □ HighlightMark(color='important'): background:rgba(196,148,50,0.26)、border-radius:2px、padding:0 1px。
     - ハイライト直後に注釈番号バッジ「2」(HighlightMark の annotationNumber): 14×14px、inline-flex 中央、border-radius:50%、background:rgba(196,148,50,0.30)(`--pr-ann-important-chip-bg`)、color:#8A6A24(`--pr-ann-important-chip-fg`)、font-size:9px、font-weight:700、vertical-align:4px、margin-left:2px。
     - 引用「[8]」(CitationLink): color:var(--pr-a)、font-weight:600。
6. **対訳ポップ(ParallelPop, インライン展開)**: border:1px solid #E2DFD5、border-left:3px solid var(--pr-a)、background:#FFFFFF、border-radius:8px、padding:14px 18px、margin:0 0 22px、position:relative。
   - ヘッダー行: font-family:'IBM Plex Sans JP'、font-size:10.5px、color:#9A9EA4、margin-bottom:6px、flex、gap:8px。テキスト: 「原文」+「¶2 / 1 Introduction」(color:#B6BAC0=`--pr-text-faint`)+右端「閉じる ×」(margin-left:auto、color:#9A9EA4)。
   - 原文英語: font-family:'Source Serif 4',Georgia,serif、font-size:14.5px、line-height:1.8、color:#33373C(`--pr-text-en`)。デモ本文: 「Generative modeling and domain transfer can both be formulated as finding a transport map between two distributions. Diffusion models construct this map as the solution of a stochastic differential equation (SDE), but require many iterative steps to generate. GANs generate in a single step but are unstable to train.」
     - 中央文「Diffusion models … to generate」に選択ハイライト: background:var(--pr-as)、outline:1px solid var(--pr-am)、border-radius:2px(ブラウザ選択とは別の、選択メニュー表示中の擬似選択スタイル。§5.5)。
   - **選択メニュー(□ SelectionMenu, ダークフローティングツールバー)**: position:absolute、top:34px、left:210px(デザイン上の座標。実装では選択矩形からの動的配置。§5.5)、z-index:var(--z-selection-menu)(=4)。flex、align-items:center、gap:2px、background:#26292E(`--pr-elev-bg`)、border-radius:8px、padding:5px 7px、box-shadow:0 10px 28px rgba(20,22,26,0.35)(`--pr-shadow-menu`)。
     - 色ドット 4 個(各 15×15px、border-radius:50%、margin:0 2px): #C49432(オーカー=重要)/ #5884AA(青=疑問)/ #659471(緑=アイデア)/ #82827E(グレー=用語)。
     - 区切り線: width:1px、height:14px、background:#4A4E55(`--pr-elev-divider`)、margin:0 5px。
     - テキストアクション(各 font-size:11.5px、color:#E8E6E1(`--pr-elev-fg`)、padding:0 6px): 「コメント」「✦ AIに質問」「語彙に追加」「コピー」。
   - フッター行(RetranslateFooter): font-family:'IBM Plex Sans JP'、flex、gap:14px、font-size:10.5px、color:#8A8E94、margin-top:10px、padding-top:8px、border-top:1px solid #F0EDE4(`--pr-border-hair`)。項目: 「訳がおかしい?」(color:var(--pr-a)、font-weight:600)/「再翻訳」/「指示つき再翻訳」/ 右端「t で開閉」(margin-left:auto、font-family:'IBM Plex Mono',monospace=`--pr-font-mono`)。
7. **段落 ¶3**: position:relative、font-size:16.5px、line-height:2.15、color:#24272B。デモ本文: 「本研究の鍵となる観察は、直線経路は1回のオイラーステップで厳密にシミュレートできるため、経路を直線に近づけることが推論コストの削減に直結する、という点である。この考えに基づき、我々は…」
   - 「直線経路は1回のオイラーステップで厳密にシミュレートできる」に青(疑問)ハイライト = □ HighlightMark(color='question'): background:rgba(88,132,170,0.22)、border-radius:2px、padding:0 1px。

### 4.6 サイドパネル(w=320px, 注釈一覧)

1. **タブ行** = □ SidePanelTabs: flex、border-bottom:1px solid #ECE9DF(`--pr-border-soft`)、padding:0 6px。各タブ padding:10px 9px 8px、font-size:12px。
   - 非アクティブ(color:#777B81=`--pr-text-sub2`): 「チャット」「メモ」「図表」「情報」、「リソース 4」(件数「4」は □ CountBadge(variant='tab'): font-size:10px、color:#9A9EA4)。
   - アクティブ: 「注釈 6」— font-weight:600、color:var(--pr-a)、box-shadow:inset 0 -2px var(--pr-a)(下線インジケータ)。件数「6」は font-size:10px。
   - 件数データ: `viewer.counts.annotations` / `viewer.counts.resources`。
2. **フィルタチップ行**(AnnotationFilterChips): flex、flex-wrap:wrap、gap:5px、padding:10px 12px、border-bottom:1px solid #F0EDE4。各チップ = □ FilterChip(size='sm'): height:20px、padding:0 8px、border-radius:999px、font-size:10.5px。
   - 「すべて 6」(選択中): background:#26292E(`--pr-elev-bg`)、color:#FFFFFF、font-weight:600、枠なし。
   - 「重要 3」: border:1px solid #DDD9CF、color:#3C4046、gap:4px、先頭に色ドット 7×7px #C49432。
   - 「疑問 2」: 同上、ドット #5884AA。
   - 「アイデア 1」: 同上、ドット #659471。
   - 「コメントのみ」: 同枠スタイル、ドットなし。
   - 件数データ: `GET …/annotations` の `counts`(all / important / question / idea)。「コメントのみ」は件数非表示(デザイン準拠)。
3. **注釈リスト**(AnnotationList): flex:1、overflow:hidden(実装は `overflow-y:auto`。デザインは切り出しのため hidden)、padding:10px 12px、flex 縦、gap:8px、background:#FCFBF8(`--pr-bg-feed`)。
   - カード共通(AnnotationCard): background:#FFFFFF、border:1px solid #E2DFD5、border-radius:8px、padding:9px 11px、flex 横、gap:9px。左端に縦色バー(width:3px、border-radius:2px、flex:none、色=注釈色)。右側は flex 縦、gap:4px(コメント付きカードは gap:5px)、min-width:0。
   - 引用テキスト: font-family:var(--pr-jp,'Noto Serif JP'),serif、font-size:12px、line-height:1.7、color:#33373C。
   - コメント(ある場合): font-size:11.5px、line-height:1.65、color:#24272B、background:#F7F5EF(`--pr-bg-comment`)、border-radius:5px、padding:6px 8px、先頭に絵文字「💬」。
   - メタ行: font-size:10px、color:#9A9EA4、形式「§番号 セクション名 · 相対日時」。
   - デモカード 5 枚(上から):
     1. バー #C49432 / 「「拡散モデルは…生成には多数の反復ステップを要する」」/ メタ「§1 はじめに · 昨日 21:12」
     2. バー #C49432 / 「「線形補間の経路を因果化し」」/ コメント「💬 ここが本質。marginal を保ったまま交差を解消している」/ メタ「§2.1 整流フロー · 昨日 21:40」
     3. バー #5884AA / 「「条件付き期待値 𝔼[X₁−X₀ | Xₜ = z] に等しく」」/ メタ「§2.1 整流フロー · 昨日 21:44」
     4. バー #5884AA / 「「reflow を k 回繰り返すと経路はほぼ直線になる」」/ メタ「§2.2 Reflow · 昨日 22:03」
     5. バー #659471 / 「「蒸留は reflow 後に適用すると効果が大きい」」/ コメント「💬 うちの蒸留パイプラインの前処理に使えそう」/ メタ「§2.3 蒸留との関係 · 今日 8:15」
   - ※タブ・チップは 6 件表示だがリスト描画は 5 枚(6 枚目はスクロール外想定)。実装ではスクロールで全件表示する。
   - データ対応: 色バー=`annotation.color`、引用=`annotation.anchor.quote`(鉤括弧「」で囲んで表示)、コメント=`annotation.comment`、メタ=`anchor.display` のセクション部+`created_at` 相対表記(§5.11)。並び順は文書位置順(placed)→未配置(作成日時降順)と決定。
4. **フッター**(AnnotationsFooter): padding:10px 12px、border-top:1px solid #ECE9DF、flex、align-items:center、justify-content:space-between、font-size:11px。
   - 左: 「未配置 0 件」— color:#9A9EA4。データ: `counts.unplaced`。
   - 右: 「⤓ Markdown エクスポート」— color:var(--pr-a)、font-weight:600。`GET /api/library-items/{id}/export/annotations` へのアンカーリンク(`download` 属性)。

### 4.7 全 UI 文言(逐語・照合リスト)

- ヘッダ: 「‹」「Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow」「A」「読んでいる」「▾」「訳文」「対訳」「原文」「PDF」「記事」「スタイル: 自然訳」「▾」「この論文内を検索」「/」「⋯」
- 目次レール: 「☰」(+ブックマーク SVG、虫眼鏡 SVG)
- 前回位置バナー: 「前回はここまで: §3.1 実験 · 昨日 21:52」「続きから ↓」「×」
- 本文: 「アブストラクト — Abstract」「✦ 3行要約」「AI生成」「詳細要約 →」、要約 3 行(§4.5-2)、アブストラクト訳文(§4.5-3)、「1 はじめに — Introduction」「対」、段落 ¶2 本文+丸バッジ「2」、対訳ポップ「原文」「¶2 / 1 Introduction」「閉じる ×」、原文英語(§4.5-6)、選択メニュー「コメント」「✦ AIに質問」「語彙に追加」「コピー」、ポップフッター「訳がおかしい?」「再翻訳」「指示つき再翻訳」「t で開閉」、段落 ¶3 本文
- サイドパネル: タブ「チャット」「メモ」「注釈 6」「図表」「リソース 4」「情報」、チップ「すべて 6」「重要 3」「疑問 2」「アイデア 1」「コメントのみ」、カード引用 5 件・コメント 2 件・メタ 5 件(§4.6-3)、フッター「未配置 0 件」「⤓ Markdown エクスポート」

## 5. 状態とインタラクション

### 5.1 デザインに描かれた状態(再現必須)

1. 表示モード「訳文」選択中(白背景+`--pr-shadow-seg`)、他 4 モード非選択。
2. 読書ステータス「読んでいる」(アクセント色ドット+▾)。
3. 前回位置バナーが本文上部中央に浮遊(復帰導線+閉じる)。
4. 段落 ¶2 がホバー状態: 左マージン −42px に「対」ボタン出現。
5. 対訳ポップが ¶2 直下にインライン展開(左アクセント 3px 縦帯付きカード)。
6. ポップ内原文の一文が選択中(`--pr-as` ハイライト+`--pr-am` 枠)で、ダーク選択メニューが z-index:4 で浮遊。
7. 本文ハイライト 3 種描画: グレー rgba(130,130,126,0.18) / オーカー rgba(196,148,50,0.26)+丸バッジ「2」/ 青 rgba(88,132,170,0.22)。
8. サイドパネル「注釈」タブがアクティブ(アクセント色+inset 下線)。
9. フィルタチップ「すべて 6」選択中(黒地白抜き)。
10. 注釈カード 2 種: 引用のみ / 引用+コメント(薄ベージュ #F7F5EF ボックス)。
11. フッター「未配置 0 件」。

### 5.2 前回位置バナー

- 表示条件(決定): `viewer.last_position != null` かつ 未 dismiss(`sessionStorage['yk-resume-dismissed:'+liId]` 不在)かつ 初期スクロール位置が last_position のブロックと不一致。自動ジャンプはしない(docs/04 §3)。
- 「続きから ↓」: `last_position.block_id` のセクションをロード(§2.4)→ `scrollIntoView({ block:'start' })`+スクロール後に該当ブロックを 2000ms `--pr-acc-s` 背景で強調(transition 300ms)→バナーを閉じる。
- 「×」: バナー非表示+sessionStorage 記録(同一タブセッション中は再表示しない。次回訪問では再表示)。
- スクロールを 1200px 以上進めた時点でも自動で閉じる(決定。読み始めた=不要のため)。フェードアウト 150ms。

### 5.3 対訳ポップ(段落対訳)

- 「対」ボタン表示: 段落(`type='paragraph'` のブロック)の `mouseenter` で表示、`mouseleave` で非表示(遅延 0ms、transition: opacity 120ms)。タッチ環境ではタップで表示(v1 モバイルは docs/04 §13 に従い縮退)。
- 開閉: 「対」クリック or キー `t`。`t` の対象段落(決定): ホバー中の段落があればそれ、無ければビューポート上端に最も近い可視段落。開くと `openPopBlockId` にセット(**同時に 1 段落のみ**。別段落で開くと前のポップは閉じる。決定)。再度 `t` / 「閉じる ×」で閉じる。開閉アニメーションなし(デザインに存在しないため。決定)。
- 開いている段落の margin-bottom は 22px→8px(ポップがその下 22px マージンを持つ)。
- ヘッダの位置表示「¶2 / 1 Introduction」: `¶` + セクション内段落序数 + ` / ` + セクション見出し(番号+原題 `title_en`)。序数はセクション内 paragraph ブロックの 1 始まり連番(クライアント導出)。
- 原文はブロックの `inlines` を描画(citation はアクセントリンク、太字等のインライン強調保持)。ポップ内の原文でも選択メニューが使える(§5.5。side='source')。
- 再翻訳(RetranslateFooter):
  - 「再翻訳」: `POST /api/translation-units/{unit_id}/retranslate {}` → 202 → ジョブ SSE 購読。実行中はフッタ右側に「再翻訳中…」(font-size:10.5px、color:#8A8E94)を表示し、「再翻訳」「指示つき再翻訳」を opacity:0.5・非活性化(決定)。
  - 「指示つき再翻訳」: クリックでフッタ直下に入力行を展開(決定: height:28px のテキスト入力、border:1px solid #DDD9CF、border-radius:6px、font-size:11.5px、padding:0 10px、placeholder「翻訳への指示(例: もっと簡潔に)」+ 右に「実行」ボタン h24px・bg var(--pr-a)・白・radius 6px・font 11px/600)。Enter or 実行で `retranslate { instruction }`。
  - `state='edited'` のユニットは 409(`edit_protected`)になるため、実行前に確認 UI(決定): フッタに「編集済みの訳です。破棄して再翻訳しますか? [破棄して再翻訳] [キャンセル]」(font-size:10.5px、color:`--pr-warn`)を表示し、確定時 `discard_edit:true` を付与。
  - **proposal 表示(ProposalReview)**(決定。plans/03 §7.6「差分表示→採用の UI 前提」の具体化): `unit.proposal != null` のときポップ下部(フッタ上)に提案ブロックを表示 — border-top:1px solid #F0EDE4、padding-top:10px、margin-top:10px。ラベル行「✦ 再翻訳案」(color:var(--pr-a)、font-size:10.5px、weight 700)+ 提案訳文(font-family:var(--pr-jp)、font-size:13px、line-height:1.9、color:#24272B、background:var(--pr-as)、border-radius:6px、padding:8px 10px)+ アクション行(gap:10px、font-size:10.5px): 「この訳を採用」(color:var(--pr-a)、weight 600 → `POST …/proposal/accept` → `qk.units` invalidate)/「破棄」(color:#8A8E94 → `DELETE …/proposal`)。段落本文は採用まで変更しない。

### 5.4 キーボードショートカット(この画面で有効)

キー登録は viewer-shell §10 の `useViewerKeymap` が一元で行う(1b は登録しない)。1b が実処理を担うのは `t`(§5.3)と `b`(下記)のみ。

| キー | 動作 |
|---|---|
| `/` | ヘッダ検索ボックスにフォーカス(入力中は無効) |
| `t` | 対訳ポップ開閉(§5.3。シェルが store イベント `toggleBilingualPop` を発火し 1b が消費) |
| `Esc` | 開いている浮遊 UI を 1 つ閉じる(優先順は viewer-shell §10 と同一): 選択メニュー → 検索ドロップダウン → Popover → 対訳ポップ。※コメント入力ポップは textarea フォーカス中のためシェルのキーマップ対象外で、ポップ自身が Esc を処理する(§5.5) |
| `j` / `k` | 次/前の段落へスクロール(docs/04 §14 決定分) |
| `m` | 表示モード循環(訳文→対訳→原文→PDF→記事) |
| `c` | サイドパネルをチャットタブへ |
| `b` | 現在セクションのブックマーク切替(下記) |

- **`b` の実処理(1b 所有。決定)**: 対象セクション=viewer-store の `activeSectionId`(現在位置ハイライトと同一)。当該セクションを参照する `kind='bookmark'` の注釈が既にあれば `DELETE /api/annotations/{annotation_id}`+Toast「ブックマークを解除しました」、無ければ `POST /api/library-items/{id}/annotations { kind:'bookmark', anchor: セクション参照(start/end/quote=null。plans/03 §8.1) }`+Toast「ブックマークしました」。成功で `qk.annotations(liId)`・`qk.viewer(liId)` を invalidate(目次のしおり表示更新)。

いずれも input/textarea フォーカス中と IME 変換中(`isComposing`)は無効。

### 5.5 テキスト選択メニュー(SelectionController + □ SelectionMenu)

- 発火: 本文(訳文)・対訳ポップ内原文での `selectionchange` 終了(`pointerup` / `keyup`)時、選択が空でなく単一ブロック内に収まる場合に表示。複数ブロック跨ぎ選択は非対応(メニューを出さない。決定: アンカーがブロック単位のため)。
- 位置(決定): 選択範囲 `getClientRects()` の最終行矩形の下 8px・左端揃え。本文カラム右端をはみ出す場合は右端に合わせ、ビューポート下端 60px 以内なら選択の上 8px に反転。デザイン値(top:34px / left:210px)はこの規則の一例スナップショット。
- 選択中の擬似ハイライト: メニュー表示中、対象 Range を `background:var(--pr-as); outline:1px solid var(--pr-am); border-radius:2px` の `<span data-yk-pending>` でラップ(メニュー消滅時に解除)。ブラウザ標準 `::selection` はトークンの rgba(62,92,118,0.22) のまま。
- アンカー構築: `{ revision_id, block_id, start, end, quote(選択文字列・最大500字), side }`。side は選択元が対訳ポップ内原文なら `'source'`、訳文段落なら `'translation'`。
- アクション:
  - **色ドット 4 個**: クリックで `POST …/annotations { kind:'highlight', color, anchor }`。楽観的更新(§5.6)。メニューを閉じる。
  - **コメント**: 色選択を兼ねる(決定): クリックでメニュー直下にコメント入力ポップ(width 280px、bg #FFFFFF、border 1px `--pr-border-pop`、radius 8px、shadow `--pr-shadow-pop`、padding 10px。textarea 3 行 font-size:12px + 色ドット 4 個(既定 important 選択)+「保存」ボタン h24 アクセント)を表示。保存で `POST { kind:'highlight', color, anchor, comment }`。textarea 内の `Esc` はポップ自身が処理し、入力ポップのみ閉じて選択メニューへ戻る(入力内容は破棄。決定)。空文字のまま保存した場合は comment なしのハイライトとして作成する(決定)。
  - **✦ AIに質問**: `panel=chat` に切替え、選択アンカー(引用チップ+引用テキスト)を入力コンテキストにプリセット(docs/05)。メニューを閉じる。
  - **語彙に追加**: `side='source'` のみ活性。訳文選択時は非活性(opacity:0.45、cursor:default、`title="原文(英語)の選択でのみ使えます"`。決定)。実行: 選択を含むセンテンスを DOM から抽出し `POST /api/vocab { library_item_id, term, anchor, context_sentence, highlight }` → 成功 Toast「✓ 語彙帳に追加しました」/ 409 duplicate は Toast「既に語彙帳にあります」+アクション「開く」(→ `/vocab/{existing.vocab_id}`)。
  - **コピー**(CopySubmenu。docs/04 §8「引用形式/プレーンを選べる」の具体化。決定): クリックでメニュー右下に同ダークスタイルのサブメニュー(縦 2 項目、各 padding:5px 10px、font-size:11.5px): 「引用形式でコピー」「プレーンでコピー」。引用形式=`"{選択}" — {論文タイトル}, {位置参照}`。位置参照はクライアント導出(決定): 番号つきセクションは `§{セクション番号} ¶{セクション内段落序数}`(例 `§1 ¶2`。序数は §5.3 の導出と同一)、番号なしセクション(アブストラクト等)は `{title_en} ¶{序数}`(例 `Abstract ¶1`)。実行後 Toast「✓ コピーしました」。
- 消滅: 外側クリック / Esc / スクロール 40px 以上 / アクション実行後。

### 5.6 注釈の作成・更新(楽観的更新)

- 作成: `qk.annotations(liId)` のキャッシュへ仮 Annotation(id=`tmp_`+UUID、placed:true)を挿入し、本文ハイライトも即時描画。成功でサーバー値に置換、失敗でロールバック+Toast(error)「× 注釈を保存できませんでした」+アクション「再試行」。
- 注釈番号(丸バッジの数字): placed 注釈を文書順(ブロック順→ブロック内 start 順)に並べた 1 始まり連番をクライアントで導出(決定。API は番号を持たない)。番号は注釈の増減で振り直す。
- 注釈カードのホバー(決定): カード bg を `--pr-bg-hover`(#FAF9F5)に。クリックで §5.7 ジャンプ。カード右上にホバー時のみ「×」(10px、`--pr-text-muted`)を表示し、クリックで削除(DELETE)+Toast「注釈を削除しました」+「元に戻す」(6000ms。POST で再作成)。
- コメント編集(決定): カードのコメント部クリックでインライン textarea 化(同スタイル)、blur or Enter で `PATCH { comment }`。

### 5.7 注釈タブのフィルタとジャンプ

- フィルタ(クライアントサイド。全件ロード済みのため再フェッチしない。決定): `all`=全件 / `important|question|idea`=color 一致 / `with_comment`=`comment != null`。チップ選択は排他 1 つ。件数はサーバー `counts` を表示(フィルタしても件数表示は変えない)。
- フィルタ結果 0 件時は □ EmptyState(title「該当する注釈がありません」、description「フィルタを変更してください」)。
- カードクリック: `anchor.block_id` のセクションをロード→スクロール→対象ハイライトを 2000ms `outline:1px solid var(--pr-am)` で強調。`placed:false`(未配置)のカードはジャンプ不可 — カード全体 opacity:0.6+メタ行末尾に「· 未配置」(color:`--pr-warn`)を表示(決定。P3: 黙って消さない)。
- 本文の丸バッジ「2」クリック: `panel=annotations` に切替え、該当カードへ `scrollIntoView` +カードを 2000ms `--pr-acc-s` 背景で強調。

### 5.8 論文内検索

実装・仕様の正は viewer-shell §7(`InPaperSearch`。シェル所有)。1b は本文側のジャンプ先強調(§5.7 と同じ 2000ms 強調)のみ担当する。シェル仕様の要点(照合用転記): `/` またはクリックで起動、クエリ 2 文字以上・300ms デバウンスで `GET /api/revisions/{revision_id}/search?q=&limit=50`、結果は □ Popover(width 300、placement 'bottom-end'、caret なし)に `display`(10px)+ snippet(11.5px、2 行 clamp、`<mark>`=bg rgba(196,148,50,0.30))、`↓`/`↑`/`Enter` で連続ジャンプ、0 件時は EmptyState 縮小版「一致なし」。ヒットの目次マーカー(docs/04 §12 決定分)は目次ペイン展開時のみ(本画面のレール状態では非表示)。

### 5.9 ローディング・エラー・空状態(デザイン未描画分の決定)

- **初期スケルトン**(決定): ヘッダ・レール・パネル枠は即時描画(タイトル位置に 330×13px、bg `--pr-bg-muted`、radius 4px のバー)。本文カラムに: セクションラベル位置 160×12px バー、3行要約カード枠(border 1px `--pr-border-card`、radius 10px、高さ 118px、内部に 11.5px 相当バー 1 本+13px 相当バー 3 本)、段落位置に幅 720/680/700px×16.5px のバー 3 群(各群 4 本、gap 12px)。すべて `animation: yk-pulse 1.6s ease-in-out infinite`(opacity 1→0.55→1)。「〜px 相当バー」の高さは当該 font-size の四捨五入整数 px(11.5px→h12px、13px→h13px、16.5px→h17px)と決定。パネルには注釈カード形スケルトン 3 枚(h64px)。
- **viewer 取得エラー**: 本文領域中央に □ EmptyState(title「論文を読み込めませんでした」、description=Problem.title、action「再読み込み」→ refetch)。404 は `notFound()`(Next.js)。
- **セクション本文/訳文取得エラー**: 該当セクション位置にインラインで再試行行(font-size:12px、color:`--pr-warn`、「このセクションを読み込めませんでした · 再試行」)。
- **未翻訳ユニット**(`text_ja: null`): 原文(`--pr-font-en`、14.5px/1.8、color #33373C)+ 直後に「翻訳中…」(font-size:10.5px、color:#9A9EA4、font-family:'IBM Plex Sans JP')。`quality_flags` に翻訳失敗フラグ **`placeholder_mismatch` / `provider_refusal` / `untranslated` のいずれか**(plans/06 §12: text_ja が null で返る失敗系はこの 3 値のみ)を含む場合は「翻訳中…」の代わりに「この段落の翻訳に失敗しました · 再翻訳」(color:`--pr-warn`、再翻訳リンクは §5.3 と同処理)。それ以外の品質フラグ(`number_mismatch` / `length_outlier` 等の警告系)は訳文をそのまま表示し、docs/03 §10 の下線表示に従う。P3 準拠。
- **summary_3line が null**(生成前): 3行要約カードの本文位置に「✦ 要約を生成しています…」(font-size:12px、color:#9A9EA4)。生成はパイプライン側で自動、`qk.viewer` の SSE 由来 invalidate で反映。
- **注釈 0 件**: リストに □ EmptyState(title「注釈はまだありません」、description「本文を選択して 4 色ハイライトやコメントを付けられます」)。タブバッジは件数 0 のとき数字を出さない(「注釈」のみ。決定)。フッターは「未配置 0 件」を維持。
- **Toast**: すべて □ Toast(plans/08 §5.20。bottom 22px 中央、`--pr-elev-bg`)。

### 5.10 ホバー・フォーカス(デザイン未描画分の決定)

- ヘッダの「‹」「⋯」、レールの 3 アイコン: hover で color を `--pr-text`(#1E2227)へ(transition 120ms)。
- セグメント非選択項目 hover: color `--pr-text`。スタイルセレクタ/ステータスピル hover: border-color `--pr-am`。
- 「詳細要約 →」「訳がおかしい?」「⤓ Markdown エクスポート」等のアクセント色リンク hover: text-decoration:underline。
- SelectionMenu の色ドット hover: `transform:scale(1.15)`(transition 100ms)、テキストアクション hover: bg #33373A・radius 4px。
- フォーカスリングは共通規則(plans/08 §5 共通事項: `outline:1.5px solid var(--pr-acc); outline-offset:1px`)。
- 「対」ボタン hover: border-color var(--pr-am)、bg var(--pr-as)(決定)。

### 5.11 相対日時の表記規則(確定)

メタ行・バナーで共通: 当日=「今日 H:mm」/ 前日=「昨日 H:mm」/ それ以前の同年=「M/D H:mm」/ 前年以前=「YYYY/M/D」。0 埋めなし(21:05 のような分のみ 0 埋め)。実装は `features/shared/formatRelativeDay.ts` に一元化し、1a/1d 等と共用する。

### 5.12 位置・時間の計測

実装の正はシェル所有フック(viewer-shell §8 の `useReadingPosition` / `useReadingSession`)。1b の責務は先頭可視ブロック(viewport top を最初に跨ぐブロック)の追跡と `setCurrentBlock` 通知のみ。シェル仕様の要点(照合用転記):

- 位置保存: `currentBlockId` 変化から 5000ms デバウンスで `PUT …/position { revision_id, block_id, mode }`。`pagehide` 時は `navigator.sendBeacon` で即時送信。
- 読書時間: `client_session_id`(マウント時 `crypto.randomUUID()`)で `POST …/reading-sessions` を 60 秒間隔+`visibilitychange(hidden)`+`pagehide`(`navigator.sendBeacon`)で送信。アクティブ判定はタブ前面かつ直近 60 秒に pointer/scroll/key イベントあり。設定 `reading.track_reading_time=false` なら計測・送信しない。

## 6. 受け入れ基準

### 6.1 ピクセル一致検証(ビジュアルリグレッション対象)

Playwright + Storybook VRT(plans/08 §9)。ビューポート 1440×900、シードデータ(arXiv:2209.03003、デモ注釈 6 件)で以下のスナップショットが確定デザイン `div#1b` と一致すること:

- [ ] 画面全景: ヘッダ h52 / レール w44 / 本文カラム w720(padding-top 64px)/ パネル w320 の 4 領域構成、背景 #FBFAF7
- [ ] ヘッダ: タイトル max-width 330px 省略、QualityBadge 18×18(--pr-as/--pr-a)、StatusPill h24(ドット 7px アクセント+「読んでいる ▾」)、セグメント「訳文」選択(白+shadow-seg)/他 4 項 #5B6067、スタイルセレクタ h26、検索ボックス w150 h26(#F1EFE9+Keycap「/」)、「⋯」15px
- [ ] 前回位置バナー: top14px 中央、ピル形、padding 7px 8px 7px 16px、shadow-banner、z=3、「§3.1 実験」太字・「· 昨日 21:52」#9A9EA4、CTA h24 アクセント白「続きから ↓」
- [ ] 3行要約カード: radius 10px・padding 16px 20px・margin-bottom 26px、「✦ 3行要約」11.5px/700 アクセント、AI生成バッジ h15 9px、「詳細要約 →」10.5px、本文 13px/1.75 #33373C・番号 #9A9EA4
- [ ] 本文タイポグラフィ: 訳文 16.5px/2.15 #24272B Noto Serif JP、セクションラベル 12px #9A9EA4+「— Abstract」italic Source Serif 4、見出し 19px/700+「— Introduction」14px/400 italic #8A8E94
- [ ] ハイライト 3 種: term rgba(130,130,126,0.18) / important rgba(196,148,50,0.26)+丸バッジ 14×14(bg 0.30・#8A6A24・9px/700・vertical-align 4px)/ question rgba(88,132,170,0.22)、いずれも radius 2px・padding 0 1px。引用「[8]」アクセント 600
- [ ] 「対」ボタン: left:-42px top:6px、26×26、radius 6、border #DDD9CF、color アクセント、shadow-float
- [ ] 対訳ポップ: border-left 3px アクセント、radius 8、padding 14px 18px、ヘッダ 10.5px(「¶2 / 1 Introduction」#B6BAC0)、原文 14.5px/1.8 Source Serif 4 #33373C、選択ハイライト --pr-as+outline --pr-am、フッタ 10.5px(「訳がおかしい?」アクセント 600、「t で開閉」IBM Plex Mono、border-top #F0EDE4)
- [ ] 選択メニュー: bg #26292E、radius 8、padding 5px 7px、shadow 0 10px 28px rgba(20,22,26,0.35)、z=4、色ドット 15×15×4(#C49432/#5884AA/#659471/#82827E)、区切り 1×14 #4A4E55、テキスト 11.5px #E8E6E1
- [ ] サイドパネル: タブ行(アクティブ「注釈 6」アクセント+inset -2px 下線、件数 10px)、フィルタチップ行(「すべて 6」#26292E 白/他は枠 #DDD9CF+ドット 7px、h20 10.5px)、リスト bg #FCFBF8
- [ ] 注釈カード: 色バー 3px、引用 12px/1.7 Noto Serif JP、コメント 11.5px #F7F5EF radius 5、メタ 10px #9A9EA4「§2.1 整流フロー · 昨日 21:40」形式、カード radius 8・padding 9px 11px・gap 9px
- [ ] フッター: 「未配置 0 件」#9A9EA4 /「⤓ Markdown エクスポート」アクセント 600、border-top #ECE9DF
- [ ] §4.7 の全 UI 文言が一字一句一致(自動テキスト比較)
- [ ] ダークモード・アクセント 4 色切替でトークン追随(SelectionMenu・選択中チップは #26292E 固定のまま)

### 6.2 機能検証

- [ ] `/papers/{li_id}` 初回表示: 1 リクエスト(`GET …/viewer`)+表示セクション分の document/units のみで描画され、p50 2 秒以内(docs/09 §1)
- [ ] `?mode` 省略時に last_position.mode で開き、セグメント切替が URL に反映される
- [ ] 前回位置バナー: 表示条件(§5.2)どおりに出て、「続きから ↓」で該当ブロックへスクロール+2 秒強調、「×」でセッション中再表示なし、1200px スクロールで自動クローズ
- [ ] 段落ホバーで「対」出現 → クリック / キー `t` でポップ開閉。同時に開くポップは 1 つ。Esc で 選択メニュー→検索ドロップダウン→Popover→対訳ポップ の順(viewer-shell §10)に閉じる
- [ ] ポップの「再翻訳」: 202→SSE→proposal 表示→「この訳を採用」で本文訳が更新、「破棄」で消える。edited ユニットは確認 UI 経由で discard_edit:true
- [ ] 「指示つき再翻訳」: 指示入力行が展開され、instruction 付き POST が送られる
- [ ] 選択メニュー: 訳文・ポップ内原文の双方で表示。4 色ドットで注釈が楽観的に即時描画され、丸番号が文書順連番で振られる
- [ ] コメント: 入力ポップから color+comment 付き注釈が作成され、カードに 💬 ボックスが出る
- [ ] 「✦ AIに質問」: panel=chat へ切替わり選択アンカーがプリセットされる。「詳細要約 →」も panel=chat+detailed_summary プリセット
- [ ] 「語彙に追加」: 原文選択で POST /api/vocab(context_sentence 付き)、訳文選択では非活性。409 で「既に語彙帳にあります」+「開く」
- [ ] コピー: 引用形式/プレーンの 2 択サブメニューが機能し、引用形式に論文タイトルと § 参照が含まれる
- [ ] 注釈タブ: counts がサーバー値と一致、フィルタ 5 種(すべて/重要/疑問/アイデア/コメントのみ)がクライアント側で正しく絞り込む
- [ ] 注釈カードクリックで本文該当位置へジャンプ+強調。未配置カードは opacity 0.6+「· 未配置」でジャンプ不可。丸バッジクリックで逆方向(カードへ)ジャンプ
- [ ] 削除で「元に戻す」トースト(6 秒)から復元できる
- [ ] 「⤓ Markdown エクスポート」で `GET …/export/annotations` の .md がダウンロードされる
- [ ] `/` で検索フォーカス、300ms デバウンス検索、結果クリックでジャンプ。訳文ヒットが原文ブロックと同一視され重複しない
- [ ] ステータスピルから 6 値変更が PATCH され楽観的に反映される
- [ ] 位置が 5 秒デバウンスで PUT され、別デバイスで開くとバナーに反映される。読書時間が 60 秒間隔+タブ非表示時に送信され、track_reading_time=false で送信されない
- [ ] 未翻訳段落は原文+「翻訳中…」、翻訳セット進捗 SSE で翻訳済みセクションが自動反映される
- [ ] スケルトン→実データの差し替えでレイアウトシフト(CLS)が 0.02 以下
- [ ] キーボード: t / / / Esc / j / k / m / c / b が §5.4 どおり動作し、input フォーカス中・IME 変換中は無効

## 7. 本書で確定した実装決定(一覧)

1. ルートは `/papers/[itemId]`(viewer-shell §3.1)。mode はクエリで永続、panel は初期化時 1 回消費の補助クエリ(`router.replace`)。mode 省略時は last_position.mode → translation。
2. 本文は仮想化せずセクション単位遅延マウント(IntersectionObserver rootMargin 1200px、プレースホルダ min-height 480px)。
3. Query キーは `qk.*` に集約。document/block は staleTime Infinity(不変+ETag)。
4. 再翻訳は proposal 方式の UI(ProposalReview: ✦ 再翻訳案+採用/破棄)。実行中はフッタ非活性+「再翻訳中…」。
5. 対訳ポップは同時 1 段落のみ。`t` の対象は ホバー段落 > 先頭可視段落。開閉アニメーションなし。
6. 選択メニューは選択最終行の下 8px・複数ブロック跨ぎ非対応。擬似ハイライトは `--pr-as`+`--pr-am` outline。
7. コメントは色選択兼用の入力ポップ(width 280px)。コピーは 2 択サブメニュー(引用形式/プレーン)。
8. 語彙追加は side='source' のみ活性(訳文選択時 opacity 0.45+title 説明)。
9. 注釈番号は placed 注釈の文書順 1 始まり連番をクライアント導出。フィルタはクライアントサイド。未配置カードは opacity 0.6+「· 未配置」。
10. バナーは sessionStorage で dismiss 記憶、1200px スクロールで自動クローズ。
11. 相対日時規則(今日/昨日/M/D/YYYY/M/D)を formatRelativeDay に一元化。
12. 位置保存 5 秒デバウンス、読書時間 60 秒間隔+sendBeacon。
13. スケルトン形状・エラー文言・ホバー表現は §5.9〜§5.10 の値で確定(スケルトンバー高さ=font-size の四捨五入整数 px)。
14. 共通骨格(ヘッダ・⋯メニュー・論文内検索・キーマップ・位置/時間フック・パネル幅連動)は plans/09-screens/viewer-shell.md が正。本書の転記は viewer-shell と一致させた(Esc 順=選択メニュー→検索→Popover→対訳ポップ、検索 Popover w300・2 文字以上、`pagehide`+sendBeacon、注釈タブ 320px/他タブ 340px+本文 720/680px 連動)。
15. コンポーネント名・配置は viewer-shell §11 の契約に従う: `TranslationPane`(components/viewer/translation/)・`AnnotationsTab`(components/viewer/panel/。props なし)。1b 固有 UI 状態は `useTranslationPaneStore`(stores/translation-pane-store.ts)。
16. キー `b` の実処理: `activeSectionId` のセクションに対する bookmark 注釈のトグル(POST kind='bookmark' / DELETE)+Toast。
17. `--pr-a`/`--pr-as`/`--pr-am` の直書き禁止 — 実装は semantic エイリアス `--pr-acc`/`--pr-acc-s`/`--pr-acc-m` を使う。
18. 翻訳失敗の表示分岐は `quality_flags` の `placeholder_mismatch`/`provider_refusal`/`untranslated` の 3 値のみ(他フラグは警告下線で訳文表示)。
19. コピー引用形式の位置参照は `§{番号} ¶{序数}`(番号なしセクションは `{title_en} ¶{序数}`)。コメント入力ポップの Esc=入力破棄でポップのみ閉、空保存=comment なしハイライト。
