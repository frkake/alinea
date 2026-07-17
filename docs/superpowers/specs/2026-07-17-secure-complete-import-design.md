# 安全で完全なバックアップインポート設計

**日付:** 2026-07-17  
**対象:** `integration/all-features` の完全データ移行(S2)

## 目的

完全バックアップのインポートを、任意のアップロードZIPに対して安全に処理し、エクスポートしたユーザー状態を移行先で復元できるようにする。

## 方針

### 1. インポート用S3キーを再採番する

ZIPの `storage_key` は到達可能なアセットを識別するためだけに使い、S3の書き込み先としては使わない。インポート時に、対象ユーザーと移行先の論文・図・アセットIDから `imports/restored/<user>/<asset-id>` 形式のキーを生成する。

- source asset、overview figure、explainer figure のDBレコードには再採番後のキーを保存する。
- manifestのアセットは、エクスポートpayload内の許可されたアセット参照と一致する場合だけ復元する。
- bucket種別もpayloadの参照元から決定し、manifestの任意値を信用しない。
- これにより、悪意あるZIPが他ユーザーのS3オブジェクトを指定しても上書きできない。

### 2. ZIPを明示的な資源上限の下で処理する

APIはアップロードをチャンク読み込みし、アーカイブサイズ上限を超えた時点で413を返す。workerはアーカイブを読む前に、ZIP entry数・各entryの非圧縮サイズ・合計非圧縮サイズ・圧縮率を検証する。

- `manifest.json` と `data.json` は小さなJSON上限の範囲内に制限する。
- アセットは検証済みの `ZipInfo` に対応するentryだけを読む。
- 壊れたZIP、過大なZIP、許可されないentryはジョブを失敗にし、DB・S3の部分復元を始めない。

### 3. 全カテゴリを往復復元する

export payloadに含まれる設定、vocab candidates、overview figures、explainer figuresを復元する。論文の `latest_revision_id` は、復元されたrevisionへ対応付けて設定する。既存の冪等性方針を維持し、既存行を再作成しない。

- ユーザー設定はBYOK秘密情報を含めず、通常設定のみを復元する既存方針を維持する。
- 図のDBメタデータと対応バイナリを同じ復元キーで再接続する。
- 候補の status と vocab_entry_id は、移行先で有効な語彙IDにのみリンクする。

## テスト

次の回帰テストを先に追加する。

1. アップロード上限超過はS3への保存やジョブ作成なしで拒否する。
2. ZIP bomb相当のentryメタデータは、展開前にジョブ失敗となる。
3. 任意のmanifest keyではなく、再採番済みの移行先キーへだけアセットが書かれる。
4. settings、latest revision、vocab candidates、両種のfigureとそのアセットが、別ユーザーへの往復移行後に保持される。
5. import/exportの既存テスト、ruff、mypy、JSのbuild/lint/typecheckを実行する。

## 非対象

- 既存エクスポートフォーマットの破壊的変更
- 外部ユーザーの共有データをインポート時に統合する仕様の変更
- アップロード済みインポートZIPの自動削除ポリシー
