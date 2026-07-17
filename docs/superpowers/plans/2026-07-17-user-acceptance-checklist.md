# 最終ユーザー受け入れチェックリスト

「ACL Anthologyに対応した」という一行だけでは、確認者ごとに別の論文を使い、別の画面を見て、別の基準で合格を決めてしまう。
それでは、チェックが埋まっても同じ機能を確認したことにならない。

このチェックリストでは、入力するURL、操作、画面に表示される値を固定する。
確認者は説明を聞いて判断するのではなく、記載された操作を行い、記載された結果を画面で確かめる。
後のケースは前のケースで取り込んだ論文を使うため、UAT-SRC-01から記載順に進める。
全ケースの所要時間は、外部サイトとLLMが正常に応答する環境で4時間から6時間を見込む。

## 1. 確認者が記録する情報

- [ ] 確認対象のコミットSHA：`________________________________________`
- [ ] 確認環境URL：`________________________________________`
- [ ] 確認日：`________________________________________`
- [ ] 確認者：`________________________________________`
- [ ] OS：`________________________________________`
- [ ] ブラウザとバージョン：`________________________________________`
- [ ] PowerPointまたはLibreOfficeのバージョン：`________________________________________`

受け入れ結果は、このファイルの作業用コピーまたはIssueへ記録する。
リポジトリ内の原本を直接更新すると確認対象のコミットSHAが変わるため、原本にはチェックを入れない。

## 2. 判定方法

各ケースには一つの判定を付ける。

- **PASS**：記載された操作を行い、合格条件をすべて確認した。
- **FAIL**：操作はできたが、合格条件を一つでも満たさなかった。
- **BLOCKED**：外部サイトの停止、確認環境の故障、アカウント不足により操作を完了できなかった。

外部サイトをブラウザで開けない場合は、Alineaの不具合と決めつけず `BLOCKED` とする。
その場合は、外部サイトのエラー画面と確認時刻を証拠へ残す。

次の問題は一件でも残れば `NO-GO` とする。

- **P0**：データ消失、他ユーザーのprivateデータ表示、権限外操作、APIキー露出、確認なしの予算超過。
- **P1**：取り込み、閲覧、検索、出力、PPTX生成、公開の主要手順を完了できない。
- **P1**：原論文にない主張や、対応しないコードを高い確度で表示する。
- **BLOCKED**：必須ケースを確認できない。

## 3. 実装者が先に用意するもの

確認者へ渡す環境が毎回違えば、同じ手順でも結果は揃わない。
実装者は、確認依頼の前に次を用意する。

```bash
uv run python apps/api/scripts/seed_user_acceptance.py \
  --reset \
  --output /tmp/alinea-uat-accounts.json
```

このコマンドはreview環境だけで実行し、出力ファイルを確認者へ安全な方法で渡す。

- [ ] UAT-AとUAT-Bの二つの一般ユーザーアカウントを用意した。
- [ ] UAT-AはOpenAIを、UAT-BはAnthropicをPPTX生成に利用できる。
- [ ] 両アカウントのGitHubコード解析予算を5.00 USDに設定した。
- [ ] 確認開始時点では、両アカウントのライブラリを空にした。
- [ ] 外部URLへの通信を許可したreview環境を用意した。
- [ ] 自動テスト、マイグレーション、OpenAPI一致、`pnpm ppt-master:smoke` が成功している。
- [ ] 既知の制約と未解決課題を確認者へ渡した。

アカウント情報をここへ記録する。

- UAT-A：`________________________________________`
- UAT-B：`________________________________________`

固定URLと期待値の機械可読版は[受け入れ確認用データ](./2026-07-17-user-acceptance-fixtures.json)に保存している。
downloads、likes、stars、upvotes、関連候補の総件数は変動するため合否に使わない。

## 4. 固定する入力データ

