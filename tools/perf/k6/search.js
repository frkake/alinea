// PF-06 相当(plans/12 §12.2): 横断検索 p95<3s、preview p50<300ms。
// M0 で /api/search が未提供の場合は 404 になり閾値未達で失敗するため、
// 検索 API 実装後に有効化する(それまでは perf.yml から除外可能)。
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const COOKIE = __ENV.COOKIE || "";
const Q = __ENV.QUERY || "EMA teacher";

export const options = {
  vus: 10,
  duration: "60s",
  thresholds: {
    "http_req_duration{name:search}": ["p(95)<3000"],
    "http_req_duration{name:preview}": ["p(50)<300"],
  },
};

const params = COOKIE ? { headers: { Cookie: COOKIE } } : {};

export default function () {
  http.get(`${BASE_URL}/api/search?q=${encodeURIComponent(Q)}`, {
    ...params,
    tags: { name: "search" },
  });
  http.get(`${BASE_URL}/api/search/preview?q=${encodeURIComponent(Q)}`, {
    ...params,
    tags: { name: "preview" },
  });
}
