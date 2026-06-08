# Outer loop generator — the seam between the frontend and the agent backend.
#
# run_loop() is a Python *generator*: instead of returning one value at the end, it
# `yield`s a stream of small event dicts as work happens. Gradio (the UI) iterates
# over those yields and updates the screen live — so the operator sees each round
# start, each AIID search, and each round finish as they occur.
#
# One loop iteration ("round") = one call to run_harness() (the outer agent). Between
# rounds we:
#   1. extract the agent's accumulated `historical_results` block from its output,
#   2. re-inject that block into the next round's message so the agent keeps its memory,
#   3. grow `past_queries` from the searches done so far (used by topic steering),
#   4. optionally switch the task to a steering prompt once STEERING_ROUND is reached.
#
# Event dicts yielded (see SPEC § Event dict shapes):
#   {"type": "round_start",    "round", "total_rounds", "task"}
#   {"type": "search",         "query"}
#   {"type": "round_complete", "round", "new_incidents", "total_incidents"}
#   {"type": "done",           "response": ExplorerResponse}
#   {"type": "error",          "message"}

import re
from collections.abc import Generator

from explorer.agents import run_harness
from explorer.config import settings
from explorer.db import df_to_incidents, parse_to_df
from explorer.models import ExplorerResponse
from explorer.prompts import HISTORICAL_RESULTS_END, HISTORICAL_RESULTS_START
from explorer.topic import next_topic_prompt


# Matches the search string inside a generated `aiid_search(...)` call so we can report
# it in a `search` event. Handles both `aiid_search("x")` and `aiid_search(query="x")`,
# with single or double quotes.
_SEARCH_QUERY_RE = re.compile(r"""aiid_search\(\s*(?:query\s*=\s*)?["']([^"']*)["']""")


def _extract_search_query(code: str) -> str:
    """Pull the search string out of a generated aiid_search(...) call.

    The on_code_execute hook hands us the whole code block as a string; we only want
    the query text for the `search` event. If we can't find one, return "" rather than
    failing — a missing label should never crash the loop.
    """
    match = _SEARCH_QUERY_RE.search(code)
    return match.group(1) if match else ""


def _extract_block(agent_output: str) -> str:
    """Return the text between the HISTORICAL_RESULTS markers, or "" if absent.

    The outer agent prints its accumulated results wrapped in
    HISTORICAL_RESULTS_START / HISTORICAL_RESULTS_END. It may print more than once in a
    run, so we take the LAST block (rfind = search from the end) — that is the most
    up-to-date version. When the validator gave up and printed no markers, we get "".
    """
    start = agent_output.rfind(HISTORICAL_RESULTS_START)
    end = agent_output.rfind(HISTORICAL_RESULTS_END)
    if start == -1 or end == -1 or end < start:
        return ""
    # Slice out just the inner block (skip past the START marker text itself).
    return agent_output[start + len(HISTORICAL_RESULTS_START):end].strip()


def _count_stars(block: str) -> int:
    """Count ⭐ lines — i.e. how many relevant incidents the block records.

    The ⭐ prefix is the agent's only "this incident is relevant" signal, so counting
    stars counts incidents. We count per line so two stars on one line can't inflate it.
    """
    return sum(1 for line in block.splitlines() if "⭐" in line)


def _extract_found_with(block: str) -> list[str]:
    """Collect the short search strings from each `AIID Search:` line in the block.

    These `found_with` values — NOT the full task/steering prompt — are what feed topic
    steering's LDA. We read them straight off the historical_results so past_queries
    always reflects the searches actually run.
    """
    queries: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("AIID Search:"):
            query = stripped[len("AIID Search:"):].strip()
            if query:
                queries.append(query)
    return queries


def _compose_user_message(task: str, historical_results: str) -> str:
    """Build one round's message: the task plus the re-injected results block.

    Wrapping historical_results in the markers lets the agent restore its memory at the
    top of its first code block, and lets the NEXT round's _extract_block find it again.
    """
    block = f"{HISTORICAL_RESULTS_START}\n{historical_results}\n{HISTORICAL_RESULTS_END}"
    return f"{task}\n\n{block}"


