"""LLM 補助層の単体テスト(caching / tokens / structured / router / Fake の分岐網羅)。

plans/04 §9・§12・§13・§14 の分岐を FakeLLMProvider/FakeImageProvider と決定的
スタブのみで検証する(実プロバイダ・ネットワーク非依存)。PY-LLM-04/05 の補助。
"""

from __future__ import annotations

import pytest
from alinea_llm.caching import (
    MIN_CACHE_TOKENS,
    min_cache_tokens_for,
    translation_cache_key,
)
from alinea_llm.errors import (
    ErrorKind,
    ProviderChainExhausted,
    ProviderError,
    SchemaValidationFailed,
)
from alinea_llm.providers.openai_provider import OpenAIProvider
from alinea_llm.registry import ModelRegistry
from alinea_llm.router import ImageRouter, LLMRouter
from alinea_llm.routing import RetryConfig
from alinea_llm.structured import attach_parsed, structured_with_json_mode
from alinea_llm.testing.fake_provider import FakeImageProvider, FakeLLMProvider
from alinea_llm.tokens import IMAGE_TOKEN_COST, budget_for, estimate_tokens_o200k
from alinea_llm.types import (
    ContentPart,
    ImageRequest,
    ImageResult,
    JsonSchemaSpec,
    LLMRequest,
    LLMResponse,
    Message,
)

_OBJ_SCHEMA = JsonSchemaSpec(name="obj", json_schema={"type": "object"})


async def _noop_sleep(_seconds: float) -> None:
    return None


def _user_req(text: str, *, task: str = "translate") -> LLMRequest:
    return LLMRequest(
        model="m",
        messages=[Message(role="user", parts=[ContentPart(type="text", text=text)])],
        metadata={"task": task},
    )


# ---------------------------------------------------------------------------
# caching / tokens
# ---------------------------------------------------------------------------
def test_caching_helpers() -> None:
    assert translation_cache_key("rev1", "natural", "g1") == "tr:rev1:natural:g1"
    assert min_cache_tokens_for("claude-3-5-haiku") == MIN_CACHE_TOKENS["haiku"]
    assert min_cache_tokens_for("claude-opus-4-8") == MIN_CACHE_TOKENS["opus"]


def test_openai_prompt_cache_key_is_omitted_when_sdk_does_not_support_it() -> None:
    provider = OpenAIProvider(api_key="sk-test", base_url="http://localhost")
    req = _user_req("hello").model_copy(update={"prompt_cache_key": "tr:rev:natural:g1"})
    kwargs = provider._kwargs(req)
    assert "prompt_cache_key" not in kwargs
    assert "extra_body" not in kwargs


def test_openai_none_effort_is_sent_as_none() -> None:
    provider = OpenAIProvider(api_key="sk-test", base_url="http://localhost")
    kwargs = provider._kwargs(_user_req("hello"))
    assert kwargs["reasoning"] == {"effort": "none"}


def test_token_estimate_and_budget() -> None:
    req = LLMRequest(
        model="m",
        system=[ContentPart(type="text", text="you are a translator")],
        messages=[
            Message(
                role="user",
                parts=[
                    ContentPart(type="text", text="hello world"),
                    ContentPart.from_image_bytes(b"\x89PNG", "image/png"),
                ],
            )
        ],
    )
    est = estimate_tokens_o200k(req)
    assert est > IMAGE_TOKEN_COST  # 画像 1 枚分 + テキスト分
    assert budget_for(1000, 100) == 0  # 余りが負 → 0 にクランプ
    assert budget_for(10000, 100) == 10000 - 100 - 2048


def test_token_estimate_treats_model_special_token_text_as_plain_input() -> None:
    assert estimate_tokens_o200k(_user_req("prefix <|endoftext|> suffix")) > 0


