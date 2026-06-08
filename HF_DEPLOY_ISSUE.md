# Hugging Face Spaces — Deployment Issues & Fixes

Post-mortem of the problems hit while deploying **AI Incident Explorer** to
<https://huggingface.co/spaces/edangx100/aiid-explorer>, and how each was fixed. Written
so the next person doesn't re-discover them.

The headline symptom was **"Round complete — 0 incidents found"** on the live Space while
the app worked locally. That turned out to be **two independent bugs** (Issues 4 & 5).
Three earlier issues blocked the deploy itself (Issues 1–3). After those were fixed the
search worked, but two **quality/reliability** problems remained (Issues 6 & 7): the model
was intermittently unreliable, and it emitted placeholder ATLAS technique IDs.

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | `gradio deploy` → `HFValidationError: Repo id must use alphanumeric chars` | `gradio deploy` uses the README `title:` verbatim as the repo id; `title: AI Incident Explorer` has spaces | Set frontmatter `title: aiid-explorer` |
| 2 | Deploy about to upload `.venv` **and `.env`** to a public Space | `gradio deploy`'s `upload_folder` does **not** honor `.gitignore` | Abandoned `gradio deploy`; used `hf upload … --exclude` |
| 3 | Space build → `BUILD_ERROR: ResolutionImpossible` | Pinned `requirements.txt` had `pydantic==2.13.4`; HF auto-adds `gradio[oauth,mcp]` which needs `pydantic<=2.12.5` | Minimal `requirements.txt` with `pydantic<=2.12.5` ceiling |
| 4 | Live search → 0 incidents (no error banner) | AIID API bot-protection blocks non-browser UA from HF's datacenter IP; `aiid_search` swallows the error → `[]` | Send a browser `User-Agent`/`Referer` |
| 5 | Agent classifies incidents but loop reports 0 | `minimax/minimax-m3` omits the `HISTORICAL_RESULTS` markers; `_extract_block` required them and returned `""` | Salvage `⭐` lines when markers absent |
| 6 | Intermittent 0-results (~40% of rounds) on the Space | `minimax/minimax-m3` is weak at the CodeMode + strict-`⭐`-format task; ~40% of rounds it never stars anything even after retries | Switch outer model → `openai/gpt-5-mini` (variable, not secret) |
| 7 | `atlas_techniques` column shows `TBD …` / `none` instead of `AML.TXXXX` | Outer agent emits a **placeholder** technique id instead of a real MITRE ATLAS id from the inner judge | Prompt: require real `AML.TXXXX`, ban placeholders, add a common-techniques reference list |

---

## Issue 1 — `gradio deploy` rejects the repo name

**Symptom**
```
HFValidationError: Repo id must use alphanumeric chars, '-', '_' or '.'. … 'AI Incident Explorer'.
```

**Cause.** When a `README.md` with frontmatter already exists, `gradio deploy` skips its
interactive prompts and reads everything from the frontmatter — including using the
`title:` field **verbatim as the Space repo id** (`deploy_space.py` →
`create_repo(configuration["title"], …)`). "AI Incident Explorer" contains spaces, which
repo-id validation rejects.

**Fix.** Make `title:` a valid slug:
```yaml
title: aiid-explorer
```
The app's own `# AI Incident Explorer` H1 still renders, so only the small Space card
label changed.

---

## Issue 2 — `gradio deploy` ignores `.gitignore` (would leak `.env`)

**Symptom.** During upload: *"It seems you are trying to upload a large folder…"* — it was
pulling in the entire `.venv/` (23k+ files), which meant it was **also** about to upload
`.env` (the `OPENROUTER_API_KEY`) to a public Space.

**Cause.** `gradio deploy` calls `HfApi.upload_folder(...)` with no `ignore_patterns`, and
`upload_folder` does **not** read `.gitignore` (only `.git/` is auto-skipped). The Gradio
guide's wording ("respecting any `.gitignore`") is misleading.

**Fix.** Abandon `gradio deploy`; upload with explicit excludes instead:
```bash
hf upload edangx100/aiid-explorer . . --repo-type=space \
  --exclude=".env" --exclude=".venv/*" --exclude="**/__pycache__/**" \
  --exclude="*.pyc" --exclude="*.pyo" --exclude=".git/*"
```
Verified before uploading by simulating `filter_repo_objects` — of 23,632 files, **25**
were kept, with `.env` and `.venv` excluded.

---

## Issue 3 — Space build fails on a pydantic version conflict

**Symptom**
```
ERROR: Cannot install gradio[mcp,oauth]==6.16.0 and pydantic==2.13.4 …
    gradio[mcp,oauth] 6.16.0 depends on pydantic<=2.12.5 and >=2.11.10; extra == "mcp"
ERROR: ResolutionImpossible
```

