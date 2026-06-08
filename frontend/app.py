# Gradio UI entry point — wires the run_loop() generator to UI components.
# frontend/ only imports from explorer/; explorer/ never imports from frontend/.
# Swapping Gradio for another UI means replacing this file only.
#
# Big picture of how this screen works:
#   1. The operator types a query and clicks "Search".
#   2. query_handler() drives the backend generator run_loop() and `yield`s
#      updated (status text, results table) pairs as each round happens. Gradio
#      shows every yield live, so the user watches progress in real time.
#   3. The final results are kept in a hidden gr.State so the type/severity
#      checkboxes can filter them instantly without re-running the search.

import html
import time

import gradio as gr
import pandas as pd

from explorer.loop import run_loop
from explorer.db import incidents_to_df, DISPLAY_COLUMNS
from explorer.models import AIIncident, ATLASTechnique


# ── Column layout for the results table ─────────────────────────────────────────
# incidents_to_df() produces exactly these columns. We reuse its DISPLAY_COLUMNS list
# (instead of repeating the names) so the empty starting table always has the same
# shape as a populated one — otherwise Gradio would show a differently-shaped grid
# before and after a search.
RESULT_COLUMNS = DISPLAY_COLUMNS

# Per-column rendering for the results grid: the "title" column holds a Markdown link
# to each incident's source page, so it is rendered as Markdown (making the title
# clickable); every other column is plain text. The list lines up one-to-one with
# RESULT_COLUMNS above.
RESULT_DATATYPES = ["markdown" if col == "title" else "str" for col in RESULT_COLUMNS]

# The closed vocabularies from models.py. These drive the checkbox filter options so
# the UI can never offer a value the data model doesn't allow.
INCIDENT_TYPES = ["security_attack", "safety_failure", "reliability_issue"]
SEVERITIES = ["critical", "high", "medium", "low"]

# Ready-made queries shown as clickable chips under the Query box, so a new user can
# try the app in one click instead of guessing what to type. Each is a short keyword
# (the agent searches AIID best with one or two words — see prompts.py) that reliably
# returns real incidents. Clicking a chip just fills the Query box; the user still
# presses Search to run it.
EXAMPLE_QUERIES = [
    "healthcare",
    "chatbot",
    "prompt injection",
]


# ── Status banner styling ───────────────────────────────────────────────────────
# The search is SLOW (it runs live AI agents), so the status needs to be big and
# obvious — not a tiny line the user might miss. This CSS is injected once into the
# whole page (passed to launch() at the bottom of this file) and styles the banner
# built by _status_html(). We target our own CSS classes so nothing else changes.
STATUS_CSS = """
.status-banner {
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 1.6rem;          /* much larger than the old default text */
    font-weight: 700;
    line-height: 1.35;
    padding: 22px 26px;
    border-radius: 12px;
    margin: 8px 0;
    border: 2px solid;
}
/* A small grey, muted second line for the time estimate / elapsed counter. */
.status-banner .status-hint {
    display: block;
    font-size: 0.95rem;
    font-weight: 400;
    opacity: 0.85;
    margin-top: 6px;
}
/* One colour per state so the meaning is obvious at a glance. */
.status-idle    { color:#9ca3af; border-color:#4b5563; background:rgba(75,85,99,.12); }
.status-loading { color:#f97316; border-color:#f97316; background:rgba(249,115,22,.14); }
.status-done    { color:#22c55e; border-color:#22c55e; background:rgba(34,197,94,.14); }
.status-error   { color:#ef4444; border-color:#ef4444; background:rgba(239,68,68,.14); }
/* While loading, the whole banner gently pulses so it clearly looks "alive". */
.status-loading { animation: status-pulse 1.5s ease-in-out infinite; }
@keyframes status-pulse { 0%,100% { opacity:1; } 50% { opacity:.72; } }
/* A spinning ring shown only in the loading state. */
.status-spinner {
    width: 28px; height: 28px; flex: none;
    border: 4px solid rgba(249,115,22,.30);
    border-top-color: #f97316;
    border-radius: 50%;
    animation: status-spin .8s linear infinite;
}
@keyframes status-spin { to { transform: rotate(360deg); } }
"""

