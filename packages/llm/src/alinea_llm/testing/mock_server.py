"""E2E / CI 用の決定的モックサーバ(plans/12 §8.4・Task 8)。

FakeLLMProvider と同一の決定的応答規則を HTTP で提供する。5 社チャット/生成・画像・
arXiv(abs / e-print / Atom)・GitHub/YouTube oEmbed 相当をエミュレートする。外部
ネットワークには一切出ない。同一リクエストは常にバイト同一のレスポンスを返す。

接続(§8.4・§15 ⚠-2/3):
    ALINEA_OPENAI_BASE_URL   = http://localhost:8090/openai/v1
    ALINEA_ANTHROPIC_BASE_URL= http://localhost:8090/anthropic
    ALINEA_GOOGLE_BASE_URL   = http://localhost:8090/google
    ALINEA_DEEPSEEK_BASE_URL = http://localhost:8090/deepseek
    ALINEA_XAI_BASE_URL      = http://localhost:8090/xai/v1
    ALINEA_ARXIV_BASE_URL    = http://localhost:8090/arxiv

単体起動: python -m alinea_llm.testing.mock_server [--host 127.0.0.1] [--port 8090]
"""

from __future__ import annotations

import base64
import gzip
import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from alinea_llm.testing._assets import png_bytes
from alinea_llm.testing.fake_provider import FakeLLMProvider
from alinea_llm.types import ContentPart, LLMRequest, LLMResponse, Message

_FAKE = FakeLLMProvider()


# --- 入力テキスト抽出(各社フォーマットの content を平坦化) ---------------------


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                # OpenAI(input_text/text)・Anthropic(text)・Google(text)
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)
    return ""


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return _flatten_content(msg.get("content"))
    if messages:
        return _flatten_content(messages[-1].get("content"))
    return ""


async def _afake_response(model: str, user_text: str) -> LLMResponse:
    req = LLMRequest(
        model=model or "mock-model",
        messages=[Message(role="user", parts=[ContentPart(type="text", text=user_text)])],
        metadata={"task": "mock"},
    )
    return await _FAKE.generate(req)


# --- OpenAI 互換 Chat Completions(OpenAI / DeepSeek / xAI) --------------------


async def openai_chat(request: Request) -> Response:
    body = await request.json()
    model = body.get("model", "mock-model")
    messages = body.get("messages", [])
    text = _last_user_text(messages)
    resp = await _afake_response(model, text)
    if body.get("stream"):
        return _openai_chat_sse(model, resp.text, resp)
    payload = {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": resp.text},
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": resp.usage.input_tokens,
        },
    }
    return JSONResponse(payload)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _openai_chat_sse(model: str, text: str, resp: LLMResponse) -> StreamingResponse:
    async def gen() -> AsyncIterator[bytes]:
        for i in range(0, len(text), 20):
            chunk = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": text[i : i + 20]}}],
            }
            yield _sse(chunk).encode()
        final = {
            "id": "chatcmpl-mock",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": resp.usage.input_tokens,
                "completion_tokens": resp.usage.output_tokens,
                "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
            },
        }
        yield _sse(final).encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- OpenAI Responses API ------------------------------------------------------

import re as _re

# block_id パターン(plans/03 §12 の命名規則)。
_BLOCK_ID_RE = _re.compile(r"\b(blk-[A-Za-z0-9-]+)\b")


def _extract_first_block_id(text: str) -> str | None:
    """テキスト(システムプロンプト等)から最初の block_id を取り出す。"""
    m = _BLOCK_ID_RE.search(text)
    return m.group(1) if m else None


def _responses_text(body: dict[str, Any]) -> str:
    """Responses API リクエストから user テキストを抽出する。"""
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        return raw_input
    return _last_user_text(raw_input)