# ---------------------------------------------------------------------------
# structured(attach_parsed / json モード再試行)
# ---------------------------------------------------------------------------
def test_attach_parsed_paths() -> None:
    ok = attach_parsed(LLMResponse(text='{"a": 1}'), _OBJ_SCHEMA)
    assert ok.parsed == {"a": 1}
    # コードフェンス除去。
    fenced = attach_parsed(LLMResponse(text='```json\n{"a": 2}\n```'), _OBJ_SCHEMA)
    assert fenced.parsed == {"a": 2}
    # JSON でない → SCHEMA_VALIDATION の ProviderError。
    with pytest.raises(ProviderError) as e1:
        attach_parsed(LLMResponse(text="not json"), _OBJ_SCHEMA)
    assert e1.value.kind is ErrorKind.SCHEMA_VALIDATION
    # top-level が object でない。
    with pytest.raises(ProviderError):
        attach_parsed(LLMResponse(text="[1, 2]"), _OBJ_SCHEMA)
    # スキーマ違反。
    strict = JsonSchemaSpec(
        name="need_b", json_schema={"type": "object", "required": ["b"], "properties": {"b": {}}}
    )
    with pytest.raises(ProviderError):
        attach_parsed(LLMResponse(text='{"a": 1}'), strict)


class _SeqProvider(FakeLLMProvider):
    """generate を台本通りのテキストで返すスタブ(json モード再試行の検証用)。"""

    def __init__(self, texts: list[str]) -> None:
        super().__init__()
        self._texts = texts
        self._i = 0

    async def generate(self, req: LLMRequest) -> LLMResponse:
        text = self._texts[min(self._i, len(self._texts) - 1)]
        self._i += 1
        return LLMResponse(text=text, provider=self.name, model=req.model)


async def test_structured_json_mode_retry_then_ok() -> None:
    provider = _SeqProvider(["not json", '{"a": 1}'])
    req = _user_req("translate this").model_copy(update={"json_schema": _OBJ_SCHEMA})
    resp = await structured_with_json_mode(provider, req)
    assert resp.parsed == {"a": 1}
    assert provider._i == 2  # 初回失敗 → 1 回再試行で成功


async def test_structured_json_mode_exhausted() -> None:
    provider = _SeqProvider(["nope", "still bad", "again bad", "x"])
    req = _user_req("translate this").model_copy(update={"json_schema": _OBJ_SCHEMA})
    with pytest.raises(ProviderError) as e:
        await structured_with_json_mode(provider, req)
    assert e.value.kind is ErrorKind.SCHEMA_VALIDATION


def test_validate_raises_schema_validation_failed_directly() -> None:
    from alinea_llm.structured import _validate

    with pytest.raises(SchemaValidationFailed):
        _validate("{bad", _OBJ_SCHEMA)


# ---------------------------------------------------------------------------
# FakeLLMProvider / FakeImageProvider の分岐
# ---------------------------------------------------------------------------
async def test_fake_provider_no_user_message() -> None:
    p = FakeLLMProvider()
    req = LLMRequest(
        model="m",
        messages=[Message(role="assistant", parts=[ContentPart(type="text", text="prev")])],
        metadata={"task": "translate"},
    )
    resp = await p.generate(req)
    assert resp.text.startswith("「(訳)")  # source 空でも決定的に生成


async def test_fake_provider_synth_translation_batch() -> None:
    p = FakeLLMProvider()
    user = "# 翻訳対象ブロック\n[blk-1] (paragraph) Hello ⟦m1⟧ world\ncont line\n# 次\n"
    req = LLMRequest(
        model="m",
        messages=[Message(role="user", parts=[ContentPart(type="text", text=user)])],
        json_schema=JsonSchemaSpec(name="translation_batch_v1", json_schema={"type": "object"}),
        metadata={"task": "translate"},
    )
    resp = await p.generate_structured(req)
    assert resp.parsed is not None
    translations = resp.parsed["translations"]
    assert translations[0]["id"] == "blk-1"
    assert translations[0]["ja"] == "訳: Hello ⟦m1⟧ world\ncont line"


async def test_fake_provider_script_and_errors() -> None:
    scripted = FakeLLMProvider(script=[ErrorKind.RATE_LIMIT, None])
    with pytest.raises(ProviderError):
        await scripted.generate(_user_req("x"))
    ok = await scripted.generate(_user_req("x"))  # 2 回目は成功
    assert ok.text

    # generate_structured の json_schema 欠落。
    p = FakeLLMProvider()
    with pytest.raises(ProviderError) as e1:
        await p.generate_structured(_user_req("x"))
    assert e1.value.kind is ErrorKind.INVALID_REQUEST
    # 未知スキーマ。
    unknown = _user_req("x").model_copy(
        update={"json_schema": JsonSchemaSpec(name="unknown_x", json_schema={"type": "object"})}
    )
    with pytest.raises(ProviderError) as e2:
        await p.generate_structured(unknown)
    assert e2.value.kind is ErrorKind.SCHEMA_VALIDATION


