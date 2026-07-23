"""GitHub コード対応解析の共有契約(型・定数・費用見積り・サーバー検証)。

docs/superpowers/specs/2026-07-17-huggingface-code-correspondence-design.md §5・§7・§9。

このモジュールは api / worker の両方が import する純ロジック(DB・ネットワーク非依存)。

方針(セキュリティ上の要):
- **サーバーが全対応を実データで検証する**(:func:`verify_correspondences`)。LLM が返す
  path・行範囲・excerpt・paper anchor を抽出済みリポジトリの実バイトと照合し、一つでも
  一致しない対応は保存しない。コード内コメントの「前の指示を無視せよ」が構造化出力の
  検証規則を変えることは決してない(検証は純関数で、コード本文の内容に依存しない)。
- **費用は保守的に見積もる**(:func:`estimate_tokens_and_cost`)。過小評価で予算を破らない。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from alinea_llm.types import JsonSchemaSpec

# 解析アルゴリズムのバージョン。プロンプト・chunk 規則・検証規則を変えたら上げる。
# 一意制約 (user_id, revision_id, resource_id, commit_sha, analysis_version) の一部。
ANALYSIS_VERSION = "ca-2026-07-17.1"

# 論文から抽出する主張の上限(§9 手順4)。
MAX_CLAIMS = 30

# code_excerpt はソース全文を保存しない(§5)。500 文字上限。
EXCERPT_MAX_CHARS = 500

# confidence の値域(§5)。
CONFIDENCE_VALUES = ("high", "medium", "low")

# 見積り有効期間(§10。10 分)。
ESTIMATE_TTL_SECONDS = 600

# 保守的なトークン見積り係数(過小評価で予算を破らないため 1 トークン ≈ 3 文字と多めに数える)。
_CHARS_PER_TOKEN = 3.0
# 1 主張あたり LLM へ渡す chunk 数(§8 上位候補)。
CHUNKS_PER_CLAIM = 6
# 出力トークンの 1 主張あたり保守見積り(説明 + 対応数件の JSON)。
_OUTPUT_TOKENS_PER_CLAIM = 400
# システム/プロンプト固定オーバーヘッド(トークン)。
_SYSTEM_OVERHEAD_TOKENS = 1500


@dataclass(frozen=True)
class AnalysisClaim:
    """論文側の主張候補(block anchor 付き)。"""

    section_id: str
    block_id: str
    claim_text: str
    keywords: tuple[str, ...] = ()

    def anchor(self, revision_id: str) -> dict[str, Any]:
        return {
            "revision_id": revision_id,
            "block_id": self.block_id,
            "section_id": self.section_id,
        }


@dataclass(frozen=True)
class Correspondence:
    """検証済みの一対応(§5・§11 code_correspondences 相当)。"""

    paper_anchor: dict[str, Any]
    claim_text: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    code_excerpt: str
    explanation_ja: str
    confidence: str


# --------------------------------------------------------------------------- #
# LLM 構造化出力スキーマ(§8・§9)
# --------------------------------------------------------------------------- #
CODE_CORRESPONDENCE_SCHEMA_NAME = "code_correspondence_v1"

CODE_CORRESPONDENCE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "correspondences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "symbol": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "excerpt": {"type": "string"},
                    "explanation": {"type": "string"},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE_VALUES)},
                },
                "required": [
                    "path",
                    "symbol",
                    "start_line",
                    "end_line",
                    "excerpt",
                    "explanation",
                    "confidence",
                ],
            },
        }
    },
    "required": ["correspondences"],
}

CODE_CORRESPONDENCE_SCHEMA_SPEC = JsonSchemaSpec(
    name=CODE_CORRESPONDENCE_SCHEMA_NAME,
    json_schema=CODE_CORRESPONDENCE_JSON_SCHEMA,
)


def idempotency_key(
    *,
    user_id: str,
    revision_id: str,
    resource_id: str,
    commit_sha: str,
    analysis_version: str = ANALYSIS_VERSION,
) -> str:
    """同一対象の重複ジョブ作成を防ぐ冪等キー(§7)。"""
    return f"code_analysis:{user_id}:{revision_id}:{resource_id}:{commit_sha}:{analysis_version}"


def _normalize_ws(text: str) -> str:
    """空白を畳んで比較用に正規化する(excerpt 照合のため)。"""
    return " ".join(text.split())


def _excerpt_matches_source(
    excerpt: str, file_lines: list[str], start_line: int, end_line: int
) -> bool:
    """excerpt が実ファイルの [start_line, end_line] 範囲の実バイトと一致するか。

    行範囲のスライスを空白正規化した文字列に、excerpt の空白正規化文字列が
    部分列として含まれることを要求する。LLM が捏造した excerpt(実バイトに無い)を弾く。
    空 excerpt は不一致(検証失敗)扱い。
    """
    normalized_excerpt = _normalize_ws(excerpt)
    if not normalized_excerpt:
        return False
    slice_text = _normalize_ws("\n".join(file_lines[start_line - 1 : end_line]))
    return normalized_excerpt in slice_text


def verify_correspondences(
    raw_correspondences: list[dict[str, Any]],
    *,
    files: dict[str, str],
    valid_block_ids: set[str],
    claim: AnalysisClaim,
    revision_id: str,
) -> list[Correspondence]:
    """LLM が返した対応を実データで検証し、合格分だけ返す(§9 手順9)。

    検証(すべて満たすもののみ採用):
    1. ``path`` が抽出済みファイルに実在する。
    2. ``start_line``/``end_line`` がファイル行数内で ``1 <= start <= end <= n``。
    3. ``excerpt`` が実ファイルの当該行範囲の実バイトと一致する。
    4. paper anchor(claim.block_id)が実在ブロックである。
    5. ``confidence`` が値域内。

    **prompt injection 耐性**: この関数はコード本文の内容(コメントの命令など)を一切
    解釈しない。純粋に構造照合のみを行うため、コード内の指示で規則が変わることはない。
    """
    # paper anchor は claim 側で固定(実在ブロックのみ処理する前提)。念のため検証する。
    if claim.block_id not in valid_block_ids:
        return []

    verified: list[Correspondence] = []
    seen: set[tuple[str, int, int]] = set()
    for item in raw_correspondences:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or path not in files:
            continue
        start_line = item.get("start_line")
        end_line = item.get("end_line")
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        file_lines = files[path].split("\n")
        n_lines = len(file_lines)
        if not (1 <= start_line <= end_line <= n_lines):
            continue
        excerpt = item.get("excerpt")
        if not isinstance(excerpt, str) or not _excerpt_matches_source(
            excerpt, file_lines, start_line, end_line
        ):
            continue
        confidence = item.get("confidence")
        if confidence not in CONFIDENCE_VALUES:
            continue
        dedup = (path, start_line, end_line)
        if dedup in seen:
            continue
        seen.add(dedup)

        raw_symbol = item.get("symbol")
        symbol = raw_symbol if isinstance(raw_symbol, str) else ""
        raw_explanation = item.get("explanation")
        explanation = raw_explanation if isinstance(raw_explanation, str) else ""
        verified.append(
            Correspondence(
                paper_anchor=claim.anchor(revision_id),
                claim_text=claim.claim_text,
                path=path,
                symbol=symbol,
                start_line=start_line,
                end_line=end_line,
                code_excerpt=excerpt[:EXCERPT_MAX_CHARS],
                explanation_ja=explanation,
                confidence=str(confidence),
            )
        )
    return verified


# --------------------------------------------------------------------------- #
# 費用見積り(§7・§10)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelPricing:
    """モデルの 100 万トークンあたり価格(USD)。llm_models.pricing 由来。"""

    input_per_mtok: float
    output_per_mtok: float
    embedding_per_mtok: float = 0.02  # text-embedding-3-small 既定


@dataclass(frozen=True)
class CostEstimate:
    """見積り結果(§10 CodeAnalysisEstimateResponse 相当)。"""

    files: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_embedding_tokens: int
    estimated_cost_usd: Decimal
    chunk_count: int = 0


def _tokens_from_chars(chars: int) -> int:
    return int(chars / _CHARS_PER_TOKEN) + 1


def estimate_tokens_and_cost(
    *,
    total_code_chars: int,
    chunk_count: int,
    files: int,
    claim_count: int,
    pricing: ModelPricing,
    chunks_per_claim: int = CHUNKS_PER_CLAIM,
) -> CostEstimate:
    """保守的なトークン・費用見積り(§7)。

    - 埋め込み: 全 chunk + 全 claim を 1 回埋め込む(再順位付け)。
    - LLM 入力: 各 claim に上位 ``chunks_per_claim`` chunk を渡す想定。1 chunk の平均文字数を
      total_code_chars / chunk_count で見積り、claim 数と掛ける(chunk 総量で上限)。
    - LLM 出力: 1 claim あたり固定 + システムオーバーヘッド。
    すべて過小評価を避けるため多めに出す。
    """
    claim_count = max(claim_count, 1)
    chunk_count = max(chunk_count, 0)

    # 埋め込みトークン: コード全量 + 主張分(主張は少量なので係数込みで概算)。
    embedding_tokens = _tokens_from_chars(total_code_chars) + claim_count * 64

    # LLM 入力トークン: 各 claim に渡す chunk 群。全 chunk を超えないよう min で抑える。
    avg_chunk_chars = (total_code_chars / chunk_count) if chunk_count else 0.0
    per_claim_chunks = min(chunks_per_claim, chunk_count) if chunk_count else 0
    llm_code_chars = int(avg_chunk_chars * per_claim_chunks * claim_count)
    llm_code_chars = min(llm_code_chars, total_code_chars * 4)  # 保守上限(重複送信を許容)
    input_tokens = _tokens_from_chars(llm_code_chars) + _SYSTEM_OVERHEAD_TOKENS + claim_count * 128
    output_tokens = claim_count * _OUTPUT_TOKENS_PER_CLAIM

    cost = (
        Decimal(input_tokens) * Decimal(str(pricing.input_per_mtok))
        + Decimal(output_tokens) * Decimal(str(pricing.output_per_mtok))
        + Decimal(embedding_tokens) * Decimal(str(pricing.embedding_per_mtok))
    ) / Decimal(1_000_000)
    cost = cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    return CostEstimate(
        files=files,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_embedding_tokens=embedding_tokens,
        estimated_cost_usd=cost,
        chunk_count=chunk_count,
    )


def expires_at(
    now: dt.datetime | None = None, ttl_seconds: int = ESTIMATE_TTL_SECONDS
) -> dt.datetime:
    """見積りの失効時刻(§10。既定 10 分)。"""
    base = now or dt.datetime.now(dt.UTC)
    return base + dt.timedelta(seconds=ttl_seconds)


@dataclass
class ClaimSet:
    """抽出済み主張の束(worker が retrieval へ渡す)。"""

    revision_id: str
    claims: list[AnalysisClaim] = field(default_factory=list)

    @property
    def block_ids(self) -> set[str]:
        return {c.block_id for c in self.claims}
