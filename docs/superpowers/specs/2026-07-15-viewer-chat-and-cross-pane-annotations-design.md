# 設計: チャットのクリア/履歴アクセス + 全ペインでの注釈機能

- 日付: 2026-07-15
- 対象: `apps/web`(ビューアの読解チャットと注釈)
- ステータス: レビュー待ち

## Context(背景)

ユーザーからの 2 つの要望:

1. **チャット欄をクリアするボタンを追加し、チャット履歴にもアクセスできるようにする。**
   現状、`ChatPanel` は 1 論文につき自動生成される「メイン」スレッドを 1 本表示するだけで、
   会話をクリア(仕切り直し)する手段も、過去の会話へ切り替える UI も無い。ただし
   バックエンドは既に**論文ごとに複数スレッド**を保持でき、SDK 関数
   `chatCreateThread` / `chatDeleteThread` / `chatListThreads` / `chatListMessages` も
   生成済みで、UI から使われていないだけである。したがって本要望はほぼフロントエンドのみの
   作業で実現できる(バックエンド変更不要)。

2. **ハイライト・コメント・「AIに質問」・「語彙に追加」を、訳文ペインだけでなく対訳
   (`parallel`)・原文(`source`)ペインでも使えるようにする。**
   注釈の**表示**は既に 3 ペイン全てで動作している(共有キャッシュ `["annotations", itemId]`、
   `side` 判別付きの side 非依存なアンカーモデル)。欠けているのは**作成**経路
   (テキスト選択 → 選択メニュー → 作成)で、これは現状 `TranslationPane.tsx` にのみ存在する。

期待する成果: 3 つのペインで一貫した注釈操作ができ、チャットは「新しい会話」で仕切り直せて
過去の会話にも戻れる。

## 決定事項(ユーザー確認済み)

- **「クリア」= 新しい会話を開始**(破壊的削除ではない)。古い会話は履歴に残す。
- **「AIに質問」は選択した文章を文脈チップとして添付**してからチャットを開く
  (数式の「✦ この式を説明」と同じ挙動)。現状は quote を無視してチャットを開くだけのバグを修正。
- **履歴 UI はヘッダーのポップオーバー一覧**(既存 `FigureVersionPopover.tsx` と同じ実装パターン)。
- **履歴からメイン以外の会話を削除可能**にする(メインスレッドは削除不可 — バックエンドが強制)。

## Feature 1: チャットのクリア + 履歴アクセス

対象: `apps/web/src/components/chat/ChatPanel.tsx`(ThreadBar 行、l.376-453 付近)。

### UI

ThreadBar(`readOnly` でない時のみ表示する既存の `⋯` の並び)に 2 つの操作を追加する:

- **「＋ 新しい会話」ボタン**(= クリア)。`chatCreateThread({ path:{ item_id: itemId },
  body:{ title } })` を呼び、返った `thread.id` を `setActiveThreadId` にセット、
  `qc.invalidateQueries({ queryKey: ["chat-threads", itemId] })`。古い会話はそのまま残る。
  `title` は必須(`ThreadCreateRequest.title: string`)なので、現在時刻から生成する
  (例: `会話 7/15 14:30`)。送信中(`streaming`)は無効化。
- **「履歴」ボタン → ポップオーバー一覧**。`Popover`(`components/ui/Popover.tsx`)で、
  既に配線済みの `threadsQuery`(`chat-threads`)の `items` を一覧表示する。各行:
  - タイトル(`title`)、最終更新(`last_message_at`)、件数(`message_count`、`CountBadge` 可)。
  - 現在のスレッド/メイン(`is_main`)にバッジ。
  - 行クリック → `setActiveThreadId(thread.id)`。`messagesQuery` は `activeThreadId` を
    キーに持つため自動で再取得される。
  - メイン以外の行に削除(×)。クリックで確認 → `chatDeleteThread({ path:{ thread_id }})`。
    削除対象がアクティブなら `setActiveThreadId` をメインに戻す。完了後
    `["chat-threads", itemId]` を invalidate。**削除は不可逆**なので確認を挟む
    (既存 `components/ui/Modal.tsx` を用いた確認ダイアログ)。メインスレッドには × を出さない。

`readOnly`(モバイルのボトムシート)では「新しい会話」と削除は非表示にする(既存の `⋯` メニューと
同じ方針)。履歴の閲覧・切替は非破壊なので `readOnly` でも許可する。