# Rough per-round timing used only to set the user's expectation. A live round runs
# an AI agent plus several AIID searches; measured runs land around 1.5–3 minutes
# each, so we show that range multiplied by the number of rounds requested.
SECONDS_PER_ROUND_LOW = 90
SECONDS_PER_ROUND_HIGH = 180


def _status_html(text: str, state: str, hint: str | None = None) -> str:
    """Wrap a status message in the big, colour-coded banner shown above the filters.

    `state` is one of "idle" | "loading" | "done" | "error" and picks the colour,
    the icon, and whether the spinner/pulse animation is shown. `hint` is an optional
    smaller second line (we use it for the time estimate and elapsed counter).

    We escape the text and convert newlines to <br> ourselves because this is rendered
    as raw HTML (gr.HTML), so untrusted characters must not break the markup.
    """
    spinner = '<span class="status-spinner"></span>' if state == "loading" else ""
    # A leading symbol reinforces the state for anyone who can't rely on colour alone.
    icon = {"idle": "", "loading": "", "done": "✅ ", "error": "⚠️ "}[state]
    safe_text = html.escape(text).replace("\n", "<br>")
    hint_html = f'<span class="status-hint">{html.escape(hint)}</span>' if hint else ""
    return (
        f'<div class="status-banner status-{state}">'
        f'{spinner}'
        f'<span>{icon}{safe_text}{hint_html}</span>'
        f'</div>'
    )


def _eta_hint(max_rounds: int, elapsed_seconds: int) -> str:
    """Build the small 'how long will this take' line shown while a search runs.

    Gives a rough total-time range for the chosen number of rounds plus a live
    elapsed counter, so the user knows the wait is expected and roughly how long.
    """
    low_min = max(1, round(max_rounds * SECONDS_PER_ROUND_LOW / 60))
    high_min = max(low_min, round(max_rounds * SECONDS_PER_ROUND_HIGH / 60))
    return (
        f"Live AI search running — usually ~{low_min}–{high_min} min "
        f"for {max_rounds} round(s). Elapsed {elapsed_seconds}s…"
    )


# ── Demo / fallback data ────────────────────────────────────────────────────────
# Used when run_loop() fails (e.g. the network or an API key is unavailable) so the
# operator still sees a realistic, populated table instead of a blank screen.
# Covers five ATLAS techniques across five different industries.
DEMO_DATA: list[AIIncident] = [
    AIIncident(
        incident_id=901,
        title="Hospital triage chatbot leaks records via crafted prompt",
        description=(
            "An attacker embedded hidden instructions in a patient message that "
            "caused a clinical LLM assistant to reveal other patients' records."
        ),
        incident_type="security_attack",
        atlas_techniques=[
            ATLASTechnique(technique_id="AML.T0051", name="LLM Prompt Injection"),
        ],
        industry="healthcare",
        harm_severity="critical",
        found_with="healthcare LLM prompt injection",
    ),
    AIIncident(
        incident_id=902,
        title="Adversarial stickers fool self-driving car sign detector",
        description=(
            "Small printed stickers placed on stop signs caused an autonomous "
            "vehicle's perception model to misclassify them as speed-limit signs."
        ),
        incident_type="safety_failure",
        atlas_techniques=[
            ATLASTechnique(technique_id="AML.T0043", name="Craft Adversarial Data"),
        ],
        industry="automotive",
        harm_severity="high",
        found_with="autonomous vehicle adversarial sign",
    ),
    AIIncident(
        incident_id=903,
        title="Consumer assistant jailbroken into producing disallowed content",
        description=(
            "A role-play prompt bypassed the safety guardrails of a popular "
            "consumer AI assistant, producing content its policy forbids."
        ),
        incident_type="security_attack",
        atlas_techniques=[
            ATLASTechnique(technique_id="AML.T0054", name="LLM Jailbreak"),
        ],
        industry="consumer AI",
        harm_severity="high",
        found_with="consumer chatbot jailbreak",
    ),
    AIIncident(
        incident_id=904,
        title="Fraud-scoring model probed through public inference API",
        description=(
            "Attackers repeatedly queried a bank's fraud-scoring API to reverse "
            "engineer the threshold and slip fraudulent transactions through."
        ),
        incident_type="security_attack",
        atlas_techniques=[
            ATLASTechnique(technique_id="AML.T0040", name="ML Model Inference API Access"),
        ],
        industry="finance",
        harm_severity="medium",
        found_with="finance model inference api abuse",
    ),
    AIIncident(
        incident_id=905,
        title="Facial recognition misidentifies suspect in police dragnet",
        description=(
            "A law-enforcement facial-recognition product returned a false match, "
            "leading to the wrongful detention of an innocent person."
        ),
        incident_type="reliability_issue",
        atlas_techniques=[
            ATLASTechnique(technique_id="AML.T0047", name="ML-Enabled Product or Service"),
        ],
        industry="law enforcement",
        harm_severity="critical",
        found_with="facial recognition false match police",
    ),
]


