/**
 * @yakudoku/api-client — OpenAPI から生成した型付きクライアントの公開エントリ。
 *
 * 生成物(./generated)は @hey-api/openapi-ts が apps/api の openapi.json から出力する
 * (手編集禁止・CI の openapi-drift ジョブでドリフト検出。plans/00 §2・plans/03 §1.10)。
 * 本ファイルだけが手書きで、生成された fetch クライアントを本アプリ向けに設定する薄い
 * ラッパである。apps/web / apps/extension はここから型と SDK を import する。
 *
 * - credentials: "include" — セッション Cookie(yk_session)を常に送る。
 * - baseUrl: ""(空)— 生成された各操作の URL は既に `/api/...`(plans/03 §1.1 のパス規約)
 *   を含むため、ベース URL は空にして相対 `/api/...`(同一オリジン)へ送る。ここで "/api"
 *   を与えると `/api` + `/api/...` = `/api/api/...` と二重化して全リクエストが 404 になる。
 */
import { client } from "./generated/client.gen";

client.setConfig({
  baseUrl: "",
  credentials: "include",
});

export { client };
export * from "./generated";
