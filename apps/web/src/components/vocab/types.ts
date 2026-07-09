/**
 * `@alinea/api-client` の生成物には `VocabKind` / `ReviewResult` の named type alias が
 * 存在しない(各フィールド定義にインライン union として埋め込まれているのみ)。plans/03 §11 の
 * 名前をこちらで再定義し、画面内の共有型として使う。
 */
export type VocabKind = "word" | "collocation" | "idiom";
export type ReviewResult = "again" | "good";
