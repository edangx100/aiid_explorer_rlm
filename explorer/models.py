from pydantic import BaseModel
from typing import Literal


# Represents a single MITRE ATLAS adversarial ML technique, e.g. AML.T0051 (LLM Prompt Injection).
class ATLASTechnique(BaseModel):
    technique_id: str  # ATLAS identifier, e.g. "AML.T0051"
    name: str          # Human-readable technique name


# A single AI incident record extracted from the AIID database and classified by the agent.
# Literals on incident_type and harm_severity enforce the closed vocabulary used in historical_results formatting.
class AIIncident(BaseModel):
    incident_id: int
    title: str
    description: str
    incident_type: Literal["security_attack", "safety_failure", "reliability_issue"]
    atlas_techniques: list[ATLASTechnique]  # one or more ATLAS techniques; never empty after classification
    industry: str
    harm_severity: Literal["critical", "high", "medium", "low"]
    found_with: str  # short AIID search query that surfaced this incident; used to track past_queries in the loop


# Final output of a completed run: the flat incident list plus loop-level metadata.
class ExplorerResponse(BaseModel):
    incidents: list[AIIncident]
    total: int       # len(incidents); stored explicitly so callers don't need to recompute
    rounds_run: int  # number of outer-loop iterations that completed