def empty_df() -> pd.DataFrame:
    """Return a results table with the right columns but no rows.

    Used as the table's starting value and as a reset between searches.
    """
    return pd.DataFrame(columns=RESULT_COLUMNS)


def apply_filters(
    full_df: pd.DataFrame | None,
    type_filter: list[str],
    severity_filter: list[str],
) -> pd.DataFrame:
    """Filter the results table by the active type/severity checkboxes.

    Filter rules (from the spec):
      • Within one group the checked values are OR'd together
        (e.g. type = [security_attack, safety_failure] keeps rows that are EITHER).
      • Across the two groups the conditions are AND'd
        (a row must match the type group AND the severity group).
      • An empty group means "no constraint" — every value in that group passes.
      • No active chips at all returns the full, unfiltered table.

    pandas does the OR-within-a-group for us: Series.isin([...]) is True when the
    cell equals any value in the list. Applying the two .isin() filters one after
    the other gives the AND-across-groups behaviour.
    """
    # Nothing to filter (e.g. before any search ran).
    if full_df is None or len(full_df) == 0:
        return empty_df()

    df = full_df

    # Only narrow by type if at least one type chip is active.
    if type_filter:
        df = df[df["incident_type"].isin(type_filter)]

    # Then (AND) narrow by severity if at least one severity chip is active.
    if severity_filter:
        df = df[df["harm_severity"].isin(severity_filter)]

    return df


def query_handler(query: str, max_rounds: int):
    """Run a search and stream live updates to the UI.

    This is a *generator*: every `yield` pushes a new (status, table, saved-table)
    snapshot to the screen. Gradio re-renders the components on each yield, so the
    operator watches rounds and searches appear in real time.

    The three yielded values map to three Gradio outputs:
      1. status markdown  — human-readable progress text
      2. results table    — what's shown in the grid right now
      3. full results     — saved into a hidden gr.State so the filter checkboxes
                            can work on the complete result set later
    """
    # Wall-clock start, so we can show a live "Elapsed Ns…" counter in the banner.
    start = time.monotonic()

    def elapsed() -> int:
        return int(time.monotonic() - start)

    status = "Starting…"
    state = "loading"               # idle | loading | done | error — drives the banner
    results_df = empty_df()
    # Show the initial "Starting…" state immediately, with the time estimate so the
    # user knows up front that this is a slow, multi-minute operation.
    yield _status_html(status, state, _eta_hint(max_rounds, elapsed())), results_df, results_df

    try:
        # run_loop yields small event dicts describing what just happened. We
        # translate each one into a status line and (on completion) a results table.
        for event in run_loop(query, max_rounds):
            kind = event["type"]
            hint = _eta_hint(max_rounds, elapsed())  # default hint for in-progress states

            if kind == "round_start":
                # A new round began — replace the status with its header.
                status = f"Round {event['round']} of {event['total_rounds']}…"
                state = "loading"

            elif kind == "search":
                # The agent fired an AIID search — append it under the current round.
                status += f"\n\nSearching: {event['query']}"
                state = "loading"

            elif kind == "round_complete":
                # The round finished — summarise how many incidents we have so far.
                status = (
                    f"Round {event['round']} complete — "
                    f"{event['total_incidents']} incidents found"
                )
                state = "loading"

            elif kind == "done":
                # All rounds finished. Build the display table from the final
                # incident list (techniques collapsed to a comma-separated string).
                results_df = incidents_to_df(event["response"].incidents)
                state = "done"
                # Replace the estimate with the actual finish time.
                hint = f"Completed in {elapsed()}s."

            elif kind == "error":
                # The backend reported a recoverable error event. Fall back to demo
                # data so the table is never empty, and show the message.
                status = f"Error: {event['message']}"
                results_df = incidents_to_df(DEMO_DATA)
                state = "error"
                hint = None

            # Push this snapshot to the UI before handling the next event.
            yield _status_html(status, state, hint), results_df, results_df

    except Exception as exc:  # noqa: BLE001 — any crash should degrade gracefully
        # run_loop raised instead of yielding an error event (e.g. it blew up before
        # the try/except inside it). Same fallback: demo data + an error message.
        status = f"Error: {exc}"
        results_df = incidents_to_df(DEMO_DATA)
        yield _status_html(status, "error"), results_df, results_df


