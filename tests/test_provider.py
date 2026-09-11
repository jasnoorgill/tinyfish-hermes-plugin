"""Unit tests for tinyfish-hermes-plugin — no hermes-agent required.

The provider.py module installs lightweight fallbacks when
``plugins.web._common`` is not importable, so these tests run in any
Python environment with just ``httpx`` and ``pytest``.
"""

from __future__ import annotations

import os
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from tinyfish_hermes_plugin import TinyFishProvider, __version__
from tinyfish_hermes_plugin.provider import (
    _normalize_fetch,
    _normalize_search,
    register,
)


# ---------- trivial -------------------------------------------------------


def test_version_is_string():
    assert isinstance(__version__, str)
    assert __version__.count(".") == 2  # semver-ish


def test_class_attrs():
    assert TinyFishProvider.NAME == "tinyfish"
    assert TinyFishProvider.DISPLAY_NAME == "TinyFish"
    assert TinyFishProvider.KEY_ENV == "TINYFISH_API_KEY"
    assert TinyFishProvider.EXTRACT is True


# ---------- normalizers --------------------------------------------------


def test_normalize_search_happy():
    raw = {
        "results": [
            {"title": "A", "url": "https://a", "snippet": "desc A", "position": 1},
            {"title": "B", "url": "https://b", "snippet": "desc B", "position": 2},
        ]
    }
    out = _normalize_search(raw)
    assert out["success"] is True
    assert len(out["data"]["web"]) == 2
    assert out["data"]["web"][0]["title"] == "A"
    assert out["data"]["web"][0]["url"] == "https://a"


def test_normalize_search_empty():
    assert _normalize_search({"results": []}) == {"success": True, "data": {"web": []}}


def test_normalize_fetch_happy():
    raw = {
        "results": [
            {"url": "https://a", "title": "A", "text": "# A\n\nhello", "final_url": "https://a"},
            {"url": "https://b", "title": "B", "text": "world", "final_url": "https://b/"},
        ],
        "errors": [],
    }
    docs = _normalize_fetch(raw)
    assert len(docs) == 2
    assert docs[0]["title"] == "A"
    assert docs[0]["content"] == "# A\n\nhello"
    assert docs[0]["raw_content"] == docs[0]["content"]
    assert docs[1]["metadata"]["sourceURL"] == "https://b/"


def test_normalize_fetch_per_url_error():
    raw = {
        "results": [{"url": "https://ok", "title": "OK", "text": "fine"}],
        "errors": [{"url": "https://bad", "code": "FETCH_FAILED", "message": "404"}],
    }
    docs = _normalize_fetch(raw)
    assert len(docs) == 2
    ok = next(d for d in docs if d["url"] == "https://ok")
    bad = next(d for d in docs if d["url"] == "https://bad")
    assert ok["content"] == "fine"
    assert "FETCH_FAILED" in bad["error"]


# ---------- search / extract over httpx ----------------------------------


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setenv("TINYFISH_API_KEY", "tfk_test")


def _mock_response(json_body: Dict[str, Any], status_code: int = 200):
    r = MagicMock()
    r.json.return_value = json_body
    r.status_code = status_code
    r.raise_for_status = MagicMock()
    if status_code >= 400:
        def _raise():
            import httpx
            raise httpx.HTTPStatusError("err", request=MagicMock(), response=r)
        r.raise_for_status.side_effect = _raise()
    return r


def test_search_happy(fake_key):
    provider = TinyFishProvider()
    body = {"results": [{"title": "x", "url": "https://x", "snippet": "y", "position": 1}]}
    with patch("tinyfish_hermes_plugin.provider._do_get", return_value=body) as g:
        out = provider.search("anything")
    g.assert_called_once()
    args, _kwargs = g.call_args
    url, params = args[0], args[1]
    assert url.startswith("https://api.search.tinyfish.ai")
    assert params["query"] == "anything"
    assert out["success"] is True
    assert out["data"]["web"][0]["url"] == "https://x"


def test_search_rate_limited(fake_key):
    provider = TinyFishProvider()
    body = {"error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Too many"}}
    with patch("tinyfish_hermes_plugin.provider._do_get", return_value=body):
        out = provider.search("anything")
    assert out["success"] is False
    assert "rate limit" in out["error"].lower()
    assert out["retry_after"] == 60.0


def test_extract_happy(fake_key):
    provider = TinyFishProvider()
    body = {
        "results": [
            {"url": "https://a", "title": "A", "text": "content A"},
            {"url": "https://b", "title": "B", "text": "content B"},
        ],
        "errors": [],
    }
    with patch("tinyfish_hermes_plugin.provider._do_post", return_value=body) as p:
        out = provider.extract(["https://a", "https://b"])
    p.assert_called_once()
    args, _kwargs = p.call_args
    url, payload = args[0], args[1]
    assert url.startswith("https://api.fetch.tinyfish.ai")
    assert payload["urls"] == ["https://a", "https://b"]
    assert len(out) == 2


def test_extract_all_rate_limited(fake_key):
    provider = TinyFishProvider()
    body = {"error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Too many"}}
    with patch("tinyfish_hermes_plugin.provider._do_post", return_value=body):
        out = provider.extract(["https://a", "https://b"])
    assert all("rate limit" in d.get("error", "").lower() for d in out)
    assert {d["url"] for d in out} == {"https://a", "https://b"}


# ---------- register() ---------------------------------------------------


def test_register_calls_context():
    ctx = MagicMock()
    register(ctx)
    ctx.register_web_search_provider.assert_called_once()
    arg = ctx.register_web_search_provider.call_args.args[0]
    assert isinstance(arg, TinyFishProvider)


# ---------- no key in env ------------------------------------------------


def test_search_without_key_returns_no_key_error(monkeypatch):
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    provider = TinyFishProvider()
    # _do_get will raise because _headers() raises before the request.
    # run_search (the stub fallback) calls body() which surfaces the error
    # through the uniform-failure path. Just assert it didn't crash loudly.
    try:
        out = provider.search("x")
    except ValueError as e:
        assert "TINYFISH_API_KEY" in str(e)
        return
    # If the stub wraps it, success must be False with a key error.
    assert out["success"] is False