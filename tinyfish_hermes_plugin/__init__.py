"""TinyFish web search + fetch provider for Hermes Agent.

A standalone, pip-installable Hermes Agent plugin that adds TinyFish as a
web search / content-extraction backend. Auto-discovered via the
``hermes_agent.plugins`` entry-point group when installed into the same
Python environment that runs Hermes.

Two TinyFish endpoints are wired up:

- **Search** — ``GET https://api.search.tinyfish.ai`` (free tier: 30 req/min)
- **Fetch**  — ``POST https://api.fetch.tinyfish.ai`` (free tier: 150 URLs/min)

Authentication is a single ``TINYFISH_API_KEY`` from
https://agent.tinyfish.ai/api-keys. No credit card required.

Throttling: this plugin calls TinyFish directly. The free tier enforces its
own per-minute rate limits server-side (HTTP 429 / ``RATE_LIMIT_EXCEEDED``);
the plugin surfaces those errors so the caller can back off. If you need
local pre-throttling, wrap the provider in your own limiter.
"""

from __future__ import annotations

from .provider import TinyFishProvider, register

__version__ = "0.2.0"
__all__ = ["TinyFishProvider", "register", "__version__"]