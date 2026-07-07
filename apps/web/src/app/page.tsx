import { redirect } from "next/navigation";

/**
 * ルート `/` は既定の入口。認証コールバックは `/` へ 302 する(plans/01 §6.1)ため、
 * ここでログイン後の既定画面へ振り分ける。M0 は `/library` だったが、M1-10(ダッシュボード
 * 実装)で `/dashboard` に切替(plans/13 §1.5 の決定)。
 */
export default function RootIndexPage(): never {
  redirect("/dashboard");
}
