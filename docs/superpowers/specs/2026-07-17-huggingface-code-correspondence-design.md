# Hugging Face関連ソース収集とGitHubコード対応解析の設計

- 日付：2026-07-17
- ステータス：承認済み
- 対象：Hugging Face Paper Pages、Model／Dataset／Space、GitHub公開リポジトリ、Resources、Worker、設定
- 関連：S8 他サイトアダプタ、S12 セマンティック検索、S1 ユーザー別LLMルーティング

## 1. 目的

Hugging Faceには、論文に対応するモデル、データセット、デモ、GitHub実装、プロジェクトページが集約されている。

AlineaはHugging Faceを論文本文の取得元として扱うのではなく、論文を同定して関連資料を収集する情報源として扱う。

GitHub実装が見つかった場合は、論文中の主張やアルゴリズムがリポジトリ内のどのファイル、symbol、行範囲に対応するかを解析する。

コード解析はLLMと埋め込みAPIを利用するため、ユーザーが実行方式と月額予算を選べるようにする。

## 2. Hugging Faceから取得する関連ソース

Hugging Faceの公式Paper API `GET /api/papers/{paperId}` は、arXiv IDに加えて `projectPage`、`githubRepo`、`linkedModels`、`linkedDatasets`、`linkedSpaces` を返す。

したがって、HTMLの見た目へ依存したスクレイピングではなく、公開APIを第一取得経路にする。

取得対象は次のとおりとする。

| 関係 | Resource kind | 上限 | 確定方法 |
|---|---|---:|---|
| Hugging Face Paper Page | `huggingface` | 1 | 候補として提示 |
| `githubRepo` | `github` | 1 | 公式候補として提示 |
| `projectPage` | `project` | 1 | 公式候補として提示 |
| `linkedModels` | `huggingface` | 5 | downloads降順で候補提示 |
| `linkedDatasets` | `huggingface` | 3 | downloads降順で候補提示 |
| `linkedSpaces` | `huggingface` | 3 | likesまたは利用数の降順で候補提示 |

最大13件に制限し、同一の正規化URLは1件にまとめる。

Hugging Face URLをユーザーが取り込み入口として指定した場合は、Paper PageのarXiv IDを既存arXiv取り込みへ渡す。

Model、Dataset、Space URLを指定した場合はrepo APIの `arxiv:<ID>` tagを使い、一意に決まったarXiv IDを既存取り込みへ渡す。

arXiv tagが0件または複数件なら自動決定せず、診断または選択UIを返す。

取り込みに使ったHugging Face URL自体は確定Resourceとして登録し、それ以外の関連リンクは候補として提示する。

既存のarXiv論文を取り込んだ場合も、同じarXiv IDでHugging Face Paper APIを照会し、関連リンクを候補化する。

Hugging Faceの公式ドキュメントは、Paper Pageがモデル、データセット、Spaceを論文へ関連付け、Repository Card内のPaper／arXivリンクから `arxiv:<ID>` タグを生成することを明記している。

## 3. 候補を勝手に確定しない

Hugging Faceが返した関連リンクは、`resource_links.status="suggested"` として保存する。

Resources APIは単数の `suggestion` ではなく複数の `suggestions` を返す。

各候補はResource IDを持ち、次のAPIで個別に採用または却下する。

```text
POST /api/resources/{resource_id}/accept-suggestion
POST /api/resources/{resource_id}/dismiss-suggestion
```

現在のarXiv公式実装候補も同じ永続候補へ移行し、Hugging Face候補と一つの仕組みで扱う。

候補は件数バッジへ含めず、採用した時点で `active` に変える。

却下したURLは `dismissed` のまま保持し、再取り込みや再同期で復活させない。

`githubRepo` と `projectPage` はHugging Face Paper APIの論文単位フィールドを根拠に `official_candidate=true` とする。

linked model、dataset、Spaceは論文との関連を示すだけなので `official_candidate=false` とする。

## 4. Resource kind

既存の `github`、`youtube`、`slides`、`article` に次を追加する。

- **`huggingface`**：Paper Page、Model、Dataset、Space。
- **`project`**：論文の公式プロジェクトページ。

Hugging Faceカードはrepo種別、repo ID、downloads、likes、pipeline tagを表示する。

プロジェクトページはタイトル、ドメイン、OGP、Hugging Face由来の公式候補であることを表示する。

手動で貼り付けた任意URLは従来どおり `article` とし、Hugging Face Paper APIの `projectPage` から得た場合だけ `project` を自動設定する。

## 5. コード対応解析の出力

解析対象は採用済みの公開GitHubリポジトリに限定する。

private repositoryへの認証は初版の対象外とする。

一つの対応結果は次の情報を持つ。

