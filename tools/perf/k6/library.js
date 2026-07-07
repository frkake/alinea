// PF-02(plans/12 §12.2): ライブラリ・ダッシュボード表示 p50<1s / p95<3s。
// k6 0.57.0。実行: k6 run -e BASE_URL=http://localhost:8000 -e COOKIE="yk_session=…" library.js
// 認証必須エンドポイントのため COOKIE を渡す(未指定なら anonymous で 401 になり閾値未達で失敗)。
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const COOKIE = __ENV.COOKIE || "";

export const options = {
  vus: 10,
  duration: "60s",
  thresholds: {
    // docs/09 §1: 1s / 3s。
    http_req_duration: ["p(50)<1000", "p(95)<3000"],
    checks: ["rate>0.99"],
  },
};

const params = COOKIE ? { headers: { Cookie: COOKIE } } : {};

export default function () {
  const list = http.get(`${BASE_URL}/api/library-items?limit=50`, params);
  check(list, { "library-items 200": (r) => r.status === 200 });
  const facets = http.get(`${BASE_URL}/api/library-items/facets`, params);
  check(facets, { "facets 200": (r) => r.status === 200 });
  // plans/12 §12.2: 対象操作は「ライブラリ・ダッシュボード表示」の両方(docs/09 §1)。
  const dashboard = http.get(`${BASE_URL}/api/dashboard`, params);
  check(dashboard, { "dashboard 200": (r) => r.status === 200 });
}
