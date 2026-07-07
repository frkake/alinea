import { expect, type Locator, type Page } from "@playwright/test";

/** ビューアを開き、モード切替 SegmentedControl が見えるまで待つ。 */
export async function openViewer(page: Page, itemId: string, mode?: string): Promise<void> {
  const suffix = mode ? `?mode=${mode}` : "";
  await page.goto(`/papers/${itemId}${suffix}`);
  await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();
}

/** モード切替(訳文 / 対訳 / 原文)。 */
export async function switchMode(page: Page, label: "訳文" | "対訳" | "原文"): Promise<void> {
  await page.getByRole("radio", { name: label, exact: true }).click();
}

/** 要素テキストをドラッグ選択して pointerup を発火させる(選択メニュー起動)。 */
export async function dragSelect(page: Page, locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  if (!box) throw new Error("target has no bounding box");
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + 8, y);
  await page.mouse.down();
  await page.mouse.move(box.x + Math.max(40, box.width * 0.6), y, { steps: 10 });
  await page.mouse.up();
}