```text
論文側:
  revision_id
  section_id
  block_id
  claim_text

コード側:
  resource_id
  repository
  commit_sha
  path
  symbol
  start_line
  end_line
  code_excerpt

判定:
  explanation_ja
  confidence: high | medium | low
```

GitHubリンクはbranch名ではなく解析時のcommit SHAへ固定し、`#Lx-Ly` の行範囲を付ける。

`code_excerpt` は500文字以下とし、ソース全文はDBへ保存しない。

highとmediumは通常表示し、lowは「関連候補」として折り畳む。

対応を確認できなかった論文上の主張も「対応箇所を特定できませんでした」として残し、結果を捏造しない。

## 6. 解析モード

ユーザー設定 `code_analysis.mode` は次の三値を取る。

| 値 | 表示名 | 挙動 |
|---|---|---|
| `off` | 使用しない | 新しい解析を開始しない。既存結果は閲覧できる |
| `on_demand` | 必要なときだけ | Resourcesの「コード対応を解析」ボタンから見積確認後に実行する |
| `automatic` | 取り込み後に自動 | 論文本文がreadyになり、高信頼の公式GitHub候補またはactive GitHub Resourceが存在する時点で実行する |

既定値は `on_demand` とする。

automaticはHugging Face Paper APIの `githubRepo`、arXivメタデータから高信頼で検出した公式GitHub候補、採用済みまたは手動追加済みのactive GitHub Resourceを対象にする。

公式根拠を持たないsuggested Resourceとdismissed Resourceはautomaticでも解析しない。

公式候補の自動解析はResourceの採用を意味せず、候補カードはユーザーが採用または却下するまでsuggestedのまま残す。

automaticへ切り替えても既存ライブラリ全件を即時実行しない。

既存論文をまとめて解析する場合は、対象件数と概算費用を表示する一回限りのバックフィル確認を必要とする。

## 7. 費用制御

設定 `code_analysis.monthly_budget_usd` を追加し、既定値を5.00 USDとする。

費用はBYOKか運営キーかに関係なく `usage_records.cost_usd` で集計する。

on-demandでは開始前に次を表示する。

- 対象commit。
- 対象ファイル数とコード量。
- 入力token、出力token、埋め込みtokenの概算。
- 選択モデル。
- 概算費用。
- 当月予算の残額。

予算を超える場合は解析を開始せず、設定変更への導線を出す。

automaticで予算を超えた場合はjobを `waiting_budget` にし、通知を作る。

同じ `(user_id, revision_id, resource_id, commit_sha, analysis_version)` の成功結果は再利用し、二重課金を防ぐ。

論文revisionまたはGitHub commitが変わった場合は既存結果へ「古い結果」を表示し、再解析は設定モードに従う。

## 8. リポジトリ取得と境界

GitHub REST APIでdefault branchのcommit SHAを解決し、そのSHAを指定してtar archiveを取得する。

GitHubの公式APIは公開リポジトリのarchive取得を認証なしでも許可するが、レート制限を避けるためサーバー側GitHub tokenを優先する。

取得上限は次のとおりとする。

- 圧縮archive：100 MiB。
- 展開後総量：300 MiB。
- 対象コード：10 MiB。
- 対象ファイル：2,000件。
- 1ファイル：512 KiB。

`node_modules`、`vendor`、`dist`、`build`、`.git`、生成物、minified file、lock file、dataset、weight、binaryを除外する。

`.env`、秘密鍵、証明書、credentialらしいファイルはLLMへ送らない。

symlink、hardlink、絶対path、`..`、device fileを拒否し、archiveを実行しない。

依存のインストール、build、test、任意commandの実行は行わない。

## 9. 解析パイプライン

解析は `code_analysis` Jobとしてbulk queueで実行する。

処理は次の順とする。

1. 所有権、設定モード、月額予算、Resource statusを再検証する。
2. GitHub default branchのcommit SHAを解決する。
3. 境界付きでarchiveを取得し、安全な対象ファイルだけを抽出する。
4. 論文から最大30件の主張候補をblock anchor付きで抽出する。
5. コードをsymbolまたは最大200行単位へchunk化する。
6. 識別子、希少語、数式名によるlexical retrievalで各主張の候補を絞る。
7. セマンティック検索基盤のEmbeddingProviderで候補を再順位付けする。
8. 上位候補だけをLLMへ渡し、structured outputで対応を判定する。
9. サーバーがpath、行範囲、excerpt、paper anchorを元データと照合する。
10. 検証済み対応とusageを保存し、一時archiveを削除する。

コード内のコメント、README、文字列に含まれる命令はすべて解析対象データとして扱う。

コード由来テキストをsystem instructionへ連結せず、LLMへ外部tool権限を与えない。

structured outputが実在しないpathや行範囲を返した場合はその対応を破棄する。

## 10. API

