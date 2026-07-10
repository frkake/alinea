# Line Prism アプリアイコン設計

> 作成日: 2026-07-11 / 設計承認: ユーザー（2026-07-11）

## 1. 目的

Alinea の既存「A＋下線」モノグラムを使わず、論文の原文が対訳と理解へ展開される体験を
表す新しいアプリアイコンを生成する。Web、ブラウザ拡張、画面内ロゴの各接点を同じ
シンボルへ統一する。ただし、生成候補をユーザーが確認するまでは既存資産や参照コードを
変更しない。

## 2. 採用コンセプト

名称は「Line Prism」とする。左から入る一本の線が中央の小さな菱形の読解レンズを通り、
右側で二本の線へ分岐する。一本の原文から原文・訳文の並列表示と理解が生まれる様子を、
文字や既存の A モノグラムを使わずに表現する。

## 3. ビジュアル仕様

- 形状: 青灰色のスクエアと、中央に配置した Line Prism シンボル。生成マスターは背景を
  全面に敷き、画面内ロゴ用の角丸と透過角は派生画像の決定的なマスクで作る
- 背景色: `#3E5C76`
- 主線: オフホワイト。高コントラストかつ 16 px でも途切れない太さ
- 副線: 既存 UI と調和する淡いセージ色 `#DDE8E1`
- 構図: 正方形中央配置。外周とシンボルの間に十分なセーフエリアを確保する
- 表現: フラット、幾何学的、ベクター風、明確なシルエット
- 禁止: 文字、A モノグラム、紙や本の具象画、影、グラデーション、3D、モックアップ、
  写真質感、透かし、追加装飾

ワードマーク「Alinea」の文字列と既存書体は維持し、シンボルだけを置き換える。

## 4. 生成仕様

ユーザーが確認済みの CLI フォールバックを使い、`gpt-image-2` で 1024 × 1024 px の
正方形 PNG を一案生成する。候補は `output/imagegen/alinea-line-prism-preview.png` に保存し、
この段階ではアプリから参照しない。

最終プロンプトは次の内容を基準とする。

```text
Use case: logo-brand
Asset type: app icon master for a scholarly reading application
Primary request: Create a completely original abstract symbol named “Line Prism”: one continuous
off-white line enters from the left, passes through a small central diamond-shaped reading lens,
and separates into two clearly spaced output lines on the right, representing one source text
unfolding into parallel original/translation reading and deeper understanding.
Style/medium: flat vector-like geometric app icon; minimal; precise; strong silhouette
Composition/framing: centered symbol on a square canvas; generous safe area; legible at 16 px
Color palette: full-bleed solid slate-blue #3E5C76 background, warm off-white input and upper
output line, pale sage #DDE8E1 lower output line
Constraints: completely new symbol; no letterforms; no A monogram; no text; no gradients; no shadows;
no 3D; no mockup; no paper or book pictogram; no watermark; crisp clean edges
```

生成後は確認専用の派生画像を作り、1024 px、128 px、32 px、16 px の表示で分岐・菱形・
二本線が判別できるかを目視する。この派生画像もアプリからは参照しない。小サイズで潰れる
場合は、コンセプトを変えずに線幅、間隔、セーフエリアだけを一度調整して再生成する。

## 5. 承認ゲート

生成候補と小サイズのプレビューをユーザーへ提示する。ユーザーが設定を明示的に承認するまで、
次の操作は行わない。

- 既存アイコンの上書き
- Web の metadata アイコン変更
- Web の画面内ブランドマーク変更
- 拡張機能アイコンとポップアップ内マークの変更
- 既存ブランド SVG の変更

候補が却下された場合は、ユーザーが指定した一点だけを変えて再生成し、再び承認を得る。

## 6. 承認後の設定範囲

承認されたマスターから高品質に縮小した派生 PNG を作り、次を同じシンボルへ統一する。

- Next.js の favicon / アプリアイコン
- Web のヘッダー、ログイン、共有ページで使う画面内ブランドマーク
- `apps/web/public/brand` のブランド資産
- Chrome / Edge 拡張の 16、32、48、128 px アイコン
- 拡張ポップアップ内のブランドマーク

派生画像はマスターから決定的に生成できるようにし、古い A モノグラムを再生成する既存処理を
残さない。ワードマークの文字、レイアウト、アクセシビリティ上のラベルは維持する。

## 7. 検証

- 各 PNG の形式、寸法、色モードを機械的に確認する
- 16 px と 32 px でシンボルが潰れず、背景とのコントラストが保たれることを目視確認する
- Web の対象コンポーネントテスト、型チェック、lint を実行する
- 拡張の対象コンポーネントテスト、型チェック、lint、ビルドを実行する
- 生成物に文字、A モノグラム、透かし、意図しない影やグラデーションがないことを確認する
- Git 差分を確認し、承認前の生成段階では既存アプリ資産が無変更であることを確認する

## 8. 受け入れ基準

- Line Prism が既存 A モノグラムと明確に異なる完全新規のシンボルである
- 一本の入力線、中央の読解レンズ、二本の出力線が小サイズでも認識できる
- 配色が Alinea の既存 UI と調和する
- 生成候補の提示前に既存アイコンや画面内ロゴを変更していない
- ユーザーの設定承認後に限り、Web・拡張・画面内ロゴが同じシンボルへ更新される