def _responses_output_text(body: dict[str, Any], base_text: str) -> str:
    """output_config.evidence が true のとき block_id を [[evidence:...]] で付加する。

    E2E チャット(PW-08)の新規質問→根拠チップ生成経路を通すため: リクエストの
    `instructions` または `input` に block_id パターンが含まれる場合、最初の 1 件を
    [[evidence:...]] マーカーとして末尾に付加する(stream_pipeline が抽出して evidence
    イベントへ変換する)。
    """
    output_config = body.get("output_config") or {}
    if not output_config.get("evidence"):
        return base_text
    # instructions または最初の user メッセージから block_id を探す
    search_text = body.get("instructions", "") + " " + _responses_text(body)
    block_id = _extract_first_block_id(search_text)
    if block_id:
        return f"{base_text}[[evidence:{block_id}]]"
    return base_text


async def openai_responses(request: Request) -> Response:
    body = await request.json()
    model = body.get("model", "mock-model")
    text = _responses_text(body)
    resp = await _afake_response(model, text)
    output_text = _responses_output_text(body, resp.text)

    if body.get("stream"):
        return _openai_responses_sse(model, output_text, resp)

    payload = {
        "id": "resp-mock",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": "completed",
        "output": [
            {
                "id": "msg-mock",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": output_text, "annotations": []}],
            }
        ],
        "output_text": output_text,
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": resp.usage.output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
        },
    }
    return JSONResponse(payload)


def _openai_responses_sse(model: str, text: str, resp: LLMResponse) -> StreamingResponse:
    """Responses API ストリーミング(OpenAI SDK `responses.stream()` 互換)。

    OpenAI SDK が期待する最小イベント列:
      response.created → response.output_item.added → response.content_part.added
      → response.output_text.delta × N → response.output_text.done
      → response.output_item.done → response.completed
    """

    def _ev(event_type: str, data: dict[str, Any]) -> bytes:
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    async def gen() -> AsyncIterator[bytes]:
        response_obj: dict[str, Any] = {
            "id": "resp-mock",
            "object": "response",
            "created_at": 0,
            "model": model,
            "status": "in_progress",
            "output": [],
            "usage": None,
        }
        yield _ev("response.created", {"type": "response.created", "response": response_obj})

        output_item: dict[str, Any] = {
            "id": "msg-mock",
            "type": "message",
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        }
        yield _ev(
            "response.output_item.added",
            {"type": "response.output_item.added", "output_index": 0, "item": output_item},
        )
        yield _ev(
            "response.content_part.added",
            {
                "type": "response.content_part.added",
                "item_id": "msg-mock",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )

        for i in range(0, len(text), 20):
            delta = text[i : i + 20]
            yield _ev(
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg-mock",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": delta,
                },
            )

        yield _ev(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "item_id": "msg-mock",
                "output_index": 0,
                "content_index": 0,
                "text": text,
            },
        )
        yield _ev(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    **output_item,
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            },
        )
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": resp.usage.output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
        }
        completed_response: dict[str, Any] = {
            **response_obj,
            "status": "completed",
            "output": [
                {
                    **output_item,
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            ],
            "output_text": text,
            "usage": usage,
        }
        yield _ev(
            "response.completed",
            {"type": "response.completed", "response": completed_response},
        )

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- Anthropic Messages --------------------------------------------------------


async def anthropic_messages(request: Request) -> Response:
    body = await request.json()
    model = body.get("model", "mock-model")
    messages = body.get("messages", [])
    text = _last_user_text(messages)
    resp = await _afake_response(model, text)
    if body.get("stream"):
        return _anthropic_sse(model, resp.text, resp)
    payload = {
        "id": "msg-mock",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": resp.text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }
    return JSONResponse(payload)


def _anthropic_sse(model: str, text: str, resp: LLMResponse) -> StreamingResponse:
    async def gen() -> AsyncIterator[bytes]:
        def ev(name: str, data: dict[str, Any]) -> bytes:
            return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

        yield ev(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg-mock",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": 0},
                },
            },
        )
        yield ev(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        for i in range(0, len(text), 20):
            yield ev(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text[i : i + 20]},
                },
            )
        yield ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield ev(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": resp.usage.output_tokens},
            },
        )
        yield ev("message_stop", {"type": "message_stop"})

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- Google generateContent / streamGenerateContent / 画像 ---------------------


