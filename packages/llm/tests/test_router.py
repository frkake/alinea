"""LLMRouter / Fake プロバイダの単体テスト(PY-LLM-01〜07)。

実プロバイダへの実通信は行わない(FakeLLMProvider のみ)。plans/04 §17・
plans/12 §8.1 の決定的応答規則を検証する。
"""

from __future__ import annotations

import pytest
from yakudoku_llm.errors import ErrorKind, ProviderChainExhausted, ProviderError
from yakudoku_llm.router import LLMRouter, RetryConfig
from yakudoku_llm.testing.fake_provider import FakeLLMProvider
from yakudoku_llm.types import JsonSchemaSpec


async def _noop_sleep(_seconds: float) -> None:
    return None


# --- PY-LLM-04: フォールバック連鎖(計画 Step 1 の逐語テスト) ---------------


async def test_router_falls_back_on_primary_error() -> None:
    primary = FakeLLMProvider(fail=True)
    secondary = FakeLLMProvider(responses={"translate": "訳文"})
    router = LLMRouter(chain=[("primary", "m1", primary), ("secondary", "m2", secondary)])
    resp = await router.complete(task="translate", prompt="Rectified flow")
    assert resp.text == "訳文"
    assert resp.fallback_rank == 1


# --- PY-LLM-01: Fake 決定性(同一入力→同一出力) ---------------------------


async def test_fake_provider_is_deterministic() -> None:
    p = FakeLLMProvider()
    router = LLMRouter(chain=[("primary", "m1", p)])
    r1 = await router.complete(task="translate", prompt="⟦x1⟧ hello ⟦x2⟧ world")
    r2 = await router.complete(task="translate", prompt="⟦x1⟧ hello ⟦x2⟧ world")
    assert r1.text == r2.text
    # §8.1: 入力プレースホルダを保持し、逆順連結の決定的マーカーを含む
    assert "⟦x1⟧" in r1.text and "⟦x2⟧" in r1.text
    assert "⟦x2⟧⟦x1⟧" in r1.text  # 出現逆順に連結(x2, x1)
    assert r1.fallback_rank == 0


# --- PY-LLM-02: 故障注入(retryable は再試行→フォールバック、計測記録) -----


async def test_retryable_error_retries_then_falls_back() -> None:
    primary = FakeLLMProvider(fail=True, error_kind=ErrorKind.RATE_LIMIT)
    secondary = FakeLLMProvider(responses={"chat": "回答"})
    router = LLMRouter(
        chain=[("primary", "m1", primary), ("secondary", "m2", secondary)],
        retry=RetryConfig(max_attempts=3),
        sleep=_noop_sleep,
    )
    resp = await router.complete(task="chat", prompt="hi")
    assert resp.text == "回答"
    assert resp.fallback_rank == 1
    # primary は max_attempts 回試行した
    assert primary.calls == 3
    assert secondary.calls == 1


async def test_fatal_error_aborts_chain_without_fallback() -> None:
    primary = FakeLLMProvider(fail=True, error_kind=ErrorKind.INVALID_REQUEST)
    secondary = FakeLLMProvider(responses={"chat": "回答"})
    router = LLMRouter(
        chain=[("primary", "m1", primary), ("secondary", "m2", secondary)],
        sleep=_noop_sleep,
    )
    with pytest.raises(ProviderChainExhausted):
        await router.complete(task="chat", prompt="hi")
    assert secondary.calls == 0  # フォールバックしない(P3: 実装バグを隠さない)


async def test_chain_exhausted_when_all_fail() -> None:
    p1 = FakeLLMProvider(fail=True, error_kind=ErrorKind.MODEL_NOT_FOUND)
    p2 = FakeLLMProvider(fail=True, error_kind=ErrorKind.BILLING)
    router = LLMRouter(chain=[("a", "m1", p1), ("b", "m2", p2)], sleep=_noop_sleep)
    with pytest.raises(ProviderChainExhausted) as exc:
        await router.complete(task="chat", prompt="hi")
    assert len(exc.value.errors) == 2
    assert str(exc.value) == "all providers failed for task=chat; last_error=fake/m2 billing"


