## Deploy to Hugging Face Spaces

> **Plan:** DEPLOY_TO_HF.md | [Gradio sharing guide](https://www.gradio.app/guides/sharing-your-app) | Account: [`edangx100`](https://huggingface.co/edangx100)
> **Access model:** Public, live search — plain `demo.launch()`, no auth.

- [x] Add root `app.py` entry point that reuses `frontend.app.build_app()` and launches with `STATUS_CSS` (HF runs the file named in `app_file`)
- [x] Generate `requirements.txt` runtime deps. **Reverted the `uv export` approach** — a fully-pinned export sets `pydantic==2.13.4`, which is incompatible with the `mcp` extra HF auto-adds to gradio (`gradio[oauth,mcp]` requires `pydantic<=2.12.5`), causing `BUILD_ERROR: ResolutionImpossible`. Now a minimal top-level list with a `pydantic<=2.12.5` ceiling (pydantic-ai-slim needs `>=2.12`, so 2.12.x is the overlap). Resolution verified locally against HF's exact install line.
- [x] Add root `README.md` with Space frontmatter (`sdk: gradio`, `sdk_version: 6.16.0`, `app_file: app.py`, `python_version: "3.11"`) plus a recruiter-facing blurb
- [x] Set `OPENROUTER_API_KEY` as a Space secret (required — `explorer/config.py` builds `settings` at import, so boot fails without it). Skip `BRAINTRUST_API_KEY` — optional, observability-only, no-op when unset, not needed for the demo. *(Set via `HfApi().add_space_secret` reading from local `.env`; value never printed.)*
- [ ] Set a hard spending/credit cap on the OpenRouter key (public Space spends real credits per search) *(manual — OpenRouter dashboard)*
- [x] `hf auth login`, then deploy from repo root → Space `edangx100/aiid-explorer` on CPU basic (free) hardware. **Deviated from plan:** `gradio deploy` was abandoned for two reasons — (1) it uses the README `title:` verbatim as the repo id, so `title: AI Incident Explorer` (spaces) failed repo-id validation → changed frontmatter `title` to the slug `aiid-explorer`; (2) its `upload_folder` does **not** honor `.gitignore` (would have uploaded `.venv` *and `.env`*). Used `hf upload edangx100/aiid-explorer . . --repo-type=space` with explicit `--exclude` for `.env`/`.venv`/caches instead (verified 25 files uploaded, no secrets).

**Verification:**
- [x] Local entry-point check: `uv run python app.py` builds, imports resolve, banner CSS applies, UI serves (confirmed via `/config`: title "AI Incident Explorer", 14 components)
- [x] `uv run pytest -q` still green (76 passed, 2 skipped)
- [x] Space reached `RUNNING` (not `RUNTIME_ERROR`) → boot succeeded with the secret present. Live at https://huggingface.co/spaces/edangx100/aiid-explorer — `GET /` and `GET /config` both 200, app title "AI Incident Explorer", 14 components, gradio 6.16.0.
- [x] Post-deploy smoke test: live `healthcare` search on the Space returns **10 incidents in 83s** with clickable title links (verified via Gradio client `/query_handler`). Two production-only bugs were found and fixed in the process:
  - **AIID API returned 0 results from Spaces.** Its bot protection blocks non-browser clients; the default `python-httpx` UA passes from a residential IP but is refused from HF's datacenter IP. Fix: `explorer/aiid.py` now sends a browser `User-Agent`/`Referer`.
  - **Agent found incidents but the loop discarded them.** `minimax/minimax-m3` prints the `⭐` result lines but omits the `HISTORICAL_RESULTS` markers, so `loop._extract_block()` returned `""` → "0 incidents found" (this also affected local runs). Fix: `_extract_block` now salvages `AIID Search:` + `⭐` lines when the markers are absent.
- [x] First build hit `BUILD_ERROR`; read it via the build-logs API (`/api/spaces/<repo>/logs/build`) → pydantic conflict, fixed as above. Second build → `RUNNING`.
