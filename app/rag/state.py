from typing import List, Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field
from langchain_core.documents import Document

class RouteDecision(BaseModel):
    route: Literal["kb", "direct"] = Field(description="kb for IT/support questions; direct for greetings/simple chat")
    intent: Literal["answer", "create_ticket"] = Field(description="Whether the request should answer a question or create a support ticket")

class EvidenceGrade(BaseModel):
    grade: Literal["good", "weak"] = Field(description="Whether evidence is sufficient to answer")

class TicketDraft(BaseModel):
    title: str = Field(description="Short ticket title")
    category: Literal["network", "identity", "hardware", "software", "email", "security", "other"] = Field(description="Ticket category")
    priority: Literal["low", "medium", "high"] = Field(description="Urgency level")
    description: str = Field(description="Ticket description, including relevant KB evidence and user context")

class AgentState(TypedDict):
    question: str
    current_query: str
    kb_docs: List[Document]
    web_results: str
    kb_grade: str
    web_grade: str
    answer: str
    source_used: str
    retry_count: int
    trace: List[str]
    citations: List[dict]
    intent: str
    ticket_draft: dict
    approval_status: str
    ticket_id: str
    verification: dict
    action_retry_count: int
