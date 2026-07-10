"""SM-04(plans/12 §8.3・M2-17): 解説図 1 枚の実プロバイダ生成スモーク。

``RUN_LLM_SMOKE=1`` のときのみ収集する(既定では即 skip)。CI のマージゲート(`python` job)は
`RUN_LLM_SMOKE` を設定しないため常に skip され、夜間ワークフロー `.github/workflows/llm-smoke.yml`
(`RUN_LLM_SMOKE=1 uv run pytest -m smoke`)からのみ実プロバイダへ到達する。

判定は plans/12 §8.3 の「機械判定可能な必要条件」のみ: PNG としてデコード可能・1536x1024・
プロンプトに技術模式図の構成指示(``EXPLAINER_STYLE_PREAMBLE``)が含まれた状態で
生成が成功すること。DB・S3 は使わず(``generate_explainer_figure.build_explainer_prompt`` と
``ImageRouter`` を直接使う)、ネットワークは実画像プロバイダのみに限定する。
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import alinea_llm
import pytest
from alinea_llm.errors import ProviderChainExhausted
from alinea_llm.providers import build_image_provider
from alinea_llm.registry import ModelRegistry
from alinea_llm.router import ImageChainEntry, ImageRouter
from alinea_llm.routing import RoutingConfig
from alinea_worker.tasks.generate_explainer_figure import (
    EXPLAINER_STYLE_PREAMBLE,
    build_explainer_prompt,
)
from PIL import Image

pytestmark = pytest.mark.smoke

# 運営キーの環境変数名(apps/worker/bootstrap.py・.github/workflows/llm-smoke.yml と同一マッピング)。
_OPERATOR_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "xai": ("XAI_API_KEY",),
}


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


def _build_image_router() -> ImageRouter:
    """routing.yaml の explainer_image チェーンから、運営キーが設定されたプロバイダのみで
    実 ImageRouter を組む(SM-01 と同一の既定モデル解決規則)。
    """
    llm_root = Path(alinea_llm.__file__).resolve().parents[2]
    routing = RoutingConfig.from_yaml(llm_root / "routing.yaml")
    registry = ModelRegistry.from_yaml(llm_root / "models.yaml")
    keys = _operator_keys()
    chain: list[ImageChainEntry] = []
    for model_id in routing.tasks["explainer_image"].chain:
        provider_name = registry.get(model_id).provider
        api_key = keys.get(provider_name)
        if not api_key:
            continue
        chain.append((provider_name, model_id, build_image_provider(provider_name, api_key)))
    if not chain:
        pytest.skip("運営キー未設定(OPENAI_API_KEY/GEMINI_API_KEY/XAI_API_KEY が無い)")
    return ImageRouter(chain, registry=registry)


async def test_sm04_explainer_image_generation_succeeds() -> None:
    """SM-04: 解説図 1 枚実生成。PNG デコード可能・1536x1024・技術図指示を含む。"""
    _skip_unless_enabled()
    router = _build_image_router()

    image_brief_en = (
        "A minimal diagram showing two point clouds (left: noise distribution, right: data "
        "distribution) connected by straight directional arrows, illustrating a rectified "
        "flow that transports samples along straight paths."
    )
    prompt = build_explainer_prompt(image_brief_en)
    assert EXPLAINER_STYLE_PREAMBLE in prompt
    assert "technical explanatory schematic" in prompt
    assert "3 to 7 visually distinct components" in prompt

    try:
        result = await router.generate(prompt, task="explainer_image")
    except ProviderChainExhausted as err:
        pytest.fail(f"全プロバイダで失敗した(SM-04): {err}")

    assert result.media_type == "image/png"
    with Image.open(io.BytesIO(result.image_bytes)) as img:
        assert img.format == "PNG"
        assert img.size == (1536, 1024)
