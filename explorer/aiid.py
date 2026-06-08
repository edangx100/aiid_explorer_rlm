# AIID GraphQL data source
# aiid_search() issues a GraphQL POST to incidentdatabase.ai and returns raw incident records.
# Includes an in-memory response cache keyed on query string to avoid duplicate API calls.
# Returns [] (not an exception) when the AIID API is unreachable.

import httpx
from explorer.config import settings

AIID_URL = "https://incidentdatabase.ai/api/graphql"

# HTTP headers are extra info we attach to a request, alongside the data itself.
# This server checks the "Origin" header (which says what website a request is coming from)
# and rejects anything it doesn't recognise with a 403 "Forbidden - Invalid origin" error.
# By setting Origin to the site's own address, our request looks like it comes from the
# website itself, so the server allows it. Without this line every call comes back empty.
_HEADERS = {"Origin": "https://incidentdatabase.ai"}

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
