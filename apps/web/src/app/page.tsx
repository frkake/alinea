import { redirect } from "next/navigation";

/**
 * ルート `/` は既定の入口。認証コールバックは `/` へ 302 する(plans/01 §6.1)ため、
 * ここでログイン後の既定画面 `/library` へ振り分ける(Global Constraints)。
 */
export default function RootIndexPage(): never {
  redirect("/library");
}
