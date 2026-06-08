# Outer agent (CodeMode + Hooks API) with llm_query() as an inner callable.
#
# Two agents live here:
#   - The INNER agent, wrapped by llm_query(), classifies a single incident against
#     MITRE ATLAS techniques. It is small and called many times from generated code.
#   - The OUTER agent runs in CodeMode: instead of calling tools one at a time, it
#     writes Python code that calls aiid_search() and llm_query() as plain functions
#     inside a sandboxed REPL. run_harness() drives one round of that agent.
#
# The "hooks" are small, independently testable helper functions used while driving the outer agent:
#   - stop_after_n_calls  -> a STOPPER: halts a run once enough code blocks have executed.
#   - has_new_incidents   -> a VALIDATOR: rejects a run whose output has no ⭐ lines.
#   - on_code_execute     -> fires a callback whenever generated code calls aiid_search().
#
# The stopper and validator use INDEPENDENT budgets: stop_after_n_calls counts code-block
# executions inside a single run; has_new_incidents counts validator retries across runs.

import asyncio
import contextlib

import braintrust
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai_harness import CodeMode
from braintrust.integrations.pydantic_ai import setup_pydantic_ai

from explorer.aiid import aiid_search
from explorer.config import settings
from explorer.prompts import INNER_AGENT_SYSTEM_PROMPT, OUTER_AGENT_SYSTEM_PROMPT

# Name of the meta-tool CodeMode exposes to the model. Every code block the outer agent
# runs arrives as a `run_code` tool call, so we match on this name while driving the loop.
_RUN_CODE_TOOL = "run_code"

# Appended to the task when has_new_incidents rejects a run, nudging the agent to retry.
_TRY_HARDER_MESSAGE = (
    "No new incidents (no ⭐ lines) were found in your last attempt. "
    "Try a different AIID search query or relax your filtering, then append any "
    "relevant incidents to historical_results with a ⭐ prefix."
)

# Wire Braintrust tracing before creating agents so every agent run emits a span.
# When BRAINTRUST_API_KEY is absent (local dev), setup is skipped and tracing is a no-op.
if settings.BRAINTRUST_API_KEY:
    setup_pydantic_ai(
        api_key=settings.BRAINTRUST_API_KEY,
        project_name=settings.BRAINTRUST_PROJECT,
    )


@contextlib.contextmanager
def _trace(name: str):
    """Open a named Braintrust span so the trace tree is readable.

    setup_pydantic_ai names every agent span the same thing ("pydantic_ai.agent.run"),
    so the outer and inner agents are indistinguishable on their own. Wrapping each
    agent invocation in a span named "outer_agent" / "inner_agent" makes the two clearly
    separable in Braintrust: inner spans nest under the outer span that triggered them.

    When Braintrust is not configured the span is skipped and this is a plain no-op.
    """
    if settings.BRAINTRUST_API_KEY:
        with braintrust.start_span(name=name):
            yield
    else:
        yield


# ── Inner agent + llm_query() ─────────────────────────────────────────────────

# Build the OpenRouter provider with the API key from settings rather than from the
# OPENROUTER_API_KEY environment variable, since pydantic-settings reads .env but does
# not populate os.environ.
_inner_provider = OpenRouterProvider(api_key=settings.OPENROUTER_API_KEY)
_inner_model = OpenAIChatModel(settings.OPENROUTER_INNER_MODEL, provider=_inner_provider)

# Inner agent — dedicated to sub-tasks like ATLAS technique classification. Kept separate
# from the outer agent so it can use a cheaper/faster model (OPENROUTER_INNER_MODEL).
_inner_agent = Agent(
    model=_inner_model,
    name="inner_agent",
    system_prompt=INNER_AGENT_SYSTEM_PROMPT,
)


def llm_query(prompt: str) -> str:
    """Query the inner LLM with a prompt and return its text response.

    Called from the outer agent's generated Python code inside CodeMode. Each call starts
    a fresh conversation with the inner agent. The "inner_agent" span makes these calls
    show up distinctly under the outer agent's span in Braintrust.
    """
    with _trace("inner_agent"):
        return _inner_agent.run_sync(prompt).output


# ── Outer agent (CodeMode) ────────────────────────────────────────────────────

_outer_provider = OpenRouterProvider(api_key=settings.OPENROUTER_API_KEY)
_outer_model = OpenAIChatModel(settings.OPENROUTER_MODEL, provider=_outer_provider)

# The outer agent writes Python that calls these two functions. We register them with
# sequential=True so CodeMode exposes them as ordinary *synchronous* functions in the
# sandbox — generated code calls them as `aiid_search(query=...)` / `llm_query(prompt=...)`
# (keyword args, no `await`) 
_outer_agent = Agent(
    model=_outer_model,
    name="outer_agent",
    system_prompt=OUTER_AGENT_SYSTEM_PROMPT,
    tools=[
        Tool(aiid_search, sequential=True),
        Tool(llm_query, sequential=True),
    ],
    capabilities=[CodeMode()],

)


# ── Hooks: stopper, validator, and code-execute callback ──────────────────────

def stop_after_n_calls(call_count: int, limit: int) -> bool:
    """STOPPER hook: True once `call_count` code blocks have executed.

    It is a safety budget that prevents a single agent run from looping forever;
    it is independent of the validator's budget.
    """
    return call_count >= limit


