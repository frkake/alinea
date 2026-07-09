"""E2E 用モックサーバの決定性テスト(Task 8 / plans/12 §8.4)。

外部ネットワークに一切出ず、同一リクエストが常に同一レスポンスを返すことを検証する。
プロキシ迂回のため httpx は ASGITransport(in-process)を使う。
"""

from __future__ import annotations

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
