# Parses historical_results marker blocks into DataFrames and back.
# parse_to_df() extracts only ⭐ lines — non-starred lines are silently dropped.
# atlas_techniques is exploded to one row per technique; incident_id is the join key.
# df_to_incidents() collapses exploded rows back to AIIncident objects for ExplorerResponse.
# incidents_to_df() produces a display DataFrame with techniques as a comma-separated string.

import pandas as pd
from explorer.models import AIIncident, ATLASTechnique
from explorer.prompts import HISTORICAL_RESULTS_END, HISTORICAL_RESULTS_START

# These are the column names used in the exploded DataFrame throughout this module.
# Keeping them as constants avoids typos when filtering or grouping.
_COLS = [
    "incident_id",
    "title",
    "description",       # always "" — not stored in historical_results
    "incident_type",
    "atlas_technique_id",
    "atlas_technique_name",
    "industry",
    "harm_severity",
    "found_with",
]


def parse_to_df(historical_results: str) -> pd.DataFrame:
    """Parse a historical_results block into an exploded DataFrame.

    The input is the text BETWEEN the HISTORICAL_RESULTS_START /
    HISTORICAL_RESULTS_END markers (loop.py strips them before calling us).
    If markers are still present we strip them here so callers that pass the
    raw agent output also work.

    Only ⭐ lines produce rows. Each incident becomes one row per ATLAS
    technique — an incident with two techniques produces two rows with the same
    incident_id. The "found_with" value comes from the most-recent
    "AIID Search:" line that precedes the result block.
    """
    # Strip markers if the caller passed the full agent output instead of the
    # already-extracted inner block (defensive — loop.py strips them itself).
    if HISTORICAL_RESULTS_START in historical_results:
        start = historical_results.find(HISTORICAL_RESULTS_START)
        end = historical_results.find(HISTORICAL_RESULTS_END)
        if start != -1 and end != -1:
            historical_results = historical_results[
                start + len(HISTORICAL_RESULTS_START):end
            ].strip()

    rows = []
    current_found_with = ""  # updated every time we see "AIID Search: ..."

    for line in historical_results.splitlines():
        stripped = line.strip()

        # Track the AIID Search header so every starred line below it gets the
        # right found_with value.
        if stripped.startswith("AIID Search:"):
            current_found_with = stripped[len("AIID Search:"):].strip()
            continue

        # Skip everything that is not a starred (relevant) incident line.
        if "⭐" not in stripped:
            continue

        # ── Parse the starred line ─────────────────────────────────────────
        # Format: "N. ⭐ #<id> <title> -- <type> | <tech_id> <tech_name> | ... | industry: x | severity: y"
        # We locate the ⭐ symbol, then work on the text that follows it.
        star_pos = stripped.find("⭐")
        after_star = stripped[star_pos + 1:].strip()  # "#id title -- ..."

        # The " -- " separator divides the id/title half from the metadata half.
        if " -- " not in after_star:
            continue  # malformed line — skip rather than crash

        id_title_part, metadata_part = after_star.split(" -- ", 1)
        id_title_part = id_title_part.strip()
        metadata_part = metadata_part.strip()

        # id_title_part: "#123 Some Incident Title"
        if id_title_part.startswith("#"):
            id_title_part = id_title_part[1:]  # drop the leading #
        tokens = id_title_part.split(" ", 1)
        if not tokens[0].isdigit():
            continue  # no numeric id — skip
        incident_id = int(tokens[0])
        title = tokens[1].strip() if len(tokens) > 1 else ""

        # metadata_part: "security_attack | AML.T0051 Foo | AML.T0054 Bar | industry: X | severity: Y"
        # Split on | and classify each segment.
        pipe_parts = [p.strip() for p in metadata_part.split("|")]

        if not pipe_parts:
            continue

        # First segment is always the incident type.
        incident_type = pipe_parts[0].strip()
        industry = ""
        severity = ""
        techniques: list[tuple[str, str]] = []  # (technique_id, technique_name)

        # Remaining segments are either "industry: X", "severity: Y", or a technique.
        for part in pipe_parts[1:]:
            part = part.strip()
            if part.startswith("industry:"):
                industry = part[len("industry:"):].strip()
            elif part.startswith("severity:"):
                severity = part[len("severity:"):].strip()
            else:
                # Technique segment: "AML.T0051 LLM Prompt Injection"
                # Split on first space: first token is the ID, rest is the name.
                tech_tokens = part.split(" ", 1)
                tech_id = tech_tokens[0].strip()
                tech_name = tech_tokens[1].strip() if len(tech_tokens) > 1 else ""
                if tech_id:
                    techniques.append((tech_id, tech_name))

        # If no technique segments were found, add a single blank placeholder so
        # the incident still appears in the DataFrame (techniques list must not be empty).
        if not techniques:
            techniques = [("", "")]

        # ── Explode: one row per technique ────────────────────────────────
        for tech_id, tech_name in techniques:
            rows.append({
                "incident_id": incident_id,
                "title": title,
                "description": "",  # historical_results does not store descriptions
                "incident_type": incident_type,
                "atlas_technique_id": tech_id,
                "atlas_technique_name": tech_name,
                "industry": industry,
                "harm_severity": severity,
                "found_with": current_found_with,
            })

    # Return an empty DataFrame with the right columns when nothing was found
    # (e.g. all rounds had no relevant incidents), so callers never get a
    # KeyError when accessing column names.
    if not rows:
        return pd.DataFrame(columns=_COLS)

    return pd.DataFrame(rows)


