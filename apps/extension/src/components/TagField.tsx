// タグ入力(3a §4.4 項3・§5.2)。チップ + 透明 input + サジェスト1件。
// Enter または「,」で確定追加(IME 変換中は無視)。× で削除。
import { useState, type KeyboardEvent } from "react";

export interface TagFieldProps {
  tags: string[];
  /** check.suggested_tags(未追加の先頭 1 件のみ表示)。 */
  suggested: string[];
  onAdd: (tag: string) => void;
  onRemove: (tag: string) => void;
}

export function TagField({ tags, suggested, onAdd, onRemove }: TagFieldProps) {
  const [draft, setDraft] = useState("");

  const commit = () => {
    const value = draft.trim();
    if (value.length === 0 || tags.includes(value)) {
      setDraft("");
      return;
    }
    onAdd(value);
    setDraft("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    // IME 変換中(isComposing)の Enter は確定として扱わない(3a §5.2)。
    if (event.nativeEvent.isComposing) return;
    const hasText = draft.trim().length > 0;
    if (event.key === ",") {
      event.preventDefault();
      commit();
      return;
    }
    if (event.key === "Enter" && hasText) {
      // 未確定テキストありの Enter はタグ確定を優先し、親への保存伝播を止める(3a §5.2)。
      event.preventDefault();
      event.stopPropagation();
      commit();
    }
    // 空欄での Enter は preventDefault せず親(SaveForm)へ伝播 → 保存が発火する。
  };

  const nextSuggestion = suggested.find((tag) => !tags.includes(tag));

  return (
    <div className="ext-row">
      <span className="ext-row-label">タグ</span>
      <div className="ext-tag-box">
        {tags.map((tag) => (
          <span key={tag} className="ext-tag-chip">
            {tag}
            <button
              type="button"
              className="ext-tag-remove"
              aria-label={`${tag} を削除`}
              onClick={() => onRemove(tag)}
            >
              ×
            </button>
          </span>
        ))}
        <input
          className="ext-tag-input"
          type="text"
          placeholder="追加…"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={commit}
          aria-label="タグを追加"
        />
        {nextSuggestion && (
          <button
            type="button"
            className="ext-tag-suggest"
            onClick={() => onAdd(nextSuggestion)}
          >
            提案: {nextSuggestion} +
          </button>
        )}
      </div>
    </div>
  );
}