def _google_usage(resp: LLMResponse) -> dict[str, int]:
    return {
        "promptTokenCount": resp.usage.input_tokens,
        "candidatesTokenCount": resp.usage.output_tokens,
        "totalTokenCount": resp.usage.input_tokens + resp.usage.output_tokens,
    }


def _google_text_from_contents(contents: Any) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        for item in reversed(contents):
            if isinstance(item, dict):
                parts = item.get("parts", [])
                text = _flatten_content(parts)
                if text:
                    return text
    return ""


async def google_generate(request: Request) -> Response:
    spec = request.path_params.get("spec", "")
    model, _, method = spec.rpartition(":")
    body = await request.json()
    contents = body.get("contents", [])
    text = _google_text_from_contents(contents)
    config = body.get("generationConfig") or body.get("config") or {}
    modalities = config.get("responseModalities") or config.get("response_modalities") or []
    if any(str(m).upper() == "IMAGE" for m in modalities):
        b64 = base64.b64encode(png_bytes(1024, 1024)).decode()
        payload_img: dict[str, Any] = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"inlineData": {"mimeType": "image/png", "data": b64}}],
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 0,
                "candidatesTokenCount": 0,
                "totalTokenCount": 0,
            },
            "modelVersion": model,
            "responseId": "resp-mock",
        }
        return JSONResponse(payload_img)

    resp = await _afake_response(model, text)
    candidate = {
        "content": {"role": "model", "parts": [{"text": resp.text}]},
        "finishReason": "STOP",
        "index": 0,
    }
    payload: dict[str, Any] = {
        "candidates": [candidate],
        "usageMetadata": _google_usage(resp),
        "modelVersion": model,
        "responseId": "resp-mock",
    }
    if method == "streamGenerateContent":
        return _google_sse(model, resp)
    return JSONResponse(payload)


def _google_sse(model: str, resp: LLMResponse) -> StreamingResponse:
    async def gen() -> AsyncIterator[bytes]:
        text = resp.text
        for i in range(0, len(text), 20):
            chunk = {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": text[i : i + 20]}]},
                        "index": 0,
                    }
                ],
                "modelVersion": model,
                "responseId": "resp-mock",
            }
            yield _sse(chunk).encode()
        final = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": ""}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": _google_usage(resp),
            "modelVersion": model,
            "responseId": "resp-mock",
        }
        yield _sse(final).encode()

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- 画像生成(OpenAI / xAI: images/generations) ------------------------------


async def image_generations(request: Request) -> Response:
    body = await request.json()
    prompt = body.get("prompt", "")
    b64 = base64.b64encode(png_bytes(1024, 1024)).decode()
    payload = {
        "created": 0,
        "data": [{"b64_json": b64, "revised_prompt": prompt}],
    }
    return JSONResponse(payload)


# --- arXiv --------------------------------------------------------------------

_ABS_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>[{arxiv_id}] Mock Paper</title></head>
<body>
<h1 class="title">Title: Mock Paper for {arxiv_id}</h1>
<div class="authors">Authors: Mock Author</div>
<blockquote class="abstract">Abstract: A deterministic mock abstract for {arxiv_id}.</blockquote>
<div class="dateline">Submitted on 6 Jul 2026</div>
</body></html>
"""

_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query: {id_list}</title>
  <entry>
    <id>http://arxiv.org/abs/{id_list}v1</id>
    <title>Mock Paper for {id_list}</title>
    <summary>A deterministic mock abstract for {id_list}.</summary>
    <author><name>Mock Author</name></author>
    <published>2026-07-06T00:00:00Z</published>
    <updated>2026-07-06T00:00:00Z</updated>
  </entry>
</feed>
"""


async def arxiv_abs(request: Request) -> Response:
    arxiv_id = request.path_params.get("arxiv_id", "")
    return PlainTextResponse(
        _ABS_HTML.format(arxiv_id=arxiv_id), media_type="text/html; charset=utf-8"
    )


async def arxiv_query(request: Request) -> Response:
    params = request.query_params
    id_list = params.get("id_list", "") or params.get("search_query", "")
    return PlainTextResponse(
        _ATOM.format(id_list=id_list), media_type="application/atom+xml; charset=utf-8"
    )


