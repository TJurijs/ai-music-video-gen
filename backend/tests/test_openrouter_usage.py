import base64

import pytest

from app.services import openrouter


@pytest.mark.asyncio
async def test_chat_billing_uses_provider_reported_cost(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "id": "gen-chat-123",
                "model": "google/gemini-3.1-pro-preview",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 6867,
                    "completion_tokens": 4712,
                    "cost": 0.070278,
                },
            }

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", Client)

    assert await openrouter.chat([{"role": "user", "content": "test"}]) == "ok"
    cost, detail, generation_id, request_json = openrouter.consume_chat_billing(
        0.01,
        "estimate",
    )

    assert cost == pytest.approx(0.070278)
    assert detail == "estimate · actual OpenRouter usage"
    assert generation_id == "gen-chat-123"
    assert '"cost": 0.070278' in request_json


@pytest.mark.asyncio
async def test_image_generation_returns_provider_reported_cost(monkeypatch):
    encoded = base64.b64encode(b"image-bytes").decode()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "gen-image-123",
                "choices": [{
                    "message": {
                        "images": [{
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        }],
                    },
                }],
                "usage": {
                    "prompt_tokens": 390,
                    "completion_tokens": 1518,
                    "cost": 0.138,
                },
            }

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", Client)

    result = await openrouter.generate_image("test", "gemini-3-pro-image")

    assert result.data == b"image-bytes"
    assert result.cost_usd == pytest.approx(0.138)
    assert result.generation_id == "gen-image-123"
    assert result.usage["completion_tokens"] == 1518