def run_loop(
    user_query: str,
    max_rounds: int = settings.MAX_ROUNDS,
    # steering_round defaults from settings; backend-only, not exposed in the Gradio UI.
    steering_round: int | None = settings.STEERING_ROUND,
) -> Generator[dict, None, None]:
    """Drive the RLM outer loop, yielding event dicts for the UI to render.

    Runs up to `max_rounds` rounds. Rounds before `steering_round` search on the
    verbatim `user_query`; from `steering_round` onward the task is replaced with a
    topic-steering prompt that pushes toward unexplored techniques/industries. When
    `steering_round` is None, steering is disabled and every round uses `user_query`.
    """
    # Accumulated state carried across rounds.
    historical_results = ""   # the marker block's inner text; the agent's running memory
    past_queries: list[str] = []  # found_with search strings, for topic steering's LDA
    rounds_run = 0

    try:
        for round_index in range(max_rounds):
            # ── Choose this round's task ──────────────────────────────────────────
            # Steering kicks in at steering_round (when enabled). Until then we search
            # the user's query verbatim to build up the initial corpus.
            steering_active = steering_round is not None and round_index >= steering_round
            if steering_active:
                task = next_topic_prompt(user_query, past_queries)
            else:
                task = user_query

            yield {
                "type": "round_start",
                "round": round_index,
                "total_rounds": max_rounds,
                "task": task,
            }

            # ── Run one round of the outer agent ──────────────────────────────────
            user_message = _compose_user_message(task, historical_results)

            # The harness can't yield (it runs deep inside async code), so its
            # on_code_execute callback just appends each detected search query here;
            # we emit the `search` events ourselves once the round returns.
            round_searches: list[str] = []

            # `sink=round_searches` binds THIS round's list as a default argument. We
            # rebuild on_search every round, and a default is captured when the function
            # is defined — so each round's callback writes to its own list, never to a
            # later round's. (A plain closure over `round_searches` would all point at
            # whatever the variable holds at call time, which is the classic late-binding
            # trap in loops.)
            def on_search(code: str, sink: list[str] = round_searches) -> None:
                sink.append(_extract_search_query(code))

            agent_output = run_harness(user_message, on_search)
            rounds_run += 1

            # Emit one `search` event per aiid_search() call made this round.
            for query in round_searches:
                yield {"type": "search", "query": query}

            # ── Fold this round's results into our running state ──────────────────
            stars_before = _count_stars(historical_results)

            new_block = _extract_block(agent_output)
            if new_block:
                # Only overwrite when the agent actually produced a block; if the
                # validator gave up (no markers), keep what we already had.
                historical_results = new_block

            stars_after = _count_stars(historical_results)
            # New incidents = the increase in ⭐ lines this round. A round that
            # exhausted MAX_VALIDATOR_RETRIES adds no stars, so this is 0.
            new_incidents = max(0, stars_after - stars_before)

            # Refresh past_queries from the (now updated) historical_results.
            past_queries = _extract_found_with(historical_results)

            yield {
                "type": "round_complete",
                "round": round_index,
                "new_incidents": new_incidents,
                "total_incidents": stars_after,
            }

        # ── All rounds done: assemble the final ExplorerResponse ──────────────────
        # parse_to_df() turns the marker block into an exploded DataFrame; df_to_incidents()
        # collapses it back into AIIncident objects (both live in explorer/db.py).
        incidents = df_to_incidents(parse_to_df(historical_results))
        response = ExplorerResponse(
            incidents=incidents,
            total=len(incidents),
            rounds_run=rounds_run,
        )
        yield {"type": "done", "response": response}

    except Exception as exc:  # noqa: BLE001 — any failure becomes a single error event
        # On any unhandled error, tell the UI and stop. Returning from a generator
        # ends iteration cleanly (no further events are produced).
        yield {"type": "error", "message": str(exc)}
