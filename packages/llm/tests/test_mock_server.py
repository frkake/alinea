"""E2E 用モックサーバの決定性テスト(Task 8 / plans/12 §8.4)。

外部ネットワークに一切出ず、同一リクエストが常に同一レスポンスを返すことを検証する。
プロキシ迂回のため httpx は ASGITransport(in-process)を使う。
"""

from __future__ import annotations

import json

import httpx
import pytest
from alinea_llm.testing.mock_server import build_app


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = build_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_mock_openai_chat_deterministic() -> None:
    app = build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        body = {"model": "gpt-5.5", "messages": [{"role": "user", "content": "hi"}]}
        r1 = await c.post("/v1/chat/completions", json=body)
        r2 = await c.post("/v1/chat/completions", json=body)
    assert r1.status_code == 200
    assert r1.json() == r2.json()
    assert r1.json()["choices"][0]["message"]["content"]


async def test_mock_openai_responses_endpoint() -> None:
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = {
            "model": "gpt-5.5",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        }
        r = await c.post("/openai/v1/responses", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["output_text"]
    assert data["usage"]["input_tokens"] >= 0


async def test_mock_openai_responses_accepts_output_config() -> None:
    """output_config フィールド(Responses API スタイル)を受け付けて無視する。"""
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = {
            "model": "gpt-5.5",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "output_config": {"include": ["output_text", "usage"]},
        }
        r = await c.post("/openai/v1/responses", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["output_text"]


async def test_mock_openai_responses_streaming() -> None:
    """Responses API の stream=true でSSEストリームを返す(OpenAI provider generate_stream 互換)。"""
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = {
            "model": "gpt-5.5",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
            "output_config": {"include": ["output_text", "usage"]},
        }
        async with c.stream("POST", "/openai/v1/responses", json=body) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            raw = await resp.aread()

    text = raw.decode()
    # response.output_text.delta イベントが少なくとも1件あること
    assert "response.output_text.delta" in text, f"no delta event in: {text[:200]}"
    # response.completed イベントがあること
    assert "response.completed" in text, f"no completed event in: {text[:200]}"

    # SSE データをパースして delta テキストを収集
    deltas: list[str] = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                evt = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "response.output_text.delta":
                deltas.append(evt.get("delta", ""))
    assert "".join(deltas), "streaming deltas should produce non-empty text"


async def test_mock_openai_responses_streaming_with_evidence() -> None:
    """output_config で evidence markers を要求すると [[evidence:...]] をレスポンスに含む。

    チャット E2E(PW-08)の新規質問→根拠チップ生成経路を検証する(Task 7)。
    モックは input に block_id を持つシステムプロンプトが含まれるとき、最初の block_id を
    [[evidence:...]] マーカーとして本文に埋め込む。
    """
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        # E2E seed の実在 block_id をシステムプロンプトに含める
        body = {
            "model": "gpt-5.5",
            "instructions": "blk-2-1-p1-9eca について説明してください。",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "explain"}]}],
            "stream": True,
            "output_config": {"include": ["output_text", "usage"], "evidence": True},
        }
        async with c.stream("POST", "/openai/v1/responses", json=body) as resp:
            assert resp.status_code == 200
            raw = await resp.aread()

    text = raw.decode()
    # ストリームのデルタをすべて連結して [[evidence:...]] が含まれるか確認
    deltas: list[str] = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                evt = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "response.output_text.delta":
                deltas.append(evt.get("delta", ""))
    full_text = "".join(deltas)
    assert "[[evidence:" in full_text, f"no evidence marker in streamed text: {full_text!r}"


async def test_mock_anthropic_messages() -> None:
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = {
            "model": "claude-opus-4-8",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        }
        r1 = await c.post("/anthropic/v1/messages", json=body)
        r2 = await c.post("/anthropic/v1/messages", json=body)
    assert r1.status_code == 200
    assert r1.json() == r2.json()
    assert r1.json()["content"][0]["text"]


async def test_mock_google_generate_content() -> None:
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
        r = await c.post("/google/v1beta/models/gemini-3.5-flash:generateContent", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["candidates"][0]["content"]["parts"][0]["text"]


async def test_mock_deepseek_and_xai_chat() -> None:
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        ds = await c.post(
            "/deepseek/chat/completions",
            json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]},
        )
        xai = await c.post(
            "/xai/v1/chat/completions",
            json={"model": "grok-4.3", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert ds.status_code == 200 and xai.status_code == 200
    assert ds.json()["choices"][0]["message"]["content"]
    assert xai.json()["choices"][0]["message"]["content"]


async def test_mock_openai_image_generation() -> None:
    import base64

    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/v1/images/generations",
            json={"model": "gpt-image-2", "prompt": "a diagram", "size": "1536x1024"},
        )
    assert r.status_code == 200
    b64 = r.json()["data"][0]["b64_json"]
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"  # PNG シグネチャ


async def test_mock_arxiv_endpoints() -> None:
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        abs_page = await c.get("/arxiv/abs/2209.03003")
        atom = await c.get("/arxiv/api/query", params={"id_list": "2209.03003"})
        eprint = await c.get("/arxiv/e-print/2209.03003")
    assert abs_page.status_code == 200
    assert "2209.03003" in abs_page.text
    assert atom.status_code == 200
    assert "<feed" in atom.text
    assert eprint.status_code == 200
    assert eprint.content[:2] == b"\x1f\x8b"  # gzip magic


async def test_mock_oembed_endpoints() -> None:
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yt = await c.get("/youtube/oembed", params={"url": "https://youtu.be/abc123"})
        gh = await c.get("/github/oembed", params={"url": "https://github.com/foo/bar"})
    assert yt.status_code == 200
    assert yt.json()["thumbnail_url"]
    assert gh.status_code == 200
    assert gh.json()["title"]
