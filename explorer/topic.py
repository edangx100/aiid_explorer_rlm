# Topic steering via LDA.
#
# After the first few rounds the outer loop has accumulated a list of search
# strings it already tried (`past_queries`). Topic steering looks at those and
# asks the inner LLM: "given what's already been searched, what's an unexplored
# direction worth trying next?" The result is a fresh search task that nudges the
# outer agent toward ATLAS technique families or industries it has not covered yet.
#
# Three functions:
#   - summarize()          -> compresses past_queries into a few short "topic" strings.
#   - next_topic()         -> asks the inner LLM for one unexplored direction.
#   - next_topic_prompt()  -> wraps that direction into an actionable search task.
#
# `past_queries: list[str]` maintained by explorer/loop.py

from gensim import corpora
from gensim.models import LdaModel
from gensim.utils import simple_preprocess

from explorer.agents import llm_query


def summarize(user_query: str, past_queries: list[str]) -> str:
    """Compress the queries searched so far, then ask for an unexplored direction.

    Why compress at all? With only a handful of queries we can hand the raw list
    straight to the LLM. But once there are many, dumping them all in is noisy, so
    we first run LDA (a topic-modelling algorithm) to cluster them into a few
    themes and pass those theme strings instead.

    - 0 queries           -> nothing searched yet, so any topic is fair game.
    - 1..10 queries        -> pass the raw query list to next_topic().
    - more than 10 queries -> fit an LDA model and pass its topic strings instead.
    """
    # Nothing searched yet: no history to steer away from, so suggest anything.
    if len(past_queries) == 0:
        return next_topic([f"Try any topic for {user_query}"], user_query)

    # Few queries: the raw list is small enough to feed directly to the LLM.
    if len(past_queries) <= 10:
        return next_topic(past_queries, user_query)

    # Many queries: cluster them into themes with LDA so the LLM sees a compact
    # summary instead of a long, repetitive list.

    # 1. Tokenize each query into a list of lowercase words. simple_preprocess
    #    strips punctuation and very short tokens. docs is a list-of-word-lists.
    docs = [simple_preprocess(q) for q in past_queries]

    # 2. Build a dictionary mapping every word to an integer id, then turn each
    #    query into a bag-of-words: a list of (word_id, count) pairs. This numeric
    #    form is what gensim's LDA model expects.
    dictionary = corpora.Dictionary(docs)
    corpus = [dictionary.doc2bow(doc) for doc in docs]

    # 3. Pick how many topics to look for. Roughly one topic per ten queries
    num_topics = max(1, len(past_queries) // 10)

    # 4. Fit the LDA model. passes=20 lets it iterate enough to settle
    lda = LdaModel(
        corpus=corpus,
        id2word=dictionary,
        num_topics=num_topics,
        passes=20,
        random_state=42,
    )

    # 5. print_topics(-1) returns every topic as an (id, description) pair, where
    #    the description is a weighted-term string like '0.40*"jailbreak" + 0.30*"prompt"'.
    #    We keep just the description strings to hand to the LLM.
    topic_strings = [description for _topic_id, description in lda.print_topics(-1)]
    return next_topic(topic_strings, user_query)


def next_topic(topic_summaries: list[str], user_query: str) -> str:
    """Ask the inner LLM for one unexplored sub-topic worth searching next.

    Given a summary of what's already been explored, the model proposes a new but
    still-relevant direction — ideally an ATLAS technique family or industry sector
    that hasn't shown up yet — phrased as instructions for a searching agent.
    Returns that suggestion as a short steering paragraph.
    """

    prompt = f"""The user is searching AI incident reports for: {user_query}

Below is a summary of the search topics explored so far (as raw queries or as
gensim LDA topic clusters):

{chr(10).join(topic_summaries)}

Suggest ONE different but still-relevant sub-topic to explore next — ideally an
unexplored MITRE ATLAS technique family or an industry sector not yet covered.
Write your answer as clear instructions to a searching agent, not as a list."""
    return llm_query(prompt)


def next_topic_prompt(user_query: str, past_queries: list[str]) -> str:
    """Build the full steering task that replaces the verbatim query in a round.

    Takes the unexplored direction from summarize() and wraps it into an
    actionable search task. The wrapper deliberately repeats the original
    user_query so the agent stays anchored to what the user actually wants, and
    ends with an explicit guardrail against drifting into irrelevant incidents.
    """
    
    summary = summarize(user_query, past_queries)
    return f"""The user is looking for AI incidents about: {user_query}

We've already searched the obvious places. To find more, focus your next searches
on this unexplored direction:

{summary}

Generate AIID search queries around this direction and evaluate every result for
relevance to {user_query}.

Do NOT add incidents not relevant to {user_query}."""