# --- PY-LLM-03: structured output(Pydantic/jsonschema 検証済み parsed) -----


async def test_structured_output_returns_parsed_json() -> None:
    schema = JsonSchemaSpec(
        name="vocab_entry_v1",
        json_schema={
            "type": "object",
            "properties": {"term": {"type": "string"}},
            "required": ["term"],
        },
    )
    p = FakeLLMProvider(structured={"vocab_entry_v1": {"term": "rectified flow"}})
    router = LLMRouter(chain=[("primary", "m1", p)])
    resp = await router.complete(task="vocab", prompt="term?", schema=schema, mode="structured")
    assert resp.parsed == {"term": "rectified flow"}


async def test_structured_unknown_schema_raises_schema_validation() -> None:
    schema = JsonSchemaSpec(name="unknown_schema_v9", json_schema={"type": "object"})
    p = FakeLLMProvider()
    router = LLMRouter(chain=[("primary", "m1", p)], sleep=_noop_sleep)
    with pytest.raises(ProviderChainExhausted) as exc:
        await router.complete(task="vocab", prompt="x", schema=schema, mode="structured")
    assert exc.value.errors[-1].kind == ErrorKind.SCHEMA_VALIDATION


# --- PY-LLM-05: count_tokens(決定的見積り) --------------------------------


async def test_fake_count_tokens_is_deterministic() -> None:
    p = FakeLLMProvider()
    router = LLMRouter(chain=[("primary", "m1", p)])
    n1 = await router.count_tokens(task="chat", prompt="hello world")
    n2 = await router.count_tokens(task="chat", prompt="hello world")
    assert n1 == n2 > 0


# --- PY-LLM-06: 未設定キープロバイダの自動除外 ------------------------------


async def test_unconfigured_provider_is_skipped() -> None:
    # provider=None は運営キー未設定(§11.1-3)を表しチェーンから除外される
    secondary = FakeLLMProvider(responses={"chat": "回答"})
    router = LLMRouter(chain=[("primary", "m1", None), ("secondary", "m2", secondary)])
    resp = await router.complete(task="chat", prompt="hi")
    assert resp.text == "回答"
    # 除外後の実効チェーンでは secondary が rank 0
    assert resp.fallback_rank == 0


async def test_all_providers_unconfigured_raises() -> None:
    router = LLMRouter(chain=[("primary", "m1", None)])
    with pytest.raises(ProviderChainExhausted):
        await router.complete(task="chat", prompt="hi")


# --- PY-LLM-07: Retry-After 尊重(上限 30s) --------------------------------


async def test_retry_after_is_respected_and_capped() -> None:
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    primary = FakeLLMProvider(fail=True, error_kind=ErrorKind.RATE_LIMIT, retry_after_s=999.0)
    secondary = FakeLLMProvider(responses={"chat": "回答"})
    router = LLMRouter(
        chain=[("primary", "m1", primary), ("secondary", "m2", secondary)],
        retry=RetryConfig(max_attempts=2, retry_after_cap_s=30.0),
        sleep=record_sleep,
    )
    resp = await router.complete(task="chat", prompt="hi")
    assert resp.text == "回答"
    # Retry-After 999s は上限 30s に丸められる
    assert slept == [30.0]


def test_provider_error_classification_flags() -> None:
    rate = ProviderError(ErrorKind.RATE_LIMIT, "openai", "m", "x")
    assert rate.retryable and rate.fallback_eligible
    auth = ProviderError(ErrorKind.AUTH, "openai", "m", "x")
    assert not auth.retryable and not auth.fallback_eligible
    content = ProviderError(ErrorKind.CONTENT_FILTER, "openai", "m", "x")
    assert not content.retryable and content.fallback_eligible
