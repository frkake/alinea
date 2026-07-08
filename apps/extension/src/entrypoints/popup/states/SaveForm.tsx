// 状態1: 保存前(3a §4.4・§5.2)。書誌プレビュー + 品質見込み + ステータス3択 +
// タグ + コレクション + ひとことメモ + 保存(Enter)。
// コレクション欄は docs/10 §2 の決定により M2 で表示解禁(XT-03。plans/13 §4.2)。
import { useState, type KeyboardEvent } from "react";

import { StatusPillRow } from "@/components/StatusPillRow";
import { TagField } from "@/components/TagField";
import type { SaveStatus } from "@/lib/status";

export interface SaveFormCollection {
  id: string;
  name: string;
}

export interface SaveFormPreview {
  title: string;
  /** 「Liu, Gong, Liu · ICLR 2023 · arXiv:2209.03003 v3」等。null なら非表示。 */
  metaLine?: string | null;
  /** true→品質A見込み / false→B見込み / null→行非表示(3a §5.2)。 */
  latexAvailable?: boolean | null;
  /** check.suggested_tags。 */
  suggestedTags?: string[];
  /** GET /api/collections の一覧(M2 で表示。空配列なら「なし」のみ選べる)。 */
  collections?: SaveFormCollection[];
}

export interface SavePayload {
  status: SaveStatus;
  tags: string[];
  quickNote: string;
  collectionId: string | null;
}

export interface SaveFormProps {
  preview: SaveFormPreview;
  onSave?: (payload: SavePayload) => void;
  saving?: boolean;
  /** 保存失敗などのエラー行(3a §5.1)。 */
  error?: string | null;
}

export function SaveForm({ preview, onSave, saving = false, error = null }: SaveFormProps) {
  const [status, setStatus] = useState<SaveStatus>("planned");
  const [tags, setTags] = useState<string[]>([]);
  const [quickNote, setQuickNote] = useState("");
  const [collectionId, setCollectionId] = useState<string | null>(null);

  const triggerSave = () => {
    if (saving) return;
    onSave?.({ status, tags, quickNote: quickNote.trim(), collectionId });
  };

  // Enter で保存(3a §5.2)。タグ入力に未確定テキストがある場合は TagField が
  // stopPropagation してタグ確定を優先する。ボタン上の Enter は各自の click に委ねる。
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    if ((event.target as HTMLElement).tagName === "BUTTON") return;
    event.preventDefault();
    triggerSave();
  };

  return (
    <div className="ext-body" onKeyDown={onKeyDown}>
      <div className="ext-bib">
        <div className="ext-bib-title">{preview.title}</div>
        {preview.metaLine && <div className="ext-bib-meta">{preview.metaLine}</div>}
        {preview.latexAvailable === true && (
          <div className="ext-quality ext-quality-a">✓ LaTeX ソースあり — 品質レベル A 見込み</div>
        )}
        {preview.latexAvailable === false && (
          <div className="ext-quality ext-quality-b">LaTeX ソースなし — 品質レベル B 見込み</div>
        )}
      </div>

      <StatusPillRow value={status} onChange={setStatus} />

      <TagField
        tags={tags}
        suggested={preview.suggestedTags ?? []}
        onAdd={(tag) => setTags((current) => (current.includes(tag) ? current : [...current, tag]))}
        onRemove={(tag) => setTags((current) => current.filter((t) => t !== tag))}
      />

      <div className="ext-row">
        <span className="ext-row-label" id="ext-collection-label">
          コレクション
        </span>
        <select
          className="ext-collection-select"
          aria-labelledby="ext-collection-label"
          value={collectionId ?? ""}
          onChange={(event) => setCollectionId(event.target.value || null)}
        >
          <option value="">なし</option>
          {(preview.collections ?? []).map((collection) => (
            <option key={collection.id} value={collection.id}>
              {collection.name}
            </option>
          ))}
        </select>
      </div>

      <input
        className="ext-note-input"
        type="text"
        maxLength={200}
        placeholder="ひとことメモ…"
        value={quickNote}
        onChange={(event) => setQuickNote(event.target.value)}
        aria-label="ひとことメモ"
      />

      <button
        type="button"
        className="ext-btn ext-btn-save"
        onClick={triggerSave}
        disabled={saving}
      >
        {saving ? (
          "保存中…"
        ) : (
          <>
            保存
            <span className="ext-keycap" aria-hidden="true">
              ⏎
            </span>
          </>
        )}
      </button>

      {error && <div className="ext-error-line">{error}</div>}

      <div className="ext-privacy-note">URL のみを送信します — 取得・解析はサーバーで実行</div>
    </div>
  );
}
