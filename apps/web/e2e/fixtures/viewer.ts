import { expect, type Locator, type Page } from "@playwright/test";

/** ビューアを開き、モード切替 SegmentedControl が見えるまで待つ。 */
export async function openViewer(page: Page, itemId: string, mode?: string): Promise<void> {
  const suffix = mode ? `?mode=${mode}` : "";
  await page.goto(`/papers/${itemId}${suffix}`);
  await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();
}

/** モード切替(訳文 / 対訳 / 原文 / PDF / 記事。plans/13 §4.2 M2-07 の 5 モード)。 */
export async function switchMode(
  page: Page,
  label: "訳文" | "対訳" | "原文" | "PDF" | "記事",
): Promise<void> {
  await page.getByRole("radio", { name: label, exact: true }).click();
}

/**
 * 要素テキストをドラッグ選択して pointerup を発火させる(選択メニュー起動)。
 * 決定: 垂直中央・幅比率での長距離ドラッグは、複数行に折り返す段落で引用/参照インライン
 * ボタン(user-select:none の既定 UA スタイル)を横切ると選択が空になることがあるため、
 * 常に先頭行の先頭付近(折り返し前・インライン要素が出現しにくい範囲)を短距離でドラッグする。
 */
export async function dragSelect(page: Page, locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  if (!box) throw new Error("target has no bounding box");
  const y = box.y + Math.min(14, box.height / 2);
  await page.mouse.move(box.x + 4, y);
  await page.mouse.down();
  await page.mouse.move(box.x + Math.min(120, Math.max(40, box.width * 0.6)), y, { steps: 10 });
  await page.mouse.up();
}