| ID | 入力URL | 変わらない照合値 |
|---|---|---|
| ARXIV-ATTENTION | `https://arxiv.org/abs/1706.03762` | `Attention Is All You Need`、Ashish Vaswani、v1からv7 |
| ACL-BERT | `https://aclanthology.org/N19-1423/` | BERT論文、Jacob Devlin、NAACL 2019、DOI `10.18653/v1/N19-1423` |
| OPENREVIEW-VIT | `https://openreview.net/forum?id=YicbFdNTTy` | ViT論文、Alexey Dosovitskiy、ICLR 2021 |
| PUBMED-HEALTHCARE | `https://pubmed.ncbi.nlm.nih.gov/38878555/` | PMID `38878555`、PMCID `PMC11638972` |
| PMC-HEALTHCARE | `https://pmc.ncbi.nlm.nih.gov/articles/PMC11638972/` | 同じPMIDとPMCID、図9件以上 |
| HF-LLAMA2 | `https://huggingface.co/papers/2307.09288` | arXiv `2307.09288`、GitHub、Model、Dataset、Space |
| HF-PAPER-CIRCLE | `https://huggingface.co/papers/2604.06170` | arXiv、project、GitHub、Dataset、Space |
| ARXIV-LORA | `https://arxiv.org/abs/2106.09685` | LoRA論文、Edward J. Hu、公式GitHub |
| INVALID-NON-PAPER | `https://example.com/not-a-paper` | 論文として取り込まれない |

タイトルは大文字と小文字、連続空白の違いを無視して比較する。
著者は先頭著者が一致することを必須とする。

## 5. arXivから論文を取り込む

### UAT-SRC-01 Attention Is All You Need

**操作**

1. UAT-Aでログインする。
2. `https://arxiv.org/abs/1706.03762` を取り込み欄へ貼る。
3. 処理完了まで待ち、論文を開く。

**合格条件**

- [ ] タイトルが `Attention Is All You Need` である。
- [ ] 先頭著者が `Ashish Vaswani` である。
- [ ] arXiv IDが `1706.03762` である。
- [ ] 最新版がv7として表示される。
- [ ] 本文、数式、図を閲覧できる。
- [ ] 処理完了後に再読込しても、失敗表示や終了しない進行表示へ戻らない。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 6. ACL Anthologyから論文を取り込む

### UAT-SRC-02 BERT

**操作**

1. UAT-Aの取り込み欄へ `https://aclanthology.org/N19-1423/` を貼る。
2. 処理完了後に論文を開く。
3. 論文情報をACL Anthologyの元ページと見比べる。

**合格条件**

- [ ] タイトルが `BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding` である。
- [ ] 著者がJacob Devlin、Ming-Wei Chang、Kenton Lee、Kristina Toutanovaの順で表示される。
- [ ] venueが `NAACL`、年が `2019` である。
- [ ] DOIが `10.18653/v1/N19-1423` である。
- [ ] Anthology IDが `N19-1423` である。
- [ ] abstractだけで終わらず、本文の `3.1 Pre-training BERT` を開ける。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 7. OpenReviewから論文を取り込む

### UAT-SRC-03 Vision Transformer

**操作**

1. UAT-Aの取り込み欄へ `https://openreview.net/forum?id=YicbFdNTTy` を貼る。
2. 処理完了後に論文を開く。
3. OpenReviewのforum IDと書誌を確認する。

**合格条件**

- [ ] タイトルが `An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale` である。
- [ ] 先頭著者が `Alexey Dosovitskiy` である。
- [ ] forum IDが `YicbFdNTTy` である。
- [ ] venueが `ICLR`、年が `2021` である。
- [ ] PDFまたは構造化本文を閲覧できる。
- [ ] OpenReviewのブラウザ確認画面を、論文本文として保存していない。

OpenReviewがchallenge画面を返して取り込めない場合は、エラー画面と時刻を残して `BLOCKED` とする。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 8. PubMedとPMCを同じ論文へ統合する

### UAT-SRC-04 PMID 38878555

**操作**

