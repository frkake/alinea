import { clsx, type ClassValue } from "clsx";

/** className 合成ユーティリティ(plans/08 §5: className の追加合成のみ許可)。 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
