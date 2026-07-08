"""SM-03(plans/12 §8.3・M2-17): チャット品質サンプル。実 API キーでの疎通確認。

``RUN_LLM_SMOKE=1`` のときのみ収集する(既定では即 skip)。CI のマージゲート(`python` job)は
`RUN_LLM_SMOKE` を設定しないため常に skip され、夜間ワークフロー `.github/workflows/llm-smoke.yml`
(`RUN_LLM_SMOKE=1 uv run pytest -m smoke`)からのみ実プロバイダへ到達する。

判定は plans/12 §8.3 の「機械判定可能な必要条件」のみ(LLM-as-judge は使わない):
- 「この論文には記載がない」質問 → 根拠チップ 0 件 + 定型語彙を含む
- 「実験設定の整理」(quick_action=experiment_setup) → Markdown 表(`|` 区切り)を含む

DB を使わず(ネットワークは実 LLM プロバイダのみ)、apps/api の本番コード(
``build_chat_request`` / ``StreamPipeline`` / ``EvidenceValidator``)をそのまま使って
実プロバイダ応答を検証する。ルーティングは ``packages/llm/{routing,models}.yaml`` の
シード既定チェーン(SM-01 と同一の「既定モデル」解決規則)から、運営キーが環境変数に
設定されているプロバイダのみでチェーンを組む。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yakudoku_llm
from yakudoku_api.chat.context_builder import build_chat_request
from yakudoku_api.chat.evidence import BlockRow, EvidenceValidator
from yakudoku_api.chat.prompts import QUICK_ACTION_TEMPLATES
from yakudoku_api.chat.stream_pipeline import StreamPipeline
from yakudoku_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from yakudoku_core.document.inlines import Inline
from yakudoku_core.search.rebuild import compute_index_rows
from yakudoku_llm.errors import ProviderChainExhausted
from yakudoku_llm.providers import build_provider
from yakudoku_llm.registry import ModelRegistry
from yakudoku_llm.router import ChainEntry, LLMRouter
from yakudoku_llm.routing import RoutingConfig

pytestmark = pytest.mark.smoke

# 運営キーの環境変数名(apps/worker/bootstrap.py・.github/workflows/llm-smoke.yml と同一マッピング)。
_OPERATOR_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "xai": ("XAI_API_KEY",),
}

# 「記載がない」旨の定型判定語彙(§8.3 の機械判定可能な必要条件)。
_NOT_STATED_MARKERS = (
    "記載がありません",
    "記載がない",
    "記載は見当たりません",
    "述べられていない",
    "言及がありません",
    "言及がない",
)


def _skip_unless_enabled() -> None:
    if os.environ.get("RUN_LLM_SMOKE") != "1":
        pytest.skip("RUN_LLM_SMOKE!=1 のため skip(plans/12 §8.3。夜間 llm-smoke.yml のみ収集)")


def _operator_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for provider, env_names in _OPERATOR_KEY_ENV.items():
        for env_name in env_names:
            value = os.environ.get(env_name, "").strip()
            if value:
                keys[provider] = value
                break
    return keys


def _build_chat_router() -> LLMRouter:
    """routing.yaml の chat チェーンから、運営キーが設定されたプロバイダのみで実ルータを組む。"""
    llm_root = Path(yakudoku_llm.__file__).resolve().parents[2]
    routing = RoutingConfig.from_yaml(llm_root / "routing.yaml")
    registry = ModelRegistry.from_yaml(llm_root / "models.yaml")
    keys = _operator_keys()
    chain: list[ChainEntry] = []
    for model_id in routing.tasks["chat"].chain:
        provider_name = registry.get(model_id).provider
        api_key = keys.get(provider_name)
        if not api_key:
            continue
        chain.append((provider_name, model_id, build_provider(provider_name, api_key)))
    if not chain:
        pytest.skip("運営キー未設定(OPENAI_API_KEY 等が Environment secrets に無い)")
    return LLMRouter(chain, registry=registry)


def _seed_document() -> DocumentContent:
    """§14 Rectified Flow シードを模した最小文書(ImageNet 等への言及は含まない)。"""
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(
                        id="blk-1-p1",
                        type="paragraph",
                        inlines=[
                            Inline(
                                t="text",
                                v=(
                                    "Rectified flow learns straight transport paths between "
                                    "two distributions by solving a least squares regression "
                                    "over the linear interpolation X_t = (1-t) X_0 + t X_1."
                                ),
                            )
                        ],
                    ),
                ],
            ),
            Section(
                id="sec-2",
                heading=SectionHeading(number="2", title="Experiments"),
                blocks=[
                    Block(
                        id="blk-2-p1",
                        type="paragraph",
                        inlines=[
                            Inline(
                                t="text",
                                v=(
                                    "We evaluate on a synthetic two-moons toy dataset and a "
                                    "CIFAR-10 subset, using a batch size of 256 and the Adam "
                                    "optimizer with learning rate 1e-4. No ImageNet "
                                    "experiments are reported in this paper."
                                ),
                            )
                        ],
                    ),
                    Block(
                        id="blk-2-p2",
                        type="paragraph",
                        inlines=[
                            Inline(
                                t="text",
                                v=(
                                    "The training objective boils down to a least squares "
                                    "regression against the straight-line velocity field."
                                ),
                            )
                        ],
                    ),
                ],
            ),
        ],
    )


def _validator_for(content: DocumentContent) -> EvidenceValidator:
    rows = [
        BlockRow(
            r.block_id,
            r.block_type,
            r.section_path,
            r.section_label,
            r.paragraph_ordinal,
            r.element_label,
        )
        for r in compute_index_rows(content)
    ]
    return EvidenceValidator("smoke-rev", rows)


async def _ask(router: LLMRouter, content: DocumentContent, question: str) -> tuple[str, int]:
    """1 ターン実行し (回答テキスト, 実在検証を通過した根拠件数) を返す。"""
    request = build_chat_request(
        content=content,
        revision_id="smoke-rev",
        title="Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow",
        authors_short="Liu et al.",
        venue_year="ICLR 2023",
        arxiv_id="2209.03003",
        user_content=question,
    )
    try:
        resp = await router.complete(task="chat", request=request, mode="generate")
    except ProviderChainExhausted as err:
        pytest.fail(f"全プロバイダで失敗した(SM-03): {err}")
    pipeline = StreamPipeline(_validator_for(content))
    events = list(pipeline.feed(resp.text)) + list(pipeline.finish())
    evidence_count = sum(1 for e in events if e.event == "evidence")
    return resp.text, evidence_count


async def test_sm03_unstated_question_yields_no_evidence_and_disclaims() -> None:
    """SM-03 前半: 記載のない質問 → 根拠チップ 0 件 + 「記載がない」旨の定型判定。"""
    _skip_unless_enabled()
    router = _build_chat_router()
    content = _seed_document()
    text, evidence_count = await _ask(router, content, "この論文で ImageNet の結果はどうでしたか?")
    assert evidence_count == 0, f"記載のない事項に根拠チップが付いた: {text!r}"
    assert any(marker in text for marker in _NOT_STATED_MARKERS), (
        f"「記載なし」の定型表現が見当たらない: {text!r}"
    )


async def test_sm03_experiment_setup_yields_markdown_table() -> None:
    """SM-03 後半: 「実験設定の整理」quick_action への回答が Markdown 表を含む。"""
    _skip_unless_enabled()
    router = _build_chat_router()
    content = _seed_document()
    question = QUICK_ACTION_TEMPLATES["experiment_setup"]
    text, _evidence_count = await _ask(router, content, question)
    assert "|" in text, f"Markdown 表(| 区切り)が見当たらない: {text!r}"