1. UAT-Aの取り込み欄へ `https://pubmed.ncbi.nlm.nih.gov/38878555/` を貼る。
2. 処理完了後にタイトルと識別子を確認する。
3. 続けて `https://pmc.ncbi.nlm.nih.gov/articles/PMC11638972/` を貼る。
4. ライブラリの件数と、開かれた論文のIDを確認する。

**合格条件**

- [ ] タイトルが `Transformers and large language models in healthcare: A review` である。
- [ ] 先頭著者が `Subhash Nerella` である。
- [ ] PMIDが `38878555`、PMCIDが `PMC11638972` である。
- [ ] DOIが `10.1016/j.artmed.2024.102900` である。
- [ ] PubMed URLとPMC URLが同じライブラリ項目を開く。
- [ ] 二回の取り込み後も論文が二件に増えない。
- [ ] PMC本文の見出しと九件以上の図を閲覧できる。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 9. Hugging Faceから追加ソースを集める

### UAT-SRC-05 Llama 2のModel、Dataset、Space

**操作**

1. UAT-Aの取り込み欄へ `https://huggingface.co/papers/2307.09288` を貼る。
2. 処理完了後に関連ソース欄を開く。
3. 候補を採用せずに一度画面を再読込する。

**合格条件**

- [ ] 論文タイトルが `Llama 2: Open Foundation and Fine-Tuned Chat Models` である。
- [ ] arXiv IDが `2307.09288` である。
- [ ] GitHub候補に `https://github.com/facebookresearch/llama` がある。
- [ ] Model候補に `meta-llama/Llama-2-7b-chat-hf` がある。
- [ ] Dataset候補に `elyza/ELYZA-tasks-100` がある。
- [ ] Space候補に `mteb/leaderboard` がある。
- [ ] どの候補も、採用前は確定リソースの件数へ加算されない。
- [ ] 再読込後も候補のままであり、勝手に採用されない。

候補の総数と表示順は合否に使わない。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

### UAT-SRC-06 projectページの採用と候補の却下

**操作**

1. UAT-Aの取り込み欄へ `https://huggingface.co/papers/2604.06170` を貼る。
2. 関連ソース欄を開く。
3. project候補 `https://papercircle.vercel.app/` を採用する。
4. Dataset候補 `ItsMaxNorm/pc-benchmark` を却下する。
5. 画面を再読込する。

**合格条件**

- [ ] GitHub候補に `https://github.com/MAXNORM8650/papercircle` がある。
- [ ] project候補に `https://papercircle.vercel.app/` がある。
- [ ] Dataset候補に `ItsMaxNorm/pc-benchmark` がある。
- [ ] Space候補に `ItsMaxNorm/papercircle-papers-api` がある。
- [ ] 採用したprojectだけが確定リソースとして表示される。
- [ ] 却下したDatasetは再読込後も候補へ戻らない。
- [ ] projectとGitHubが同じURLとして誤って統合されない。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

### UAT-SRC-07 論文ではないURL

**操作**

1. UAT-Aの取り込み欄へ `https://example.com/not-a-paper` を貼る。

**合格条件**

- [ ] 対応していない入力であることが表示される。
- [ ] 空タイトルの論文や、example.comの文章を本文とする論文が作られない。
- [ ] 失敗後も別のURLを入力できる。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 10. BERTを読みながら生成機能を確認する

### UAT-READ-01 翻訳スタイルと再翻訳

**操作**

1. UAT-AでBERT論文を開く。
2. 自然訳、直訳、やさしい訳を順に選ぶ。
3. abstractの先頭段落を選び、再翻訳を開始する。
4. 追加指示へ `専門用語は英語を括弧で残し、二文以内で訳してください` と入力する。
5. 提案を一度破棄し、同じ操作をもう一度行って提案を採用する。

**合格条件**

- [ ] 三つの翻訳スタイルを切り替えられる。
- [ ] 未生成のスタイルは生成状態を表示し、完了後に本文へ切り替わる。
- [ ] 再翻訳中も現在の訳文が消えない。
- [ ] 提案を破棄した一回目は現在の訳文が変わらない。
- [ ] 提案を採用した二回目は選択した段落だけが変わる。
- [ ] 画面を再読込しても採用結果が残る。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