async def test_fake_provider_stream_error() -> None:
    p = FakeLLMProvider(fail=True, error_kind=ErrorKind.SERVER)
    events = [ev async for ev in p.generate_stream(_user_req("x"))]
    assert events[0].type == "start"
    assert events[-1].type == "error"
    assert events[-1].error_kind == str(ErrorKind.SERVER)


async def test_fake_image_provider_success_and_fail() -> None:
    ok = await FakeImageProvider().generate_image(ImageRequest(model="img", prompt="draw"))
    assert isinstance(ok, ImageResult)
    assert ok.image_bytes.startswith(b"\x89PNG")
    with pytest.raises(ProviderError):
        await FakeImageProvider(fail=True).generate_image(ImageRequest(model="img", prompt="x"))


# ---------------------------------------------------------------------------
# LLMRouter / ImageRouter の分岐
# ---------------------------------------------------------------------------
class _Meter:
    def __init__(self) -> None:
        self.drafts: list[object] = []

    async def record(self, record: object) -> None:
        self.drafts.append(record)


async def test_router_request_schema_and_meter() -> None:
    meter = _Meter()
    provider = FakeLLMProvider(responses={"translate": "訳"})
    router = LLMRouter(
        chain=[("op", "unknown-model", provider)],
        meter=meter,
        registry=ModelRegistry([]),  # 空 → cost 計算は KeyError を握りつぶし 0.0
        sleep=_noop_sleep,
    )
    base = LLMRequest(
        model="ignored",
        messages=[Message(role="user", parts=[ContentPart(type="text", text="hi")])],
        metadata={"trace_id": "t1"},
    )
    resp = await router.complete(task="translate", request=base, schema=_OBJ_SCHEMA)
    assert resp.text == "訳"
    assert resp.fallback_rank == 0
    assert len(meter.drafts) == 1  # 成功記録 1 件


async def test_router_fallback_retry_and_fatal() -> None:
    meter = _Meter()
    primary = FakeLLMProvider(fail=True, error_kind=ErrorKind.RATE_LIMIT)  # retry→fallback
    secondary = FakeLLMProvider(responses={"translate": "ok"})
    router = LLMRouter(
        chain=[("a", "m1", primary), ("b", "m2", secondary)],
        retry=RetryConfig(max_attempts=2),
        meter=meter,
        sleep=_noop_sleep,
    )
    resp = await router.complete(task="translate", prompt="x")
    assert resp.text == "ok"
    assert resp.fallback_rank == 1
    # primary で error 記録 2 件(attempt 1,2)+ secondary の成功 1 件。
    assert len(meter.drafts) == 3

    # fatal(AUTH)は即チェーン中断。
    fatal = LLMRouter(chain=[("a", "m1", FakeLLMProvider(fail=True, error_kind=ErrorKind.AUTH))])
    with pytest.raises(ProviderChainExhausted):
        await fatal.complete(task="translate", prompt="x")


async def test_router_count_tokens_and_empty_chain() -> None:
    router = LLMRouter(chain=[("op", "m1", FakeLLMProvider())])
    n = await router.count_tokens(task="translate", prompt="hello world")
    assert n >= 1
    empty = LLMRouter(chain=[("op", "m1", None)])  # provider=None → active 空
    with pytest.raises(ProviderChainExhausted):
        await empty.count_tokens(task="translate", prompt="x")


async def test_image_router_fallback_and_meter() -> None:
    meter = _Meter()
    # 1 本目は RATE_LIMIT(retry→fallback)、2 本目で成功。registry 空 → cost KeyError 握り。
    router = ImageRouter(
        chain=[
            ("g", "img-bad", FakeImageProvider(fail=True, error_kind=ErrorKind.RATE_LIMIT)),
            ("x", "img-ok", FakeImageProvider()),
        ],
        retry=RetryConfig(max_attempts=2),
        meter=meter,
        registry=ModelRegistry([]),
        sleep=_noop_sleep,
    )
    result = await router.generate("draw a diagram")
    assert result.image_bytes.startswith(b"\x89PNG")
    assert result.fallback_rank == 1
    assert len(meter.drafts) == 3  # errorx2 + okx1

    exhausted = ImageRouter(
        chain=[("g", "img", FakeImageProvider(fail=True, error_kind=ErrorKind.AUTH))]
    )
    with pytest.raises(ProviderChainExhausted):
        await exhausted.generate("x")