async def arxiv_eprint(request: Request) -> Response:
    arxiv_id = request.path_params.get("arxiv_id", "")
    # 決定的な gzip(mtime=0)の単一ファイル LaTeX ソース。_LATEXML_HTML と同一の
    # 論理構造(§1 Introduction+式(1)/§2 Method+図/参考文献)を持たせ、M2-01 の
    # LaTeX 優先経路でも E2E が同じ本文(例: PW-11 の quote リアンカー対象
    # "The mock method paragraph.")を得られるようにする。
    source = (
        "\\documentclass{article}\n"
        f"\\title{{Mock Paper for {arxiv_id}}}\n"
        "\\author{Mock Author}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        "\\begin{abstract}\n"
        f"A deterministic mock abstract for {arxiv_id}.\n"
        "\\end{abstract}\n"
        "\\section{Introduction}\n"
        "Deterministic mock introduction with inline math $x_{0}$.\n"
        "\\begin{equation}\n"
        "y=f(x)\n"
        "\\end{equation}\n"
        "\\section{Method}\n"
        "The mock method paragraph.\n"
        "\\begin{figure}\n"
        "\\includegraphics{x1.png}\n"
        "\\caption{Mock figure caption.}\n"
        "\\end{figure}\n"
        "\\begin{thebibliography}{1}\n"
        "\\bibitem{ref1} Mock Reference. Deterministic citations, 2026.\n"
        "\\end{thebibliography}\n"
        "\\end{document}\n"
    )
    blob = gzip.compress(source.encode(), mtime=0)
    return Response(blob, media_type="application/gzip")


# LaTeXML(arXiv 公式 HTML)相当の決定的ドキュメント。ingest パイプラインの
# fetching→parsing 実経路(E2E)を成立させる(パーサは ltx_* クラス体系を読む)。
_LATEXML_HTML = """<!DOCTYPE html>
<html>
<head><title>Mock Paper for __ARXIV_ID__</title></head>
<body>
<article class="ltx_document">
  <h1 class="ltx_title ltx_title_document">Mock Paper for __ARXIV_ID__</h1>
  <div class="ltx_authors"><span class="ltx_personname">Mock Author</span></div>
  <div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">Abstract</h6>
    <p class="ltx_p">A deterministic mock abstract for __ARXIV_ID__.</p></div>
  <section class="ltx_section" id="S1">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>
      Introduction</h2>
    <div class="ltx_para" id="S1.p1"><p class="ltx_p">Deterministic mock introduction
      with inline math <math display="inline" alttext="x_{0}"><semantics>
      <annotation encoding="application/x-tex">x_{0}</annotation></semantics></math>.</p></div>
    <table class="ltx_equation ltx_eqn_table" id="S1.E1">
      <tr class="ltx_equation ltx_eqn_row">
        <td class="ltx_eqn_cell ltx_align_center"><math display="block" alttext="y=f(x)">
          <semantics><annotation encoding="application/x-tex">y=f(x)</annotation>
          </semantics></math></td>
        <td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag ltx_tag_equation">(1)</span></td>
      </tr>
    </table>
  </section>
  <section class="ltx_section" id="S2">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">2 </span>
      Method</h2>
    <div class="ltx_para" id="S2.p1"><p class="ltx_p">The mock method paragraph.</p></div>
    <figure class="ltx_figure" id="S2.F1">
      <img class="ltx_graphics" src="x1.png" alt="mock figure"/>
      <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 1: </span>
        Mock figure caption.</figcaption>
    </figure>
  </section>
  <section class="ltx_bibliography" id="bib">
    <h2 class="ltx_title ltx_title_bibliography">References</h2>
    <ul class="ltx_biblist">
      <li class="ltx_bibitem" id="bib.bib1"><span class="ltx_tag ltx_tag_bibitem">[1]</span>
        Mock Reference. Deterministic citations, 2026.</li>
    </ul>
  </section>
</article>
</body></html>
"""