```text
POST /api/library-items/{item_id}/code-analysis/estimate
  body: {resource_id, section_ids?}
  -> {estimate_id, commit_sha, files, input_tokens, output_tokens, estimated_cost_usd,
      budget_remaining_usd, expires_at}

POST /api/library-items/{item_id}/code-analysis
  body: {resource_id, estimate_id, section_ids?}
  -> 202 {job_id}

GET /api/library-items/{item_id}/code-analysis
  -> {runs, current_result, stale}

POST /api/code-analysis/{run_id}/rerun
  -> 202 {job_id}
```

estimateは10分で失効する。

開始時にcommit、設定、予算が変わっていた場合は409を返し、再見積もりを求める。

同一対象のqueued／running jobがある場合は既存jobを返す。

## 11. データモデル

`code_analysis_estimates` は実行前見積もりを表し、commit SHA、対象規模、概算token、概算費用、有効期限を保持する。

`code_analysis_runs` は実行単位を表す。

```text
id, user_id, library_item_id, resource_id, revision_id,
commit_sha, analysis_version, trigger, status,
estimated_cost_usd, actual_cost_usd, error,
created_at, finished_at
```

`code_correspondences` は検証済み対応を表す。

```text
id, run_id, position,
paper_anchor, claim_text,
path, symbol, start_line, end_line, code_excerpt,
explanation_ja, confidence
```

一意制約は `(user_id, revision_id, resource_id, commit_sha, analysis_version)` とする。

解析結果は生成コストの高いユーザーデータなので完全バックアップへ含める。

復元後はcommit SHA付きURLをそのまま利用できるため、再解析を必須にしない。

## 12. UI

アカウント設定のモデルルーティング付近へ「GitHubコード対応解析」を追加する。

設定項目は解析モード、月額予算、code_analysis taskのprovider／modelとする。

ResourcesのGitHubカードへ次を表示する。

- 未解析：`コード対応を解析`。
- 見積中：対象規模を取得中。
- 実行確認：概算費用と予算残額。
- 実行中：進捗とcancel。
- 完了：対応件数、commit、更新日時、`結果を見る`。
- stale：`リポジトリが更新されています` と再解析。
- off：`設定でコード解析が無効です` と設定リンク。

結果はResourcesタブ内の詳細面で、論文側とコード側を一行の対応として表示する。

論文anchorはビューア内の該当blockへ移動し、コードanchorはGitHubの固定commitと行範囲を新規タブで開く。

## 13. エラー処理

- GitHub 404／private：公開リポジトリだけ対応する旨を表示する。
- GitHub 403／429：reset時刻またはretry-afterまで再試行しない。
- archive上限超過：対象規模と上限を表示し、解析を開始しない。
- 対象コード0件：対応するコードファイルが見つからないと表示する。
- LLM失敗：途中結果を公開せず、再実行できるfailed runを残す。
- 対応0件：成功扱いで「対応箇所を特定できませんでした」と表示する。
- 予算超過：waiting_budgetとし、設定変更または翌月まで外部APIを呼ばない。

## 14. テスト

Hugging FaceとGitHubへの実通信は行わない。

保存したJSON、tar fixture、FakeEmbeddingProvider、FakeLLMProviderを使う。

最低限、次を検査する。

- Hugging Face Paper URL、Model、Dataset、Space URLの正規化。
- `projectPage`、`githubRepo`、linked artifactsの上限、順序、重複排除。
- 候補の個別採用、却下、再提案防止。
- off、on_demand、automaticの三モード。
- automaticが高信頼の公式GitHub候補とactive GitHubを対象にし、根拠の弱いsuggested／dismissed GitHubを対象にしないこと。
- 見積失効、予算超過、同一対象の冪等性。
- archive path traversal、symlink、展開量超過、秘密ファイル除外。
- commit SHA固定URLと行範囲検証。
- 存在しないpath／lineをLLMが返した場合の破棄。
- 二ユーザー間で設定、BYOK、結果が混ざらないこと。
- バックアップのラウンドトリップ。
- UIの見積確認、進捗、結果、stale、off表示。

## 15. 非目標

- private GitHub repositoryのOAuth／PAT連携。
- リポジトリのbuild、test、benchmark実行。
- 論文実験の再現性を自動で保証すること。
- コードの正しさや論文主張の真偽を断定すること。
- GitHub issue、pull request、discussionの解析。
- Hugging Face上のmodel weightやdataset本体のダウンロード。

## 16. 参照資料

- [Hugging Face Paper Pages](https://huggingface.co/docs/hub/en/paper-pages)
- [Hugging Face Model Cards](https://huggingface.co/docs/hub/en/model-cards)
- [Hugging Face Hub API Endpoints](https://huggingface.co/docs/hub/en/api)
- [GitHub repository archive API](https://docs.github.com/en/rest/repos/contents)
- [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