### 再利用する既存物

- `threadsQuery`(`ChatPanel.tsx:133`)、`activeThreadId`/`setActiveThreadId`(l.122)、
  `messagesQuery`(l.151、`activeThreadId` キー)。
- SDK: `chatCreateThread`、`chatDeleteThread`(`@alinea/api-client`。型 `ChatThread` =
  `{ id, title, is_main, message_count, last_message_at? }`)。
- UI: `Popover`、`Modal`、`CountBadge`、`useToast`、`EmptyState`。
- 実装テンプレート: `components/viewer/article/FigureVersionPopover.tsx`(バージョン一覧+操作)。

### 注意点

- メインスレッドは削除できない(`chat.py` が conflict を返す)。× を出さないことで防ぐ。
- スレッド個別メッセージの一括消去エンドポイントは無い。「クリア=新しい会話」を採るのはこのため。
- 現在の自動選択 `useEffect`(l.142-149)は `activeThreadId` が既にあれば何もしないので、
  「新しい会話」で明示セットした後もメインに戻されない。整合する。

## Feature 2: 対訳・原文ペインでの注釈作成

### 現状の要点

- 注釈**表示**は 3 ペインで動作済み(`highlightsByBlock` / `highlightsBySide`、共有キャッシュ)。
- 注釈**作成**経路(選択ハンドラ・`SelectionMenu`・各アクション)は `TranslationPane.tsx` の
  約 200 行(`onPointerUp` l.348-396、`addToVocab` l.399-438、`copySelection` l.440-451、
  `createHighlight` l.454-506、`SelectionMenu` 描画 l.664-680)にのみ存在する。
- アンカーは `{ revision_id, block_id, side, start, end, quote }`。`side` が原文/訳文を判別し、
  `block_id` は 3 ペイン共通の原文ブロック ID。オフセットは**その side のテキスト内**の文字位置。

### アプローチ: 共有フック `useAnnotationSelection` に抽出

`TranslationPane` の作成ロジックを新フック
`apps/web/src/hooks/use-annotation-selection.ts` に切り出し、3 ペインが共有する。三重複を避け、
1 実装に統一する(`TranslationPane` もこのフックを使うよう置換)。

```ts
useAnnotationSelection({
  itemId: string,
  revisionId: string,
  defaultSide: "source" | "translation", // 判別できない時の既定
}): {
  onPointerUp: () => void;          // スクロール領域の onPointerUp に付ける
  selectionMenu: ReactNode;         // 選択中に描画する SelectionMenu(非選択/モバイルは null)
}
```

フックの責務(既存ロジックを移設):

- **`onPointerUp`(統一 side 判別)**: 選択アンカーから最寄りの `[data-block-id]` を辿って
  `blockEl`/`blockId` を得る。side は次の優先順で決める:
  1. `blockEl` 内に `[${SOURCE_TEXT_ATTR}]`(対訳ポップ内原文・未訳フォールバック)があれば
     `"source"`、`offsetRoot` はその要素。
  2. なければ `blockEl.dataset.side`(対訳ペインのセルが持つ `data-side`)。
  3. それも無ければ `defaultSide`。
  `offsetRoot` は 1 の source-text 要素、なければ `blockEl`。`start = textOffsetWithin(offsetRoot,…)`、
  `end = start + text.length`。`sourceFullText`(語彙文脈用)は
  `side==="source" ? (sourceRoot ?? blockEl)?.textContent : undefined`。
  - `TranslationPane`: `defaultSide="translation"`(SOURCE_TEXT_ATTR で source に上書き)。
  - `BilingualPane`: セルの `data-side` を採用。`defaultSide` は任意("translation")。
  - `SourcePane`: `defaultSide="source"`(ブロックに `data-side` も SOURCE_TEXT_ATTR も無いため source)。
- **`createHighlight` / `addToVocab` / `copySelection`**: `TranslationPane` から移設(内容不変)。
  `["annotations", itemId]` への楽観的更新、`annotationsCreate`、409/失敗トースト、`vocabCreate`+
  `extractVocabContext`+`router.push` を含む。`router`/`toast`/`qc` はフック内で取得。
- **`onAskAI`(quote 添付に修正)**: 現在の `selection` から
  `addPendingAnchor({ anchor:{ revision_id, block_id, start, end, quote, side }, display })` を積み、
  `setPanel(true, "chat")`。`display` は quote を短縮した文字列。これで選択文がチャット文脈チップと
  して渡る(数式の `onExplainEquation` と同じ流儀)。`useViewerChatStore().addPendingAnchor` を使う。
