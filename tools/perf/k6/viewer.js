// PF-04(plans/12 §12.2): ビューア初期表示(翻訳済み) p50<2s / p95<5s。
// k6 0.57.0。実行: k6 run -e BASE_URL=http://localhost:8000 -e COOKIE="yk_session=…"
//   -e ITEM_ID=<uuid> -e REVISION_ID=<uuid> viewer.js
// ITEM_ID/REVISION_ID は tools/perf/get_rf_item.py で §14 シード(Rectified Flow)から解決する。
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const COOKIE = __ENV.COOKIE || "";
const ITEM_ID = __ENV.ITEM_ID || "";
const REVISION_ID = __ENV.REVISION_ID || "";

export const options = {
  vus: 10,
  duration: "60s",
  thresholds: {
    // docs/09 §1: 2s / 5s。
    "http_req_duration{name:viewer}": ["p(50)<2000", "p(95)<5000"],
    checks: ["rate>0.99"],
  },
};

const params = COOKIE ? { headers: { Cookie: COOKIE } } : {};

export default function () {
  const viewer = http.get(`${BASE_URL}/api/library-items/${ITEM_ID}/viewer`, {
    ...params,
    tags: { name: "viewer" },
  });
  check(viewer, { "viewer 200": (r) => r.status === 200 });
  const doc = http.get(`${BASE_URL}/api/revisions/${REVISION_ID}/document`, {
    ...params,
    tags: { name: "document" },
  });
  check(doc, { "document 200": (r) => r.status === 200 });
}