_OAI_GETRECORD = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <GetRecord>
    <record>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>{arxiv_id}</id>
          <license>http://creativecommons.org/licenses/by/4.0/</license>
        </arXiv>
      </metadata>
    </record>
  </GetRecord>
</OAI-PMH>
"""

# 決定的な最小 PDF(1 ページ・空)。
_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \ntrailer<</Size 4/Root 1 0 R>>\n%%EOF\n"
)


async def arxiv_html(request: Request) -> Response:
    arxiv_id = request.path_params.get("arxiv_id", "")
    return PlainTextResponse(
        _LATEXML_HTML.replace("__ARXIV_ID__", arxiv_id), media_type="text/html; charset=utf-8"
    )


async def arxiv_oai(request: Request) -> Response:
    identifier = request.query_params.get("identifier", "")
    arxiv_id = identifier.rsplit(":", 1)[-1] if identifier else ""
    return PlainTextResponse(
        _OAI_GETRECORD.format(arxiv_id=arxiv_id), media_type="text/xml; charset=utf-8"
    )


async def arxiv_pdf(request: Request) -> Response:
    return Response(_MINIMAL_PDF, media_type="application/pdf")


# --- oEmbed(GitHub / YouTube 相当) -------------------------------------------


async def youtube_oembed(request: Request) -> Response:
    url = request.query_params.get("url", "")
    return JSONResponse(
        {
            "type": "video",
            "version": "1.0",
            "provider_name": "YouTube",
            "title": "Mock YouTube Video",
            "author_name": "Mock Channel",
            "thumbnail_url": "http://localhost:8090/arxiv/thumb.jpg",
            "html": f'<iframe src="{url}"></iframe>',
            "duration": 615,
        }
    )


async def github_oembed(request: Request) -> Response:
    url = request.query_params.get("url", "")
    return JSONResponse(
        {
            "type": "rich",
            "version": "1.0",
            "provider_name": "GitHub",
            "title": "Mock GitHub Repository",
            "author_name": "mock-owner",
            "html": f'<a href="{url}">mock/repo</a>',
            "stargazers_count": 1234,
        }
    )


async def healthz(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def build_app() -> Starlette:
    """モックサーバの Starlette アプリを構築する(決定的・in-process テスト可能)。"""
    chat_paths = [
        "/v1/chat/completions",
        "/openai/v1/chat/completions",
        "/deepseek/chat/completions",
        "/deepseek/v1/chat/completions",
        "/xai/v1/chat/completions",
    ]
    responses_paths = ["/v1/responses", "/openai/v1/responses"]
    image_paths = [
        "/v1/images/generations",
        "/openai/v1/images/generations",
        "/xai/v1/images/generations",
    ]
    routes: list[Route] = [Route("/healthz", healthz, methods=["GET"])]
    routes += [Route(p, openai_chat, methods=["POST"]) for p in chat_paths]
    routes += [Route(p, openai_responses, methods=["POST"]) for p in responses_paths]
    routes += [Route(p, image_generations, methods=["POST"]) for p in image_paths]
    routes += [
        Route("/anthropic/v1/messages", anthropic_messages, methods=["POST"]),
        Route("/v1/messages", anthropic_messages, methods=["POST"]),
        Route("/google/v1beta/models/{spec:path}", google_generate, methods=["POST"]),
        Route("/arxiv/abs/{arxiv_id:path}", arxiv_abs, methods=["GET"]),
        Route("/arxiv/api/query", arxiv_query, methods=["GET"]),
        Route("/arxiv/e-print/{arxiv_id:path}", arxiv_eprint, methods=["GET"]),
        Route("/arxiv/html/{arxiv_id:path}", arxiv_html, methods=["GET"]),
        Route("/arxiv/oai2", arxiv_oai, methods=["GET"]),
        Route("/arxiv/pdf/{arxiv_id:path}", arxiv_pdf, methods=["GET"]),
        Route("/youtube/oembed", youtube_oembed, methods=["GET"]),
        Route("/github/oembed", github_oembed, methods=["GET"]),
    ]
    return Starlette(routes=routes)


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Alinea deterministic mock server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