### UAT-READ-02 チャットの回答と根拠

**操作**

1. BERT論文のチャットへ `BERTの事前学習で使う二つのタスクは何ですか。論文中の根拠も示してください。` と送る。
2. 回答内の根拠チップを開く。

**合格条件**

- [ ] 回答に `Masked Language Model` または `MLM` が含まれる。
- [ ] 回答に `Next Sentence Prediction` または `NSP` が含まれる。
- [ ] 根拠チップから `3.1 Pre-training BERT` 付近へ移動できる。
- [ ] 根拠の本文に、回答した二つのタスクが実際に書かれている。
- [ ] 存在しない節番号や別の論文を根拠として表示しない。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

### UAT-READ-03 AI単語候補

**操作**

1. BERT論文で単語候補の抽出を開始する。
2. `masked language model` または `bidirectional` の候補を探す。
3. 見つけた候補を採用する。
4. 別の候補を一件却下し、画面を再読込する。

**合格条件**

- [ ] `masked language model` または `bidirectional` が候補に含まれる。
- [ ] 候補に意味とBERT本文中の文脈が表示される。
- [ ] 採用した語が語彙帳へ追加される。
- [ ] 語彙帳からBERTの該当箇所へ戻れる。
- [ ] 却下した候補が再読込後に復活しない。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

### UAT-READ-04 arXivのv1とv7の差分

**操作**

1. `Attention Is All You Need` を開く。
2. 改版差分でv1とv7を選ぶ。
3. 追加または変更された箇所を一件開く。
4. v7とv7を選び直す。

**合格条件**

- [ ] version選択肢にv1とv7がある。
- [ ] v1とv7の比較で、追加、削除、変更のいずれかが一件以上表示される。
- [ ] 差分からv7の対応箇所へ移動できる。
- [ ] v7同士の比較では変更なしと表示される。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 11. 固定した五つの質問で意味検索を確認する

### UAT-SEARCH-01

UAT-AへARXIV-ATTENTION、ACL-BERT、OPENREVIEW-VIT、ARXIV-LORA、PMC-HEALTHCAREを取り込んだ状態で行う。

**操作と合格条件**

- [ ] `再帰や畳み込みを使わず、注意機構だけで系列変換を行う` を検索し、ARXIV-ATTENTIONが上位三件へ入る。
- [ ] `双方向の文脈を使うマスク言語モデルで事前学習する` を検索し、ACL-BERTが上位三件へ入る。
- [ ] `画像を固定サイズのパッチ列としてTransformerへ入力する` を検索し、OPENREVIEW-VITが上位三件へ入る。
- [ ] `大規模言語モデルを低ランク行列だけで効率よく適応する` を検索し、ARXIV-LORAが上位三件へ入る。
- [ ] `医療データにおけるTransformerと大規模言語モデルの応用を概観する` を検索し、PMC-HEALTHCAREが上位三件へ入る。
- [ ] ARXIV-ATTENTIONの「似た論文」にBERT、ViT、LoRAのいずれかが表示される。
- [ ] UAT-Bだけが持つprivate論文はUAT-Aの結果へ出ない。

期待する論文が四問以上で上位三件へ入れば検索品質をPASSとする。
他ユーザーのprivate論文が一件でも出た場合はP0とする。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 12. LoRA論文とGitHubコードの対応を確認する

### UAT-CODE-01 必要なときだけ解析する

**操作**

1. UAT-Aへ `https://arxiv.org/abs/2106.09685` を取り込む。
2. GitHub候補がなければ `https://github.com/microsoft/LoRA` を関連ソースとして追加する。
3. 設定でコード対応解析を「必要なときだけ」、月額予算を5.00 USDにする。
4. LoRAのGitHubカードから解析を開始する。
5. 見積もり画面のrepository、commit SHA、対象ファイル数、概算費用、残予算を確認して実行する。
6. 完了後に論文側とGitHub側のリンクを開く。

