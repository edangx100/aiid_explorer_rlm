---
title: AI Incident Explorer
emoji: 🛡️
colorFrom: indigo
colorTo: red
sdk: gradio
sdk_version: 6.16.0
app_file: app.py
python_version: "3.11"
pinned: false
---

# AI Incident Explorer

> An autonomous agentic tool that continuously mines the [AI Incident Database (AIID)](https://incidentdatabase.ai) to surface, classify, and triage real-world AI failures and attacks — mapping each one to **MITRE ATLAS** adversarial-ML techniques via an **LLM-as-a-Judge** classifier, with **full agent observability**.

---

## Why This Project Exists

**Agents that run over a long horizon predictably hit two failure modes:** **context rot** (their context fills with stale tool output until they forget the goal and degrade) and **query narrowing** (they tunnel on a few lines of inquiry and go blind to the rest of the problem space). 

**This project is a demonstration of how both can be addressed — context rot with a [Recursive Language Model (RLM)](https://arxiv.org/html/2512.24601v3) architecture, and query narrowing with [LDA](https://www.ibm.com/think/topics/topic-modeling)-based topic steering** — using **AI Incident Explorer** as the use case.

> **What's an RLM?** A *Recursive Language Model* keeps the agent's working state in a sandboxed Python REPL instead of stuffing every tool result back into the prompt. The model writes and runs code to inspect, filter, and summarize its own data, so the context window stays small and clean no matter how many rounds it runs. See [RLM paper](https://arxiv.org/html/2512.24601v3)

> **What's LDA topic steering?** *Latent Dirichlet Allocation* is a [topic-modeling](https://www.ibm.com/think/topics/topic-modeling) technique that discovers the recurring themes hidden in a set of documents. Here it runs over the agent's *own past search queries* to reveal which themes it keeps circling — so the next round can be steered toward the topics it hasn't explored yet.

| Problem | Symptom | How this project solves it |
|---|---|---|
| **Context rot** | Over many search rounds the agent's context fills with stale tool output and it forgets its goal, repeats work, and degrades. | The agent keeps its working notes in a separate scratchpad (a Python workspace) instead of letting them pile up in its memory. Each round it pulls back only a short summary of what it's found so far, so its attention stays focused. |
| **Query narrowing** | Left alone, the agent re-issues near-identical searches and tunnels on one or two ATLAS techniques, leaving the rest of the threat surface blind. | The app spots when the agent keeps searching the same themes, then points it toward the threat types and industries it hasn't looked at yet — so it covers the wider landscape instead of circling a few corners. |

As a use case, AI Incident Explorer answers questions like — *"What MITRE ATLAS techniques are being used against LLMs in finance/healthcare/consumer products right now?"* — by autonomously searching the public record of AI incidents over many rounds and returning a structured, technique-tagged, severity-rated result set.

Under the hood it is an **RLM agent**: an outer agent writes and runs Python in a sandboxed REPL to search and triage — keeping state in the REPL rather than the prompt (the context-rot fix) — while an inner **LLM-as-a-Judge** agent classifies each incident against the MITRE ATLAS taxonomy. After seeding rounds, an **LDA** model clusters the agent's own past searches and steers it toward unexplored techniques (the query-narrowing fix).

---

## What AI Incident Explorer does

- **Autonomous, multi-round search** — give it one natural-language query; it runs up to *N* search/triage rounds on its own. One search only scratches the surface: the incidents are scattered under many different wordings, so a single query misses most of them. Each round lets the agent learn from what it just found and follow up with sharper, broader searches — building a fuller picture than any one-shot lookup could.
- **MITRE ATLAS classification** — every relevant incident is tagged with one or more adversarial-ML technique IDs (e.g. `AML.T0051` LLM Prompt Injection, `AML.T0043` deepfakes, `AML.T0047` model evasion).
- **LLM-as-a-Judge triage** — an inner agent decides *security attack vs. safety failure vs. reliability issue* and assigns a harm-severity rating.

Everything it finds shows up in a sortable table. Click any incident's **title** to open its full write-up on the AI Incident Database, and use the type and severity filters to focus on just the cases you care about.

---

## Workflow

![System architecture](images/architecture.png)

*Core context-rot mitigation — the outer agent orchestrates; the inner agent judges. State lives in the REPL, not the prompt.*

---

## Implementation Notes

- **RLM / Code-Mode agent.** Instead of cramming hundreds of incidents into one context window, the outer agent writes Python that searches and triages in a persistent sandboxed REPL powered by [Pydantic AI Harness **CodeMode**](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/code_mode). The agent is prompted with Recursive Language Model strategies — *PEEK* (glance at a few examples before diving in), *GREP* (skim for the relevant ones instead of reading everything), *PARTITION+MAP* (break a big pile into smaller batches and work through them), and *SUMMARIZE* (boil long text down to the key point).

  ![Recursive Language Model strategies — PEEK, GREP, PARTITION+MAP, SUMMARIZE](images/RLM_strategy.png)

  *The RLM strategies the outer agent uses to inspect and shrink data inside the REPL — keeping only what matters in context. Diagram source: [Daily Dose of Data Science — Recursive Language Models](https://blog.dailydoseofds.com/p/recursive-language-models).*

- **LLM-as-a-Judge classifier.** A second inner LLM agent acts as a reviewer that the app can call on for each incident. For every case it reads, it hands back the relevant ATLAS technique IDs and a triage category (how the incident should be sorted). This reviewer is set up separately from the main app, so you can drop in a cheaper or faster model for the job without changing anything else.
- **LDA topic steering.** Once a few rounds have run, the app looks back at the agent's own past search terms and groups them into recurring themes (using gensim's LDA topic modeling). Seeing which themes the agent keeps circling, it then nudges the next search toward the areas it hasn't explored yet — so coverage keeps widening instead of tunneling on the same few topics.

  ![LDA topic steering widens coverage by nudging the agent toward unexplored themes](images/widen_coverage.png)

---

## What the User Sees

With all of those pieces working together, here's what it looks like to the user in the Gradio interface — note the MITRE ATLAS classifications shown along the bottom of the table:

![ATLAS-tagged results with type + severity filters applied](images/atlas-filters.png)

*Results tagged with MITRE ATLAS technique IDs; filter by type and severity.*

---

## Tech stack

| Layer | Choice |
|---|---|
| Data source | **[Artificial Intelligence Incident Database](https://incidentdatabase.ai/)** |
| Language / runtime | Python 3.11+ (managed with **uv**) |
| Agent framework | **Pydantic AI** — CodeMode + Hooks API ([pydantic-ai-harness](https://github.com/pydantic/pydantic-ai-harness)) |
| Model provider | **[OpenRouter](https://openrouter.ai/)** (outer + inner models independently configurable) |
| Topic modelling | **[gensim](https://radimrehurek.com/gensim/)** LDA |
| Observability | **[Braintrust](https://www.braintrust.dev/)** tracing |
| UI | **Gradio** |

---

## Using it

### On Hugging Face Spaces (no install)

1. Type a short keyword in **Query** (one or two words works best).
2. Click **Search** and watch the status banner — a live round takes ~1.5–3 minutes.
3. Use the **type** and **severity** filters to slice the results.

> A live search calls a real LLM, so it is not instant. If the backend is unavailable the app falls back to bundled demo data so the table is never blank.

### Running it locally

```bash
# 1. Install dependencies (uv-managed virtual environment)
uv sync

# 2. Configure secrets — copy the template and fill in your keys
cp .env.example .env
#   OPENROUTER_API_KEY=...     OPENROUTER_MODEL=minimax/minimax-m3
#   BRAINTRUST_API_KEY=...     BRAINTRUST_PROJECT=...

# 3. Launch the app
uv run python -m frontend.app
```

Then open the local Gradio URL, type a threat question (e.g. *"LLM chatbot manipulation and harmful outputs"*), and watch the agent work.

---

## Configuration

Set as a Space secret (or in your local `.env`):

- `OPENROUTER_API_KEY` — **required**; the app calls LLMs via OpenRouter.

`BRAINTRUST_API_KEY` is optional (observability traces only) and can be left unset.
