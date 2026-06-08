# Deploy to Hugging Face Spaces — Runbook

> **Status: DEPLOYED & RUNNING** (2026-06-08). Live at
> <https://huggingface.co/spaces/edangx100/aiid-explorer>. The root `app.py`,
> `requirements.txt`, and `README.md` (Space frontmatter) are in place and the Space
> boots cleanly with the `OPENROUTER_API_KEY` secret set.
>
> **The actual deploy diverged from the original `gradio deploy` plan below — read this
> first.** Three things bit us; all are fixed in the repo now:
> 1. **`gradio deploy` uses the README `title:` verbatim as the repo id.** `title: AI
>    Incident Explorer` (spaces) fails repo-id validation. Fix: frontmatter `title` is now
>    the slug `aiid-explorer` (the app's own H1 still reads "AI Incident Explorer").
> 2. **`gradio deploy`'s `upload_folder` does NOT honor `.gitignore`** (despite the Gradio
>    guide's wording) — it would have uploaded `.venv` *and `.env` (the API key)* to a
>    public Space. We abandoned `gradio deploy` and used
>    `hf upload edangx100/aiid-explorer . . --repo-type=space` with explicit `--exclude`
>    patterns (`.env`, `.venv/*`, caches). Verified only 25 files upload, no secrets.
> 3. **A fully-pinned `requirements.txt` breaks the build.** HF auto-installs
>    `gradio[oauth,mcp]`, and the `mcp` extra requires `pydantic<=2.12.5`; a `uv export`
>    pins `pydantic==2.13.4` → `BUILD_ERROR: ResolutionImpossible`. Fix: minimal top-level
>    `requirements.txt` with a `pydantic<=2.12.5` ceiling (see the comment in that file).
>
> The remaining open item is operational: set a hard **spending cap** on the OpenRouter
> key, then run the live smoke test. The original plan follows for reference.

Goal: host the Gradio app on **Hugging Face Spaces** under account
[`edangx100`](https://huggingface.co/edangx100) so the live URL can be shared with a
recruiter.

**Access model:** Public, live search — plain `demo.launch()`, no login. Anyone with the
URL can run a real search, which spends OpenRouter credits (accepted; see
[Credit safety](#credit-safety-the-key-is-public) for mitigations).

---

## Why these changes are needed

The app already runs locally via `uv run python -m frontend.app`. HF Spaces needs three
things this `uv`/`pyproject.toml` project doesn't have yet:

1. A **top-level `app.py`** — HF runs the file named in `app_file` (default `app.py`), but
   our entry point is `frontend/app.py` using package imports.
2. A **`requirements.txt`** — HF installs deps from it; we only have `pyproject.toml` +
   `uv.lock`.
3. A **`README.md` with YAML frontmatter** — HF reads Space config (SDK, version, app
   file) from it. No root `README.md` exists today, so there's no conflict.

Plus: `OPENROUTER_API_KEY` must be set as a **Space secret**. `explorer/config.py` builds
`settings = Settings()` at import time and the key is required, so the Space crashes on
boot without it.

`BRAINTRUST_API_KEY` is **optional — skip it for this demo.** It only turns on Braintrust
observability traces (a dev/debugging convenience for you, invisible to visitors), it is
not billed per use, and `explorer/agents.py` makes all tracing a no-op when it's unset.
The only secret the Space actually needs is `OPENROUTER_API_KEY`.

---

## Files to add (repo root)

### 1. `app.py` — Spaces entry point
A thin wrapper that reuses the real app. HF runs it with `python app.py` from the repo
root, so the `frontend`/`explorer` package imports resolve. CSS must pass through
`launch()` (in Gradio 6 the page-wide CSS is a `launch()` arg, not a `Blocks` arg).

```python
# Hugging Face Spaces entry point. HF runs this file directly; it reuses the real app
# defined in frontend/app.py so local dev and the Space share one implementation.
from frontend.app import build_app, STATUS_CSS

# HF sets the server host/port via env vars, which launch() picks up automatically.
build_app().launch(css=STATUS_CSS)
```

No change to `frontend/app.py` — its `build_app()` and `STATUS_CSS` are reused as-is.

### 2. `requirements.txt`
Mirror the runtime deps from `pyproject.toml` (exclude the dev group). Preferred:

```bash
uv export --no-dev --no-hashes -o requirements.txt
```

Or hand-write the 8 runtime deps:

```
braintrust>=0.24.0
gensim>=4.4.0
gradio>=6.16.0
httpx>=0.28.1
pandas>=3.0.3
pydantic-ai>=1.106.0
pydantic-ai-harness[code-mode]>=0.3.0
pydantic-settings>=2.14.1
```

`pyproject` requires Python ≥3.11, so pin `python_version: "3.11"` in the README
frontmatter — otherwise HF may default to an older Python and fail to resolve.

### 3. `README.md` (root) — Space config + recruiter-facing blurb

```markdown
---
title: AI Incident Explorer
emoji: 🛡️
colorFrom: indigo
colorTo: red
sdk: gradio
sdk_version: <pin to installed gradio>
app_file: app.py
python_version: "3.11"
pinned: false
---

# AI Incident Explorer
Mine the AI Incident Database for real-world AI failures and attacks, classified against
MITRE ATLAS techniques using a Recursive Language Model agent loop.
```

Pin `sdk_version` to the actually-installed version:

```bash
uv run python -c "import gradio; print(gradio.__version__)"
```

---

## Deployment steps (run interactively)

1. **Create an HF token** (write scope): https://huggingface.co/settings/tokens
2. **Log in** (run from the session prompt with `!` so output appears here):
   `uv run hf auth login`  → paste the token. (`huggingface-cli` is deprecated; use `hf`.)
3. **Deploy** from the repo root: `uv run gradio deploy`. When prompted:
   - **Space name:** `aiid-explorer` → Space becomes `edangx100/aiid-explorer`
   - **Hardware:** `CPU basic` (free) — the LLM runs remotely via OpenRouter, no GPU needed
   - **Secrets:** add `OPENROUTER_API_KEY` only. Skip `BRAINTRUST_API_KEY` (optional,
     observability-only, not needed for the demo)
   - **App file:** `app.py`
4. If secrets weren't added during deploy, set them under
   **Space → Settings → Variables and secrets → New secret**:
   `OPENROUTER_API_KEY = <key>` (required — boot fails without it).
5. Watch the **Logs** tab while it builds. Live URL:
   `https://huggingface.co/spaces/edangx100/aiid-explorer`

`gradio deploy` uploads every non-`.gitignore`d file. `.env` is gitignored, so the key
lives only in HF Secrets. Re-run `gradio deploy` to push updates (or enable its GitHub
Actions option to auto-update on push).

---

## Credit safety (anyone can *spend* the key, but not read it)

The `OPENROUTER_API_KEY` text stays server-side as an HF Secret — visitors can't see or
steal it. What's open is **usage**: because the Space is public with no login, any visitor
clicking Search makes a real OpenRouter call billed to your account. Mitigations:

- Set a **hard spending/credit cap** on the OpenRouter key (OpenRouter dashboard) so a
  traffic burst can't run up an unbounded bill. Past the cap, calls just fail and the app
  falls back to demo data.
- Keep **default max rounds = 1** (already the case) to bound per-search cost and latency.
- If abuse shows up, switch to password auth (`launch(auth=("user", "pass"))`) or make the
  Space private — both are one-line changes to `app.py`.

---

## What the recruiter will see

- First load may take ~30s if the free Space has gone to sleep.
- A live search runs ~1.5–3 min for 1 round; the colour-coded status banner shows
  progress so it never looks stalled.
- On any backend/API failure the app falls back to the bundled `DEMO_DATA` with an error
  banner, so the results table is never blank — a good resilience point to highlight.

---

## Verification

1. **Local entry-point check (before deploying):** `uv run python app.py` — confirms the
   new root `app.py` builds, imports resolve, the banner CSS applies, and the UI serves at
   the printed localhost URL (uses the local `.env` key).
2. **Tests still green:** `uv run pytest -q` → expect `76 passed, 2 skipped`.
3. **Post-deploy smoke test:** open the Space URL, run query `healthcare`, and confirm the
   banner moves through *Round 0 of 1 → Searching: … → complete* and the table fills with
   clickable incident titles linking to incidentdatabase.ai.
4. **On build failure:** the Space **Logs** tab shows the cause — usually a
   `requirements.txt` resolution issue; adjust pins or `python_version`.

---

## Out of scope

- `references/*.ipynb`, `SPEC.md`, `TASKS.md`, `uv.lock`, etc. also upload (tracked, not
  gitignored). Harmless — only `app.py` runs. Trimming them would require gitignoring,
  which also untracks them on GitHub, so it's left alone.
- No change to `frontend/app.py`, `explorer/`, or the tests is required.
