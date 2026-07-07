/**
 * api-client の `throwOnError: true` は Problem Details(apps/api の RFC 9457 実装)を
 * そのまま throw する。422 判定は `code === "validation_error"` で行う(4f §5.4)。
 */
export function isValidationErrorLike(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code?: unknown }).code === "validation_error"
  );
}
