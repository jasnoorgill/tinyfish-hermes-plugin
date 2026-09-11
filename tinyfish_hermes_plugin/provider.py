"""TinyFish provider implementation.

Throttling note (v0.2+): the burst-drain local rate limiter that v0.1 shipped
with has been removed. The plugin calls TinyFish directly and relies on the
server-side 429/RATE_LIMIT_EXCEEDED responses to surface quota exhaustion.
This keeps the plugin small, dependency-free, and predictable on paid tiers
where local throttling is unhelpful.

If you self-host a free-tier key and want to avoid 429s, install a limiter
upstream of the provider (e.g. ``aiolimiter`` wrapping ``TinyFishProvider.search``)
— the class is small enough to wrap.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

try:  # hermes-agent ships the shared base class; plugin imports it at runtime
    from plugins.web._common import (
        BaseWebSearchProvider,
        document,
        page_error,
        provider_env,
        run_extract,
        run_search,
        setup_schema,
    )
except ImportError:  # pragma: no cover - allows unit tests without hermes
    BaseWebSearchProvider = object  # type: ignore[assignment,misc]

    def provider_env(name: str) -> str:  # type: ignore[no-redef]
        import os
        return os.environ.get(name, "") or ""

    def document(url, title, content, *, source_url=None):  # type: ignore[no-redef]
        return {
            "url": url, "title": title, "content": content,
            "raw_content": content,
            "metadata": {"sourceURL": source_url or url, "title": title},
        }

    def page_error(url, error):  # type: ignore[no-redef]
        return {"url": url, "title": "", "content": "", "error": error}

    def run_search(*args, **kwargs):  # type: ignore[no-redef]
        return args[2]()

    def run_extract(*args, **kwargs):  # type: ignore[no-redef]
        return args[3]()

    def setup_schema(*args, **kwargs):  # type: ignore[no-redef]
        return {}


logger = logging.getLogger(__name__)

_TINYFISH_SEARCH_BASE = "https://api.search.tinyfish.ai"
_TINYFISH_FETCH_BASE = "https://api.fetch.tinyfish.ai"


def _headers() -> Dict[str, str]:
    api_key = provider_env("TINYFISH_API_KEY")
    if not api_key:
        raise ValueError(
            "TINYFISH_API_KEY is not set. "
            "Get a free key at https://agent.tinyfish.ai/api-keys"
        )
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def _do_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    with httpx.Client(timeout=60.0) as client:
        response = client.get(url, params=params, headers=_headers())
    response.raise_for_status()
    return response.json()


def _do_post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import httpx
    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, json=payload, headers=_headers())
    response.raise_for_status()
    return response.json()


def _normalize_search(response: Dict[str, Any]) -> Dict[str, Any]:
    """Map TinyFish ``GET /`` → ``{success, data: {web: [...]}}``."""
    web: List[Dict[str, Any]] = []
    for item in response.get("results", []):
        web.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "description": item.get("snippet", ""),
            "position": item.get("position", 0),
        })
    return {"success": True, "data": {"web": web}}


def _normalize_fetch(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map TinyFish ``POST /`` → list of doc dicts (legacy ``raw_content`` shape)."""
    docs: List[Dict[str, Any]] = []
    error_map = {err.get("url", ""): err for err in response.get("errors", [])}

    for result in response.get("results", []):
        url = result.get("url", "")
        err_entry = error_map.get(url, {})
        raw = (
            result.get("text", "")
            or result.get("content", "")
            or result.get("raw_content", "")
        )
        docs.append(document(
            url,
            result.get("title", ""),
            raw,
            source_url=result.get("final_url", url) or None,
        ))

    for err_url, err_entry in error_map.items():
        if not any(d["url"] == err_url for d in docs):
            docs.append(page_error(
                err_url,
                f"{err_entry.get('code', 'ERROR')}: {err_entry.get('message', '')}",
            ))

    return docs


class TinyFishProvider(BaseWebSearchProvider):  # type: ignore[misc,valid-type]
    """TinyFish search + extract backend.

    The four class attrs below (NAME / DISPLAY_NAME / KEY_ENV / EXTRACT) are
    the only contract with ``plugins.web._common.BaseWebSearchProvider``.
    The base class supplies ``is_available``, ``supports_search``,
    ``supports_extract``, and the ``name`` / ``display_name`` properties;
    subclasses only implement ``search`` and ``extract``.
    """

    NAME = "tinyfish"
    DISPLAY_NAME = "TinyFish"
    KEY_ENV = "TINYFISH_API_KEY"
    EXTRACT = True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        def _body() -> Dict[str, Any]:
            params: Dict[str, Any] = {"query": query}
            raw = _do_get(f"{_TINYFISH_SEARCH_BASE}/", params)
            if "error" in raw:
                code = raw["error"].get("code", "")
                if code == "RATE_LIMIT_EXCEEDED":
                    return {
                        "success": False,
                        "error": (
                            "TinyFish search rate limit reached. "
                            "Upgrade at https://agent.tinyfish.ai or retry later."
                        ),
                        "retry_after": 60.0,
                    }
                return {
                    "success": False,
                    "error": raw["error"].get("message", str(raw)),
                }
            return _normalize_search(raw)

        return run_search("TinyFish", logger, _body)

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        def _body() -> List[Dict[str, Any]]:
            payload = {
                "urls": urls,
                "format": kwargs.get("format", "markdown"),
                "links": kwargs.get("links", False),
                "image_links": kwargs.get("image_links", False),
            }
            raw = _do_post(f"{_TINYFISH_FETCH_BASE}/", payload)
            if "error" in raw:
                code = raw["error"].get("code", "")
                if code == "RATE_LIMIT_EXCEEDED":
                    return [
                        page_error(u, "TinyFish fetch rate limit reached. Retry later.")
                        for u in urls
                    ]
                return [
                    page_error(
                        u,
                        f"TinyFish fetch error: {raw['error'].get('message', '')}",
                    )
                    for u in urls
                ]
            return _normalize_fetch(raw)

        return run_extract("TinyFish", logger, urls, _body)

    def get_setup_schema(self) -> Dict[str, Any]:
        return setup_schema(
            "TinyFish",
            "free",
            "Search + fetch. Free tier: 30 search/min, 150 fetch URLs/min (server-side limit; 429 on exceed).",
            "TINYFISH_API_KEY",
            "TinyFish API key",
            "https://agent.tinyfish.ai/api-keys",
        )


def register(ctx) -> None:
    """Hermes plugin entry point. Called by the plugin loader with a context.

    The context exposes ``register_web_search_provider`` (added in Hermes
    v0.20+); we forward our provider to it so the web_search/web_extract
    tools can resolve ``backend: tinyfish`` automatically.
    """
    ctx.register_web_search_provider(TinyFishProvider())