- **`selectionMenu`**: `selection && !isMobile` の時のみ `<SelectionMenu milestone="M2" side=…
  position=… onAskAI onCopy onHighlight onComment onAddVocab />` を返す(既存の描画そのまま)。
  `useIsMobile()` を使用(3 ペインとも現状はデスクトップのみ描画だが安全のため踏襲)。

### ペイン別の配線

- **`TranslationPane.tsx`**: 上記 4 ハンドラ+`onPointerUp`+`SelectionMenu` 描画をフック呼び出しに
  置換。`onPointerUp={selection.onPointerUp}` を既存のスクロール領域(l.631)に、`{selectionMenu}` を
  末尾に置く。数式の `onExplainEquation`(`onAskAI` prop 経由)は現状維持(本要望の対象外)。
- **`BilingualPane.tsx`**:
  - `BilingualParagraph` の**訳文セル**(l.582)に `data-block-id={block.id}` を追加
    (現状 `data-side="translation"` のみで `data-block-id` が無く、訳文側選択のブロック解決が
    できない)。原文セルは両方持つので変更不要。
  - スクロール領域(l.358-362)に `onPointerUp` を付け、`{selectionMenu}` を描画。
  - `useAnnotationSelection({ itemId, revisionId, defaultSide:"translation" })`。
- **`SourcePane.tsx`**: スクロール領域に `onPointerUp` を付け、`{selectionMenu}` を描画。
  `useAnnotationSelection({ itemId, revisionId, defaultSide:"source" })`。原文ブロックには
  既に `data-block-id` があり side は常に source。
- **`page.tsx`**: 選択の「AIに質問」はフックが自前で処理する(quote 添付+チャット表示)ため、
  `BilingualPane`/`SourcePane` へ選択用の `onAskAI` を追加で渡す必要はない。数式説明の
  `onExplainEquation` は既存どおり。

### スコープ外

- `ArticlePane`(記事モード)は現状の軽量選択(M0)のまま。ユーザー要望は対訳・原文が対象。
- 数式「✦ この式を説明」の挙動改善(`TranslationPane` は quote 未添付)は既存仕様のため触らない。
- 「語彙に追加」は原文選択のみ(`SelectionMenu` が `side!=="source"` を非活性化済み)。

## テスト(Vitest + @testing-library/react)

既存の colocated `*.test.tsx` と `renderWithClient`/`vi.mock("@alinea/api-client", …)` 方式に倣う。

- **ChatPanel**:
  - 「＋ 新しい会話」クリックで `chatCreateThread` が呼ばれ、`activeThreadId` が切り替わり、
    メッセージ領域が空(`まだ会話がありません`)になる。
  - 履歴ポップオーバーが `threadsQuery` の items を一覧表示し、行クリックで `chat-messages`
    クエリが対象スレッドで再取得される。
  - メイン以外の削除で `chatDeleteThread` が呼ばれ、アクティブ削除時はメインへフォールバック。
    メイン行に × が無いこと。
- **注釈作成(side 解決)**: `onPointerUp` の side/offset 解決を純関数として切り出し、
  jsdom の `Range` + 構築 DOM で単体テスト(SOURCE_TEXT_ATTR / `data-side` / `defaultSide` の
  3 分岐)。加えて `BilingualPane`/`SourcePane` のスモークテストで、選択時に `SelectionMenu`
  (`role="menu"`)が現れ、色ドット押下で `annotationsCreate` が正しい `side` で呼ばれること。

## End-to-end 検証

1. `pnpm --filter @alinea/web test`(または該当パッケージのテストコマンド)で新規/既存テスト緑。
2. 開発サーバ(`/run` スキル)で論文ビューアを開き:
   - チャットタブで「新しい会話」→ 空になり、履歴から元の会話へ戻れる。メイン以外を削除できる。
   - 対訳ペインで原文/訳文を選択 → 選択メニューでハイライト/コメント/AIに質問/(原文のみ)語彙追加。
   - 原文ペインで選択 → 同様(側は source 固定、語彙追加が活性)。
   - 「AIに質問」で選択文がチャット入力欄のチップとして添付されることを確認。
3. `pnpm typecheck` / lint。