**Cause.** The `requirements.txt` was a full `uv export` that hard-pinned
`pydantic==2.13.4`. HF Spaces always installs gradio with the **`[oauth,mcp]`** extras,
and the `mcp` extra caps pydantic at `<=2.12.5`. `pydantic-ai-slim` needs `>=2.12`, so the
compatible overlap is **pydantic 2.12.x**.

**Fix.** Replace the pinned export with a minimal top-level `requirements.txt` plus a
ceiling, and let pip resolve:
```
pydantic-ai==1.106.0
pydantic-settings>=2.14.1
pydantic<=2.12.5
# … (braintrust, gensim, gradio, httpx, pandas, pydantic-ai-harness[code-mode])
```
Resolution was verified locally against HF's exact install line (`uv pip compile`):
pydantic → 2.12.5, pydantic-ai stays 1.106.0.

> **Do not** regenerate `requirements.txt` with `uv export`, and **do not** remove the
> `pydantic<=2.12.5` line — either re-breaks the build.

---

## Issue 4 — AIID API returns 0 results from Spaces (browser-only API)

**Symptom.** Live search completes but finds 0 incidents; **no error banner** (so it looks
like a successful-but-empty search). Works perfectly locally.

**Cause.** The AIID GraphQL API sits behind bot protection that checks the client:
- No `Origin` header → `403 Forbidden - Invalid origin`.
- Wrong/`curl`-like `User-Agent` → `Forbidden - Invalid client / "API access is restricted
  to web browsers"`.

Our request sent the default `python-httpx/0.28.1` UA. That **passes from a residential
IP** but is **refused from HF's datacenter IP** (Cloudflare scores IP reputation + UA
together). `aiid_search()` catches every exception and returns `[]`, so the failure was
silent — the agent simply had nothing to classify.

**Evidence.** A boot probe added to the Space logged its egress IP and a raw AIID call:
```
egress_ip=3.228.31.30            (AWS datacenter)
raw status=200  (browser UA)     ← reachable once we send a browser UA
aiid_search('healthcare') -> 10 incidents
```

**Fix.** `explorer/aiid.py` — send browser-like headers alongside `Origin`:
```python
_HEADERS = {
    "Origin": "https://incidentdatabase.ai",
    "Referer": "https://incidentdatabase.ai/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
```

---

## Issue 5 — Agent finds incidents, loop reports 0 (missing markers)

**Symptom.** After Issue 4 was fixed, searches returned data on the Space but the run still
reported **0 incidents**.

**Cause.** The outer agent is asked to print its results wrapped in
`HISTORICAL_RESULTS_START` / `HISTORICAL_RESULTS_END` markers. `loop._extract_block()`
**required** those markers and returned `""` when they were absent. The model
(`minimax/minimax-m3`) prints the `⭐` result lines but substitutes its own `=====`
separator for the markers — so every round's findings were discarded. (This also affected
local runs; the end-to-end path had never been validated.)

**Evidence.** A local `run_harness("healthcare")` produced:
```
contains STAR(s): 10            ← 10 incidents classified
has START marker: False | END marker: False   ← markers omitted
```

**Fix.** `explorer/loop.py` — make `_extract_block` tolerant. Prefer the marker block; when
markers are absent, **salvage the self-identifying lines** (the `AIID Search:` headers and
every `⭐` line) — exactly what `parse_to_df()` / `_extract_found_with()` consume:
```python
start = agent_output.rfind(HISTORICAL_RESULTS_START)
end = agent_output.rfind(HISTORICAL_RESULTS_END)
if start != -1 and end != -1 and end >= start:
    return agent_output[start + len(HISTORICAL_RESULTS_START):end].strip()
salvaged = [
    line.rstrip()
    for line in agent_output.splitlines()
    if line.strip().startswith("AIID Search:") or "⭐" in line
]
return "\n".join(salvaged).strip()
```

**Verified end-to-end on the live Space** (Gradio client `/query_handler`):
`healthcare` → **10 incidents in 83s**, with clickable title links.

---

## Issue 6 — Intermittent 0-results: the model is the bottleneck

**Symptom.** After the salvage fix, the Space still returned 0 incidents on a meaningful
fraction of runs — my API test got 10, the user's manual test got 0, same code, same query.

**Cause.** Measured on the live Space, `minimax/minimax-m3` only produces a valid
classified-and-`⭐` round **~60%** of the time; ~40% of rounds it finishes without starring
*anything*, even after the 3 built-in validator retries. (Per-*attempt* success is only
~28%; `0.72³ ≈ 38%` matches the observed empty-round rate.) The search/parse/salvage are
solid — the model itself is weak at the CodeMode + strict-format task, and the free
Basic-CPU variance makes it worse. The `aiid_search` cache proved the search wasn't the
variable: a cached query still fed the agent 10 incidents on the 0-star runs.

**Model bake-off (all measured on the live Space, `healthcare`, max_rounds=1):**

