// PF-07(plans/12 §12.2): 拡張 check 応答 p50<500ms(Redis キャッシュヒット時)。
// 事前に 1 回叩いて LaTeX 判定を Redis に載せてから計測する。
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const COOKIE = __ENV.COOKIE || "";
const URL = __ENV.ARXIV_URL || "https://arxiv.org/abs/2209.03003";

export const options = {
  vus: 10,
  duration: "60s",
  thresholds: {
    http_req_duration: ["p(50)<500"],
    checks: ["rate>0.99"],
  },
};

const params = COOKIE ? { headers: { Cookie: COOKIE } } : {};

export function setup() {
  // キャッシュを温める。
  http.get(`${BASE_URL}/api/ingest/check?url=${encodeURIComponent(URL)}`, params);
}

export default function () {
  const res = http.get(`${BASE_URL}/api/ingest/check?url=${encodeURIComponent(URL)}`, params);
  check(res, { "check 200": (r) => r.status === 200 });
}