**合格条件**

- [ ] ボタンを押すまでは解析ジョブが始まらない。
- [ ] 確認操作の前に見積もりが表示される。
- [ ] 結果のGitHub URLが40文字の固定commitを含む。
- [ ] `loralib/layers.py` の `Linear` が、低ランク行列を加える主張と対応する。
- [ ] `loralib/utils.py` の `mark_only_lora_as_trainable` が、LoRAパラメータだけを学習対象にする主張と対応する。
- [ ] `loralib/utils.py` の `lora_state_dict` が、LoRAパラメータを分けて保存する主張と対応する。
- [ ] 上の三対応のうち二つ以上を結果画面から確認できる。
- [ ] 論文リンクはLoRA本文、GitHubリンクは該当symbol付近を開く。
- [ ] 対応が見つからない主張に、無関係なファイルを高確度で割り当てない。

確認時点の基準commitは `c4593f060e6a368d7bb5af5273b8e42810cdef90` である。
上流HEADが変わった場合はSHAの一致を求めず、固定SHAであることとpath、symbolの一致を確認する。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

### UAT-CODE-02 off、automatic、予算不足

**操作**

1. UAT-Aでmodeを `off` にし、LoRAのリソース画面を再読込する。
2. modeを「必要なときだけ」に戻し、月額予算を0.00 USDにする。
3. BERTへ `https://github.com/google-research/bert` を関連ソースとして追加し、新しい解析を開始する。
4. UAT-Bでmodeを「取り込み後に自動」、月額予算を5.00 USDにする。
5. UAT-BへLoRA論文を取り込み、公式GitHub候補を表示する。

**合格条件**

- [ ] `off` では新しい解析を開始する操作が無効または非表示になる。
- [ ] BERTの新規解析は予算0.00 USDのため `waiting_budget` となり、使用額が増えない。
- [ ] UAT-Bではreadyな論文と公式GitHub候補が揃った後に自動解析が始まる。
- [ ] 自動解析が始まってもGitHub候補自体は勝手に採用されない。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 13. 論文単位の出力を別端末で開く

### UAT-EXPORT-01 HTML、PDF、ZIP、Anki TSV

**操作**

1. UAT-AでBERT論文を開く。
2. 原文HTML、訳文HTML、対訳HTML、注釈PDF、対訳PDFを選ぶ。
3. 語彙帳からAnki TSVも出力する。
4. ZIPをダウンロードして別directoryへ展開する。
5. ブラウザをオフラインにしてHTMLを開く。
6. PDFを開き、Anki TSVを表計算ソフトへ読み込む。

**合格条件**

- [ ] 選択した成果物だけがZIPに入る。
- [ ] オフラインのHTMLで本文、数式、図を表示できる。
- [ ] 対訳HTMLで原文と訳文の対応を追える。
- [ ] 注釈PDFの注釈が対応する原文箇所にある。
- [ ] 対訳PDFが原文ページと訳文ページの交互配置になっている。
- [ ] Anki TSVがterm、meaning、contextの列へ崩れず読み込まれる。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 14. PMC論文から編集可能なPPTXを作る

### UAT-PPTX-01 研究発表用スライド

**操作**

1. UAT-AでPMC-HEALTHCAREを開く。
2. メモへ `UAT-PRIVATE-NOTE-20260717` と保存する。
3. チャットへ `UAT-PRIVATE-CHAT-20260717` と送る。
4. 訳文の一段落を `UAT-PRIVATE-TRANSLATION-20260717` に手動変更する。
5. 「✦ ツール」から「論文からスライドを生成」を開く。
6. 用途を「研究発表」、聴衆を「研究者」にする。
7. 任意指示へ `医療データの種類ごとに応用例を整理し、限界を最後に示してください` と入力する。
8. 完了後にPPTXをダウンロードし、PowerPointまたはLibreOfficeで開く。

**合格条件**