def has_new_incidents(output: str) -> bool:
    """VALIDATOR hook: True if the run's output contains at least one ⭐ line.

    A ⭐ prefix is the agent's only signal that it found a new, relevant incident. If a run
    produced none, the harness rejects it and retries (up to MAX_VALIDATOR_RETRIES).
    """
    return "⭐" in output


def on_code_execute(code: str, on_search_callback=None) -> bool:
    """Code-execute hook: inspect a generated code block before it runs.

    When the code calls aiid_search(),
    we fire `on_search_callback` so explorer/loop.py can yield a `search` event.
    Returns whether the code performed a search (used in tests).
    """
    is_search = "aiid_search(" in code
    if is_search and on_search_callback is not None:
        on_search_callback(code)
    return is_search


# ── Harness: drive one round of the outer agent ───────────────────────────────

def _extract_printed(content) -> str:
    """Pull the text the agent printed (or returned) from a run_code tool result.

    CodeMode returns either the last expression's value directly, or, when the code also
    printed, a dict like {"output": "<printed text>", "result": <value>}. The agent prints
    its HISTORICAL_RESULTS marker block, so the text we care about is usually under "output".
    """
    if isinstance(content, dict):
        text = str(content.get("output", ""))
        result = content.get("result")
        if isinstance(result, str):
            text = f"{text}\n{result}" if text else result
        return text
    if isinstance(content, str):
        return content
    return ""


async def _run_one_pass(prompt: str, on_search_callback, stop_after: int) -> str:
    """Run the outer agent once through CodeMode, returning its combined text output.

    We use `agent.iter()` to step through the agent graph node by node so we can:
      * count how many run_code blocks have executed and stop after `stop_after` of them,
      * detect aiid_search() calls and fire the search callback,
      * collect the text each code block printed (the marker blocks loop.py re-injects).

    The graph alternates: a CallToolsNode carries the model's next run_code call (not yet
    executed), and the following ModelRequestNode carries that block's executed result.
    """
    captured_outputs: list[str] = []  # printed text from each executed code block
    code_exec_count = 0  # how many code blocks have run so far (the stopper's budget)
    stop = False

    with _trace("outer_agent"):
        async with _outer_agent.iter(prompt) as run:
            async for node in run:
                # Results of the PREVIOUS code block come back attached to a model request.
                if Agent.is_model_request_node(node):
                    for part in node.request.parts:
                        if isinstance(part, ToolReturnPart) and part.tool_name == _RUN_CODE_TOOL:
                            captured_outputs.append(_extract_printed(part.content))

                # The model's NEXT code block is on a CallToolsNode (it has not run yet).
                elif Agent.is_call_tools_node(node):
                    for part in node.model_response.parts:
                        if isinstance(part, ToolCallPart) and part.tool_name == _RUN_CODE_TOOL:
                            # Enforce the stop budget BEFORE letting this block run, so we
                            # execute exactly `stop_after` blocks and no more.
                            if stop_after_n_calls(code_exec_count, stop_after):
                                stop = True
                                break
                            code = part.args_as_dict().get("code", "")
                            code_exec_count += 1
                            on_code_execute(code, on_search_callback)
                    if stop:
                        break  # halt the run early; further code blocks are abandoned

        # If the run finished on its own (we did not break), include the final text answer.
        final_text = run.result.output if run.result is not None else ""

    combined = "\n".join(o for o in captured_outputs if o)
    if final_text:
        combined = f"{combined}\n{final_text}" if combined else final_text
    return combined


async def _run_harness_async(
    user_message: str,
    on_search_callback,
    stop_after: int,
    max_validator_retries: int,
) -> str:
    """Run the outer agent, retrying while the validator rejects the output.

    Each attempt is a fresh pass. If the pass produced no ⭐ lines, we append the
    "try harder" nudge and try again, up to `max_validator_retries` times. After that we
    give up on this round *gracefully* — returning the last output instead of raising — so
    the outer loop in loop.py can move on to the next round.
    """
    prompt = user_message
    output = ""
    for _attempt in range(max_validator_retries):
        output = await _run_one_pass(prompt, on_search_callback, stop_after)
        if has_new_incidents(output):
            return output
        # Rejected: no new incidents. Nudge the agent and retry with a fresh pass.
        prompt = f"{user_message}\n\n{_TRY_HARDER_MESSAGE}"
    return output


def run_harness(
    user_message: str,
    on_search_callback=None,
    *,
    stop_after: int | None = None,
    max_validator_retries: int | None = None,
) -> str:
    """Run one round of the outer agent and return its accumulated text output.

    `user_message` is the task for this round (already including any re-injected
    historical_results). `on_search_callback(code)` is called each time the generated code
    calls aiid_search() — loop.py uses it to yield `search` events.

    `stop_after` / `max_validator_retries` default to the configured budgets but can be
    overridden (mainly so tests can keep runs short). Returns text containing the agent's
    HISTORICAL_RESULTS marker block for loop.py to parse.
    """
    if stop_after is None:
        stop_after = settings.STOP_AFTER_N_CALLS
    if max_validator_retries is None:
        max_validator_retries = settings.MAX_VALIDATOR_RETRIES

    # asyncio.run spins up a fresh event loop for this round's async iteration. Inner
    # llm_query() calls use run_sync inside the sandbox, which works fine within this loop.
    return asyncio.run(
        _run_harness_async(user_message, on_search_callback, stop_after, max_validator_retries)
    )
