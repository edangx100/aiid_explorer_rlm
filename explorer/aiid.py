# AIID GraphQL data source
# aiid_search() issues a GraphQL POST to incidentdatabase.ai and returns raw incident records.
# Includes an in-memory response cache keyed on query string to avoid duplicate API calls.
# Returns [] (not an exception) when the AIID API is unreachable.

import httpx
from explorer.config import settings

AIID_URL = "https://incidentdatabase.ai/api/graphql"

# HTTP headers that make our request look like a real browser. The AIID API sits behind
# bot protection that rejects non-browser clients, and it checks TWO things:
#   1. Origin — must be the site's own address, or it returns 403 "Forbidden - Invalid origin".
#   2. User-Agent — a non-browser UA (e.g. curl, or python-httpx from a datacenter IP like
#      Hugging Face Spaces) is refused with "Forbidden - Invalid client / API access is
#      restricted to web browsers". A browser UA passes the check. This matters in production:
#      the default httpx UA works from a residential IP but is blocked from Spaces' server IPs,
#      which silently emptied every search there. Referer is added to look more browser-like.
_HEADERS = {
    "Origin": "https://incidentdatabase.ai",
    "Referer": "https://incidentdatabase.ai/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# OR across title and description so a query term matches either field, case-insensitively
_GQL = """
query($query: String!, $limit: Int!) {
  incidents(
    filter: {
      OR: [
        { title:       { REGEX: $query, OPTIONS: "i" } }
        { description: { REGEX: $query, OPTIONS: "i" } }
      ]
    }
    pagination: { limit: $limit }
    sort: { incident_id: DESC }
  ) {
    incident_id
    title
    description
    date
  }
}
"""

# Populated on first call for a given query string; reused for the rest of the run
_cache: dict[str, list[dict]] = {}


def aiid_search(query: str) -> list[dict]:
    if query in _cache:
        return _cache[query]

    try:
        resp = httpx.post(
            AIID_URL,
            json={
                "query": _GQL,
                "variables": {"query": query, "limit": settings.AIID_RESULTS_LIMIT},
            },
            headers=_HEADERS,  # attach the Origin header so the server accepts the request
            timeout=10,
        )
        resp.raise_for_status()
        incidents = resp.json().get("data", {}).get("incidents") or []
    except Exception:
        # Covers ConnectError, TimeoutException, HTTPStatusError, and malformed responses;
        # callers must not see network failures — they get an empty list instead.
        return []

    _cache[query] = incidents
    return incidents