- [ ] source準備、構成、slide作成、検証、export、uploadの進捗を追える。
- [ ] PPTXが16:9で開き、主要な本文が日本語である。
- [ ] slide titleを編集して別名保存できる。
- [ ] 原論文のFig. 3、Fig. 4、Fig. 5のいずれかに由来する図があり、元図と内容が対応する。
- [ ] 原論文と照合した五つの主張または数値に捏造がない。
- [ ] `UAT-PRIVATE-NOTE-20260717` がPPTX内にない。
- [ ] `UAT-PRIVATE-CHAT-20260717` がPPTX内にない。
- [ ] `UAT-PRIVATE-TRANSLATION-20260717` がPPTX内にない。
- [ ] 成果物画面に使用modelとppt-master revisionが表示される。
- [ ] 再生成すると新しいPPTXが最新版になり、履歴一覧は増えない。

privateな確認文字列が一つでもPPTXへ入った場合はP0とする。
原論文にない主張を事実として表示した場合はP1とする。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

### UAT-PPTX-02 providerと所有者境界

**操作**

1. UAT-BへPMC-HEALTHCAREを取り込む。
2. UAT-BのPPTX用modelがAnthropicであることを設定画面で確認する。
3. UAT-Bでも輪読会用PPTXを一件生成する。
4. UAT-AのPPTX download URLをコピーし、UAT-Bで開く。

**合格条件**

- [ ] UAT-Aの成果物metadataはOpenAIのmodelを示す。
- [ ] UAT-Bの成果物metadataはAnthropicのmodelを示す。
- [ ] UAT-BからUAT-Aのdownload URLを開くと404になる。
- [ ] どちらの画面、PPTX、エラー表示にもAPIキーが出ない。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 15. 完全バックアップを別ユーザーへ二回取り込む

### UAT-BACKUP-01

**事前データ**

UAT-AのBERT論文へメモ `UAT-A-NOTE-20260717` を追加する。
UAT-Aには、これまでのケースで作った語彙、チャット、関連ソース、PPTXが存在する状態にする。

UAT-BでもBERT論文を先に取り込み、メモ `UAT-B-KEEP-20260717` を追加する。
UAT-PPTX-02の確認後、UAT-BからPMC-HEALTHCAREを削除し、PPTXが存在しない状態にする。

**操作**

1. UAT-Aの完全バックアップを作る。
2. UAT-Bへ同じバックアップを取り込む。
3. 完了後にBERT論文、語彙帳、関連ソース、PMC-HEALTHCAREのPPTXを確認する。
4. 同じバックアップをUAT-Bへもう一度取り込む。

**合格条件**

- [ ] UAT-Aの `UAT-A-NOTE-20260717` がUAT-Bへ復元される。
- [ ] UAT-Bの既存メモ `UAT-B-KEEP-20260717` が消えない。
- [ ] 翻訳、語彙、チャット、関連ソース、図、サムネイルが復元される。
- [ ] PMC-HEALTHCAREのPPTXをUAT-Bでダウンロードできる。
- [ ] 二回目の取り込み後も論文、語彙、関連ソース、PPTXが重複しない。
- [ ] UAT-Bのmodel設定とAPIキー設定がUAT-Aの値で上書きされない。

既存データの消失、上書き、二重作成はP0とする。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 16. 訪問済み論文だけをオフラインで開く

### UAT-OFFLINE-01

**操作**

1. UAT-AでBERT論文をオンライン表示し、訳文と図を一度開く。
2. HF-PAPER-CIRCLEは開かず、URLだけを控える。
3. ChromeまたはEdgeのDevToolsでNetworkを `Offline` にする。
4. BERTのviewer URLを再読込する。
5. 控えたHF-PAPER-CIRCLEのviewer URLを開く。
6. Networkをオンラインへ戻し、UAT-Aからログアウトする。
7. 再びOfflineにし、BERTのviewer URLを開く。

**合格条件**

