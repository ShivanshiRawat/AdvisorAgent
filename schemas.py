from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

# Output schemas that the LLM terminal tools construct.


class Option(BaseModel):
    id: str
    label: str


class ClarificationQuestion(BaseModel):
    question: str
    anchor: str
    why_asking: str
    options: Optional[List[Option]] = []
    allow_free_form: Optional[bool] = True
    free_form_label: Optional[str] = "Other"


class ClarificationPayload(BaseModel):
    message: str
    questions: List[ClarificationQuestion]


class QueryPatternRecommendation(BaseModel):
    query_pattern: str
    recommended_index: str
    reasoning: str
    eliminated_alternatives: Dict[str, str]
    config: Dict[str, Any]
    caveats: Optional[List[str]] = []


class ArchitectureSummary(BaseModel):
    total_indexes: int
    index_types_used: List[str]
    shared_indexes: str
    operational_notes: str


class RecommendationPayload(BaseModel):
    summary: str
    query_pattern_recommendations: List[QueryPatternRecommendation]
    architecture_summary: ArchitectureSummary


class UnderstandingState(BaseModel):
    """
    Agent's persistent memory across turns.
    Tracks confirmed facts, identified patterns, and open questions.
    """
    narrative_summary: str = ""
    confirmed_facts: Dict[str, Any] = Field(default_factory=dict)
    query_patterns: List[Dict[str, Any]] = Field(default_factory=list)
    open_gaps: List[str] = Field(default_factory=list)
    resolved_gaps: List[str] = Field(default_factory=list)
    reasoning_so_far: str = ""


class ThinkingRecord(BaseModel):
    """Record of the agent's scratchpad thinking."""
    reasoning: str


class PlanRecord(BaseModel):
    """Record of the agent's execution plan."""
    steps: List[Dict[str, str]] = Field(default_factory=list)
