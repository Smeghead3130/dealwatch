import json

import httpx
import pytest

from dealwatch.config import Config
from dealwatch.extract import extract


async def test_two_timeouts_then_fallback(config, deal, extraction):
    models = []

    def handler(request):
        data = json.loads(request.content)
        models.append(data["model"])
        assert data["think"] is False and data["stream"] is False and "keep_alive" not in data
        assert isinstance(data["format"], dict)
        if len(models) < 3:
            raise httpx.ReadTimeout("slow CPU")
        return httpx.Response(200, json={"message": {"content": extraction.model_dump_json()}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, model, _ = await extract(client, config, deal)
    assert models == ["qwen3.5:9b", "qwen3.5:9b", "qwen3.5:4b"] and model.endswith("4b")


async def test_invalid_json_retried_once(config, deal):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"content": "not JSON"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError):
            await extract(client, config, deal)
    assert len(calls) == 2


def test_direct_port_forbidden():
    with pytest.raises(ValueError):
        Config(ollama_url="http://192.168.50.157:30068")


def test_literal_title_price_recovery(deal, extraction):
    from dealwatch.extract import normalise_extraction

    ex = extraction.model_copy(update={"price": None, "price_quote": "", "variant": "null"})
    ex = normalise_extraction(ex, deal)
    assert ex.price == 100 and ex.price_quote == "$100" and ex.variant == ""
    for title in ("Acme X1 $20 after cashback", "Acme X1 minimum spend $100", "Acme X1 $20 off"):
        ex = extraction.model_copy(update={"price": None})
        assert normalise_extraction(ex, deal.model_copy(update={"title": title})).price is None


def test_free_item_not_confused_with_free_delivery(deal, extraction):
    from dealwatch.extract import normalise_extraction

    ex = extraction.model_copy(update={"price": None, "signal": "none"})
    ex = normalise_extraction(ex, deal.model_copy(update={"title": "Acme X1 FREE + $5 Delivery"}))
    assert ex.price == 0 and ex.signal == "free_tiny_cost"