- [ ] 訪問済みBERTはオフラインでも本文、訳文、数式、図を表示する。
- [ ] 未訪問のHF-PAPER-CIRCLEは未保存であることを表示する。
- [ ] ログアウト後は、キャッシュ済みBERTのprivate本文を表示しない。
- [ ] オンラインの401をキャッシュ済み200として隠さない。

ログアウト後にprivate本文が見えた場合はP0とする。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 17. 記事公開とコメントを別ユーザーで確認する

### UAT-PUBLIC-01

**操作**

1. UAT-AでBERTの記事を生成する。
2. privateメモへ `UAT-PRIVATE-PUBLICATION-20260717` と保存する。
3. 記事を限定公開する。
4. シークレットウィンドウで限定公開URLを開く。
5. ページソースを開き、`robots` を検索する。
6. UAT-Aでコメント `UAT-OWNER-COMMENT-20260717` を投稿する。
7. UAT-Bで同じURLを開き、コメント `UAT-COMMENT-20260717` を投稿する。
8. UAT-Bで自分のコメントを編集する。
9. UAT-BでUAT-Aのコメントを編集または削除しようとする。
10. UAT-AでUAT-Bのコメントを非表示にし、再表示する。
11. UAT-Bで自分のコメントを削除する。
12. UAT-Aで記事を公開へ変更する。

**合格条件**

- [ ] シークレットウィンドウで限定公開記事を読める。
- [ ] シークレットウィンドウではコメントを投稿できない。
- [ ] 限定公開ページに `noindex` がある。
- [ ] 公開記事に `UAT-PRIVATE-PUBLICATION-20260717` がない。
- [ ] 公開記事に原文、訳文、チャット全文が含まれない。
- [ ] UAT-Bは自分のコメントを投稿、編集、削除できる。
- [ ] UAT-Bは他人のコメントを編集または削除できない。
- [ ] UAT-Aはコメントを非表示にし、再表示できる。
- [ ] 公開へ変更後はログアウト状態でも記事を読める。

private情報の混入または権限外操作の成功はP0とする。

証拠URLまたは画像：`________________________________________`

判定：`PASS / FAIL / BLOCKED`

## 18. ユーザーへ依頼しない確認

次の事項は画面操作だけでは正しく判定できない。
実装者が自動テストと統合レポートで証明し、確認者はその結果だけを受け取る。

- 壊れたバックアップを取り込んだときのtransaction rollback。
- BYOKがDB、job payload、ログ、バックアップへ保存されないこと。
- GitHub archiveのpath traversal、symlink、容量超過、秘密ファイル拒否。
- `off` または予算不足時に外部LLM APIを一度も呼んでいないこと。
- PPTX再生成のuploadまたはDB更新を故意に失敗させても旧成果物が残ること。
- Service Workerが別ユーザーのcache keyを内部的に再利用しないこと。
- migrationのupgrade、downgrade、再upgradeで既存データを保持すること。
- OpenAPIと生成クライアントが実行中APIと一致すること。

これらをユーザーの目視確認へ混ぜない。
見えない内部状態を推測させても、再現可能な受け入れ確認にはならないからである。

## 19. 不具合の記録

FAILまたはBLOCKEDごとに次を複製する。

```text
ケースID：
判定：FAIL / BLOCKED
重要度：P0 / P1 / P2 / P3
確認したコミットSHA：
入力URL：
操作番号：
期待した表示：
実際の表示：
再現回数：
画像または動画：
論文IDまたはjob ID：
外部サイトを直接開いた結果：
```

APIキー、認証情報、private論文本文は不具合票へ貼らない。

## 20. 最終判定

- [ ] UAT-SRC-01からUAT-PUBLIC-01まで、すべてPASSになった。
- [ ] P0が0件である。
- [ ] P1が0件である。
- [ ] BLOCKEDが0件である。
- [ ] 実装者向け自動検証の必須コマンドがすべて成功している。
- [ ] 確認したコミットSHAとmainへマージするSHAが一致する。

最終判断：`GO / NO-GO`

確認者：`________________________________________`

確認日：`________________________________________`

備考：

```text

```