def build_app() -> gr.Blocks:
    """Construct the Gradio Blocks UI and wire up the event handlers.

    Returning the Blocks (instead of launching here) lets tests build the app and
    assert it constructs without errors, without actually starting a web server.
    """
    with gr.Blocks(title="AI Incident Explorer") as demo:
        gr.Markdown("# AI Incident Explorer (& MITRE ATLAS classifier)")
        gr.Markdown(
            "Mine the AI Incident Database for real-world AI failures and attacks, "
            "classified against MITRE ATLAS techniques."
        )

        # Hidden store for the complete, unfiltered results. The visible table may be
        # a filtered subset of this. gr.State is per-user session memory that never
        # renders on screen.
        full_results = gr.State(value=empty_df())

        # ── Live status ─────────────────────────────────────────────────────────
        # A big, colour-coded banner (built by _status_html). Starts in the neutral
        # "idle" state; query_handler swaps it to the loading/done/error states.
        status_md = gr.HTML(_status_html("Enter a query and click Search.", "idle"))


        # ── Query controls ──────────────────────────────────────────────────────
        with gr.Row():
            query_box = gr.Textbox(
                label="Query",
                placeholder="e.g. LLM jailbreaks in healthcare",
                scale=4,  # take most of the row width
            )
            search_btn = gr.Button("Search", variant="primary", scale=1)

        # Clickable example queries. Selecting one fills query_box (its `inputs`); the
        # user then presses Search to run it. label="" keeps the chips tight under the box.
        gr.Examples(
            examples=[[q] for q in EXAMPLE_QUERIES],
            inputs=[query_box],
            label="Try an example:",
        )

        max_rounds_slider = gr.Slider(
            minimum=1,
            maximum=20,
            value=1,
            step=1,
            label="Max rounds",
        )

        # ── Filters ─────────────────────────────────────────────────────────────
        # These filter the already-fetched results client-side; they never re-run
        # the search. No boxes checked = everything passes.
        type_filter = gr.CheckboxGroup(
            choices=INCIDENT_TYPES,
            label="Filter by type",
            value=[],
        )
        severity_filter = gr.CheckboxGroup(
            choices=SEVERITIES,
            label="Filter by severity",
            value=[],
        )

        # ── Results table ───────────────────────────────────────────────────────
        results_table = gr.DataFrame(
            value=empty_df(),
            label="Results",
            wrap=True,  # wrap long text (descriptions, technique lists) in cells
            # Render the "title" column as a clickable Markdown link (see RESULT_DATATYPES).
            datatype=RESULT_DATATYPES,
        )

        # ── Wiring ──────────────────────────────────────────────────────────────
        # Clicking Search runs query_handler. Its three yielded values flow into
        # the three listed outputs, in order. Because query_handler is a generator,
        # Gradio streams each yield to the screen as it arrives.
        search_btn.click(
            fn=query_handler,
            inputs=[query_box, max_rounds_slider],
            outputs=[status_md, results_table, full_results],
        )

        # When either checkbox group changes, re-filter the saved full results and
        # update only the visible table. apply_filters reads the hidden full_results
        # state plus both filter selections.
        for control in (type_filter, severity_filter):
            control.change(
                fn=apply_filters,
                inputs=[full_results, type_filter, severity_filter],
                outputs=[results_table],
            )

    return demo


# When run as `python -m frontend.app`, build the app and start the local server.
# In Gradio 6 the page-wide CSS is passed to launch() (not the Blocks constructor), so
# the status-banner styles in STATUS_CSS are injected here.
if __name__ == "__main__":
    build_app().launch(css=STATUS_CSS)
