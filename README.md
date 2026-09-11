# tinyfish-hermes-plugin

Standalone [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that adds
**[TinyFish](https://agent.tinyfish.ai)** as a web search + content-extraction
backend. Auto-loaded by Hermes — no per-user config files, no `web_tools.py`
edits.

| | |
|---|---|
| **Backend name** | `tinyfish` |
| **Auth** | `TINYFISH_API_KEY` (free signup, no card) |
| **Endpoints** | Search `GET api.search.tinyfish.ai` · Fetch `POST api.fetch.tinyfish.ai` |
| **Free tier** | 30 search/min, 150 fetch URLs/min (server-enforced; plugin surfaces 429s) |
| **Throttling** | None in-plugin. Calls TinyFish directly. Wrap the provider if you need it. |

## Install

```bash
pip install tinyfish-hermes-plugin
# Set your key:
export TINYFISH_API_KEY=tfk_...        # get one at https://agent.tinyfish.ai/api-keys
# Tell Hermes to use it:
hermes tools backend set web tinyfish   # or edit ~/.hermes/config.yaml:
                                        #   web:
                                        #     backend: tinyfish
                                        #     search_backend: tinyfish
                                        #     extract_backend: tinyfish
hermes gateway restart                 # or restart whichever Hermes process you run
```

Verify it loaded:

```bash
hermes plugins list | grep tinyfish    # → web-tinyfish (tinyfish-hermes-plugin v0.2.0)
```

## Why a standalone plugin?

Hermes maintainers closed
[PR #77124](https://github.com/NousResearch/hermes-agent/pull/77124) under the
standing `in-tree-provider-integration` policy — third-party SaaS connectors
don't belong in the core tree because every change to the vendor's API becomes
maintenance burden on Hermes. The recommended path is to ship as a standalone
plugin (`~/.hermes/plugins/` or pip entry point) and promote in the
`#plugins-skills-and-skins` Discord channel. This repo is that standalone.

## Directory-plugin alternative (no pip)

If you'd rather drop the code in `~/.hermes/plugins/web/tinyfish/` directly:

```bash
git clone https://github.com/jasnoorgill/tinyfish-hermes-plugin.git
mkdir -p ~/.hermes/plugins/web
cp -r tinyfish-hermes-plugin/tinyfish_hermes_plugin ~/.hermes/plugins/web/tinyfish
hermes plugins enable web-tinyfish
```

Either install form yields the same `web-tinyfish` plugin registered as
backend `tinyfish`.

## How it works

`TinyFishProvider` subclasses `plugins.web._common.BaseWebSearchProvider`
(internal to Hermes) and implements `search` + `extract`. The provider is
registered via the `hermes_agent.plugins` entry-point group:

```
[project.entry-points."hermes_agent.plugins"]
tinyfish_web_provider = "tinyfish_hermes_plugin:register"
```

Hermes's `plugins_loader._scan_entry_points()` (hermes_cli/plugins.py:1412)
iterates this group at startup and calls each entry-point's `register(ctx)`,
passing a context that exposes `ctx.register_web_search_provider(...)`.

After registration, Hermes's `tools.web_tools` resolves `web.backend: tinyfish`
through the same `web_search_registry` it uses for Firecrawl / Tavily / Exa /
SearXNG / etc. — no special-casing required.

### Request flow

```
web_search("quantum error correction", n=5)
  → tools.web_tools.get_active_search_provider()
    → registry picks "tinyfish" (matches web.backend)
      → TinyFishProvider.search(query, limit)
        → _do_get("https://api.search.tinyfish.ai/", {query})
        → _normalize_search(json)
        → {"success": True, "data": {"web": [{title,url,description,position}, ...]}}
```

### Errors

- `TINYFISH_API_KEY` missing → `ValueError` at call time (is_available returns False)
- HTTP 429 / `RATE_LIMIT_EXCEEDED` → `{success: False, error: "TinyFish rate limit reached", retry_after: 60}`
- HTTP 4xx other → `{success: False, error: <server message>}`
- Network failure → `run_search`/`run_extract` classify and return uniform failure shape

## Development

```bash
git clone https://github.com/jasnoorgill/tinyfish-hermes-plugin.git
cd tinyfish-hermes-plugin
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Unit tests (no hermes required — the heavy imports are stubbed)
pytest tests/ -v

# Build the wheel/sdist
python -m build
ls dist/
# → tinyfish_hermes_plugin-0.2.0-py3-none-any.whl
# → tinyfish_hermes_plugin-0.2.0.tar.gz
```

## Publish to PyPI

One-time setup:

```bash
# 1. Create the PyPI account & project
#    https://pypi.org/account/register/
#    https://pypi.org/manage/account/#api-tokens  → "Add API token", scope "Entire account" (first publish only)
#    Copy the token (starts with pypi-...). Treat it like a password.

# 2. Configure twine (recommended: keyring helper so the token isn't on disk in plaintext)
pip install keyring
keyring set https://upload.pypi.org/legacy/ __token__
# paste the pypi-... token when prompted

# Or for a one-off publish, set the env var:
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-...
```

Publishing:

```bash
# 3. Clean previous builds, rebuild
rm -rf dist/
python -m build

# 4. Upload (TestPyPI first is a good idea for new packages)
twine upload --repository testpypi dist/*
# → https://test.pypi.org/project/tinyfish-hermes-plugin/

# 5. If test looks right, upload to production
twine upload dist/*
# → https://pypi.org/project/tinyfish-hermes-plugin/

# 6. Tag the release
git tag v0.2.0 && git push fork v0.2.0
```

GitHub Actions can do steps 3–5 automatically — see
`.github/workflows/release.yml` in this repo.

### OIDC trusted publishing (no API token)

PyPI supports [trusted publishing](https://docs.pypi.org/trusted-publishers/)
from GitHub Actions — no long-lived secret to manage. To enable:

1. PyPI → your project → Publishing → Add a new pending publisher
   - Owner: `jasnoorgill`
   - Repository: `tinyfish-hermes-plugin`
   - Workflow: `release.yml`
   - Environment: `pypi` (recommended)
2. Create a `pypi` environment in the repo (Settings → Environments)
3. The workflow at `.github/workflows/release.yml` handles auth + upload.

## License

MIT. TinyFish itself is a third-party service; their terms govern use of
their API. This plugin only talks to their public REST endpoints.