| Outer model | Reliability | Speed/round | Verdict |
|---|---|---|---|
| `minimax/minimax-m3` | ~60% (flaky 0-results) | ~2–3 min | Cheap but unreliable |
| `google/gemini-2.5-flash` | **0% — errors** (see Issue 7-note) | n/a | Tool-calling incompatible via OpenRouter |
| `moonshotai/kimi-k2.6` | reliable | ~9 min | Reliable but too slow |
| **`openai/gpt-5-mini`** ✅ | **4/4 real** | ~3–7 min (avg ~5) | Reliable, moderate speed — **chosen** |

> **gemini-2.5-flash note:** it didn't just underperform, it *errored* — OpenRouter returned
> `finish_reason='error'`, which the OpenAI-format client (`OpenAIChatModel`) can't parse, so
> `run_loop` raised and the UI fell back to `DEMO_DATA` (the 5 hardcoded ids 901–905). Lesson:
> the app talks to OpenRouter through an OpenAI-format client, so the safest outer models for
> CodeMode tool-calling are **OpenAI models**, not Google ones.

**Fix.** Set the `OPENROUTER_MODEL` Space **variable** (not a secret) to `openai/gpt-5-mini`.

> ⚠️ **Variable vs secret collision.** The app's config (`OPENROUTER_MODEL`,
> `OPENROUTER_INNER_MODEL`, `MAX_ROUNDS`, …) is stored as Space **variables**. Adding a
> **secret** with the same name (`add_space_secret("OPENROUTER_MODEL", …)`) put the name in
> *both* namespaces → `CONFIG_ERROR: "Collision on variables and secrets names"`, with empty
> build/run logs. Fix: delete the duplicate secret; update the **variable** instead
> (`add_space_variable`).

**Diagnostic that mattered:** drive the Space headlessly with the **Gradio client**
(`Client(repo).predict("healthcare", 1, api_name="/query_handler")`) to measure hit-rate and
per-round time without manual clicking — and **detect demo-fallback** by checking whether the
returned incident ids are the `DEMO_DATA` set `{901..905}` (otherwise a fast 5-row error
masquerades as success).

---

## Issue 7 — `atlas_techniques` shows `TBD` / `none` instead of `AML.TXXXX`

**Symptom.** Real incidents returned, but the `atlas_techniques` column read e.g.
`TBD prompt injection` (gpt-5-mini) or `none` (minimax) instead of a real MITRE ATLAS id like
`AML.T0051 LLM Prompt Injection` — gutting the app's core value (ATLAS classification).

**Cause.** The outer agent emitted a **placeholder** as the technique id rather than calling
the inner LLM-as-a-Judge to get a real `AML.TXXXX`. The prompt asked it to classify "against
MITRE ATLAS" but didn't *forbid* placeholders or give the model a concrete id vocabulary, so
under its tight code-block budget it shortcut to `TBD`/`none`.

**Fix.** `explorer/prompts.py` — make the requirement explicit and provide a vocabulary:
- The `<technique_id>` **must** be a real `AML.TXXXX`, obtained via `llm_query`; **never**
  `TBD`/`none`/`N/A`/the search keyword. If no real technique fits, mark `-- not relevant`
  instead of starring with a placeholder.
- Added a **common-techniques reference list** (`AML.T0051` LLM Prompt Injection,
  `AML.T0054` LLM Jailbreak, `AML.T0057` LLM Data Leakage, `AML.T0043` Craft Adversarial
  Data, …) so valid ids flow even under budget pressure.

---

## Diagnostic techniques that mattered

- **Build logs via API:** `GET /api/spaces/<repo>/logs/build` (Bearer token) surfaced the
  pydantic conflict (Issue 3).
- **Boot probe → run logs:** a temporary `_boot_probe()` in `app.py` logged egress IP + raw
  AIID status/body to `…/logs/run`, which decisively separated "API unreachable" from
  "agent not producing results" (Issues 4 vs 5). Removed after diagnosis.
- **Don't swallow errors silently.** `aiid_search()` returning `[]` on every failure made
  Issue 4 look like an empty search. **Suggested follow-up:** surface "data source
  unreachable" as an error/warning banner so a blocked API never again reads as
  "0 incidents found."

## Files changed

| File | Change |
|------|--------|
| `README.md` | `title:` → `aiid-explorer` (frontmatter) |
| `requirements.txt` | Minimal deps + `pydantic<=2.12.5` ceiling |
| `explorer/aiid.py` | Browser `User-Agent`/`Referer` headers |
| `explorer/loop.py` | `_extract_block` salvages `⭐` lines when markers absent |
| `explorer/prompts.py` | Require real `AML.TXXXX` (ban `TBD`/`none`) + common-techniques reference list |
| Space **variable** `OPENROUTER_MODEL` | `minimax/minimax-m3` → `openai/gpt-5-mini` (outer agent; inner judge stays minimax) |
