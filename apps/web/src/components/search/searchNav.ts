import type { SearchAllData, SearchHit } from "@alinea/api-client";

/** `@alinea/api-client` は判別共用体に別名を出さないため、`SearchHit.target` から導出する。 */
export type SearchHitTarget = SearchHit["target"];

/**
 * `source`/`sort` クエリの列挙値も生成コードに named type が無いため、
 * `GET /api/search` のクエリ型(`SearchAllData`)から導出する(plans/03 §15.1)。
 */
export type SearchSourceFilter = NonNullable<SearchAllData["query"]["source"]>;
export type SearchSortOption = NonNullable<SearchAllData["query"]["sort"]>;

/**
 * 横断検索ヒットのナビゲーション・バッジ表示規則(plans/11 §4・§7 が正)。
 * 1e ドロップダウン(4a §4.3)・4e 全結果画面(4e §4.5)の両方で共有する。
 *
 * deviations(spec gap。呼び出し側で吸収):
 * - plans/11 §7 は注釈ヒットの遷移先に `panel=annotations&annotation={annotation_id}` を
 *   含めるが、実装済み API(`SearchHitTargetViewer`)は `library_item_id` と `anchor` のみで
 *   `annotation_id` を返さない。本ヘルパーは `target.kind` のみで判定するため、注釈ヒットも
 *   通常の `viewer` 遷移(該当ブロックへスクロール)にフォールバックする。
 * - plans/09-screens/4e は原文/訳文が両方ヒットしたケースを 2 行に展開し `paired_translation`
 *   スニペットを使う設計だが、実装済み API は 1 ヒットのまま `matched_in` に両方を含めるだけで
 *   訳文側スニペットを返さない。本ヘルパーは 1 行のまま「本文・原文」+「本文・訳文」の
 *   2 バッジを並記する形にフォールバックする(§4 の表の 3 行目と同じ表現)。
 */

/** 遷移先 URL(plans/11 §7 の規則)。`q` は `hl=` としてビューア側の一時ハイライトに渡す。 */
export function hrefForSearchTarget(target: SearchHitTarget, q: string): string {
  switch (target.kind) {
    case "note": {
      const params = new URLSearchParams({ panel: "notes", note: target.note_id, hl: q });
      return `/papers/${target.library_item_id}?${params.toString()}`;
    }
    case "chat": {
      const params = new URLSearchParams({
        panel: "chat",
        thread: target.thread_id,
        message: target.message_id,
        hl: q,
      });
      return `/papers/${target.library_item_id}?${params.toString()}`;
    }
    case "article": {
      const params = new URLSearchParams({
        view: "article",
        article_block: target.article_block_id,
        hl: q,
      });
      return `/papers/${target.library_item_id}?${params.toString()}`;
    }
    case "viewer":
    default: {
      // 書誌ヒット(anchor: null)は論文の先頭を開く(plans/11 §7)。
      if (!target.anchor) return `/papers/${target.library_item_id}`;
      const params = new URLSearchParams({ block: target.anchor.block_id, hl: q });
      return `/papers/${target.library_item_id}?${params.toString()}`;
    }
  }
}

/** ジャンプリンク文言(plans/03 §15.1・plans/11 §4)。 */
export function jumpLabelForTarget(kind: SearchHitTarget["kind"]): string {
  switch (kind) {
    case "note":
      return "メモを開く →";
    case "chat":
      return "スレッドを開く →";
    case "article":
      return "記事モードで開く →";
    case "viewer":
    default:
      return "該当位置へ →";
  }
}

export type SourceTone = "body" | "note" | "chat" | "article";

function toneForSource(source: SearchHit["source"]): SourceTone {
  if (source === "chat") return "chat";
  if (source === "article") return "article";
  if (source === "note" || source === "annotation") return "note";
  return "body";
}

/** 4e 全結果画面のバッジ文言(plans/11 §4)。body の両言語ヒットは 2 バッジ並記。 */
export function resultsBadges(hit: Pick<SearchHit, "source" | "matched_in">): {
  tone: SourceTone;
  label: string;
}[] {
  const tone = toneForSource(hit.source);
  if (hit.source !== "body") {
    const labels: Record<Exclude<SourceTone, "body">, string> = {
      note: "メモ",
      chat: "チャット",
      article: "記事",
    };
    return [{ tone, label: labels[tone as Exclude<SourceTone, "body">] }];
  }
  const matched = hit.matched_in ?? [];
  if (matched.length === 2) {
    return [
      { tone, label: "本文 · 原文" },
      { tone, label: "本文 · 訳文" },
    ];
  }
  if (matched[0] === "translation") return [{ tone, label: "本文 · 訳文" }];
  return [{ tone, label: "本文 · 原文" }];
}

/** 1e ドロップダウンのバッジ文言(plans/11 §4。source 単位で 1 個)。 */
export function previewBadge(source: SearchHit["source"]): { tone: SourceTone; label: string } {
  const tone = toneForSource(source);
  const labels: Record<SourceTone, string> = {
    body: "本文でヒット",
    note: "あなたのメモ",
    chat: "チャット履歴",
    article: "記事でヒット",
  };
  return { tone, label: labels[tone] };
}

/** スニペットのフォント(plans/11 §5.2 / 4e §4.5)。 */
export function snippetFontVar(hit: Pick<SearchHit, "source" | "snippet_lang">): string {
  if (hit.source === "body") {
    return hit.snippet_lang === "en" ? "var(--pr-font-en)" : "var(--pr-jp)";
  }
  return "var(--pr-font-ui)";
}

/** `generated_at`(ISO)→「M/D」(日は2桁ゼロ埋め。plans/09-screens/4e §4.5 の決定)。 */
export function formatArticleDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate().toString().padStart(2, "0")}`;
}