# ── Query helpers ──────────────────────────────────────────────────────────────
# These operate on the EXPLODED DataFrame (one row per technique).
# Callers can chain them: by_severity(by_incident_type(df, "security_attack"), "critical")

def by_technique(df: pd.DataFrame, technique_id: str) -> pd.DataFrame:
    """Return rows where atlas_technique_id matches exactly."""
    return df[df["atlas_technique_id"] == technique_id]


def by_industry(df: pd.DataFrame, industry: str) -> pd.DataFrame:
    """Return rows where industry matches exactly."""
    return df[df["industry"] == industry]


def by_incident_type(df: pd.DataFrame, incident_type: str) -> pd.DataFrame:
    """Return rows where incident_type matches exactly."""
    return df[df["incident_type"] == incident_type]


def by_severity(df: pd.DataFrame, severity: str) -> pd.DataFrame:
    """Return rows where harm_severity matches exactly."""
    return df[df["harm_severity"] == severity]


# ── Collapse helpers ───────────────────────────────────────────────────────────

def df_to_incidents(df: pd.DataFrame) -> list[AIIncident]:
    """Collapse an exploded DataFrame back into a list of AIIncident objects.

    Each unique incident_id in the DataFrame becomes one AIIncident. Technique
    rows for the same incident are gathered into a list[ATLASTechnique].
    Used by loop.py to build the final ExplorerResponse.
    """
    if df.empty:
        return []

    incidents: list[AIIncident] = []

    # groupby preserves insertion order in pandas >= 1.1 by default (sort=True
    # sorts by incident_id, which is fine for deterministic output).
    for incident_id, group in df.groupby("incident_id"):
        first = group.iloc[0]  # scalar fields are the same for every row in a group

        # Collect ATLAS techniques, de-duplicating by technique_id.
        techniques: list[ATLASTechnique] = []
        seen: set[str] = set()
        for _, row in group.iterrows():
            tech_id = row["atlas_technique_id"]
            if tech_id and tech_id not in seen:
                seen.add(tech_id)
                techniques.append(ATLASTechnique(
                    technique_id=tech_id,
                    name=row["atlas_technique_name"],
                ))

        # AIIncident.atlas_techniques must not be empty — use a blank placeholder
        # only if the DataFrame had no valid technique IDs (shouldn't happen in
        # practice, but guards against parse failures upstream).
        if not techniques:
            techniques = [ATLASTechnique(technique_id="", name="")]

        incidents.append(AIIncident(
            incident_id=int(incident_id),
            title=str(first["title"]),
            description=str(first.get("description", "")),
            incident_type=first["incident_type"],   # type: ignore[arg-type]
            atlas_techniques=techniques,
            industry=str(first["industry"]),
            harm_severity=first["harm_severity"],   # type: ignore[arg-type]
            found_with=str(first["found_with"]),
        ))

    return incidents


# Every AIID incident has a canonical page at /cite/<id> that lists the original
# source reports/articles. The URL is fully determined by the incident_id, so we can
# turn each incident's title into a clickable link without fetching anything extra
# from the API.
AIID_INCIDENT_URL = "https://incidentdatabase.ai/cite/{incident_id}"

# The exact columns incidents_to_df() emits, in order. frontend/app.py reuses this so
# the empty starting table and the populated table always have the same shape. The
# "title" column is itself a clickable link to the incident's source page (see
# _title_link); there is no separate "source" column.
#
# "description" is intentionally NOT shown: the agent records incidents as ⭐ lines whose
# format carries no description, so it would always be blank on a real search. The full
# source text is one click away via the title link.
DISPLAY_COLUMNS = [
    "incident_id", "title", "incident_type",
    "atlas_techniques", "industry", "harm_severity", "found_with",
]


def _title_link(title: str, incident_id: int) -> str:
    """Render an incident's title as a Markdown link to its AIID source page.

    e.g. _title_link("Chatbot gives bad advice", 545) ->
        "[Chatbot gives bad advice ↗](https://incidentdatabase.ai/cite/545)"

    frontend/app.py renders the "title" column as Markdown, so this shows up as the
    clickable title the user follows to read the original source articles. The "]"
    in a title would otherwise break Markdown link syntax, so we escape it.
    """
    url = AIID_INCIDENT_URL.format(incident_id=incident_id)
    safe_title = title.replace("]", "\\]")
    return f"[{safe_title} ↗]({url})"


def incidents_to_df(incidents: list[AIIncident]) -> pd.DataFrame:
    """Produce a display DataFrame with one row per incident.

    ATLAS techniques are collapsed to a comma-separated string, e.g.
    "AML.T0051 LLM Prompt Injection, AML.T0054 LLM Jailbreak". The "title" column is a
    clickable Markdown link to each incident's page on the AI Incident Database, where
    the original source articles are listed. Used by frontend/app.py to populate
    gr.DataFrame.
    """
    if not incidents:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)

    rows = []
    for incident in incidents:
        # Join each technique's id + name with a space, then join all with ", "
        techniques_str = ", ".join(
            f"{t.technique_id} {t.name}".strip()
            for t in incident.atlas_techniques
        )
        rows.append({
            "incident_id": incident.incident_id,
            # Title rendered as a clickable link to the incident's AIID source page.
            "title": _title_link(incident.title, incident.incident_id),
            "incident_type": incident.incident_type,
            "atlas_techniques": techniques_str,
            "industry": incident.industry,
            "harm_severity": incident.harm_severity,
            "found_with": incident.found_with,
        })

    return pd.DataFrame(rows)
