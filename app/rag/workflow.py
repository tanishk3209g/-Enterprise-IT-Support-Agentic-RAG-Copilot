import logging
import uuid
from typing import Literal

from duckduckgo_search import DDGS
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

from app.core.config import get_settings
from app.rag.state import AgentState, RouteDecision, EvidenceGrade, TicketDraft
from app.rag.vectorstore import get_retriever
from app.services.tickets import create_ticket, get_ticket

logger = logging.getLogger(__name__)
settings = get_settings()

_llm = None


def llm():
    """Initializes and returns the configured open-source friendly LLM (Groq or Gemini)."""
    global _llm
    if _llm is None:
        provider = (settings.llm_provider or "groq").lower().strip()
        if provider == "gemini":
            if not settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY is not set. Please set it in your .env file or switch LLM_PROVIDER to groq.")
            from langchain_google_genai import ChatGoogleGenerativeAI
            _llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model,
                google_api_key=settings.gemini_api_key,
                temperature=0,
            )
        else:
            if not settings.groq_api_key:
                raise RuntimeError("GROQ_API_KEY is not set. Please set it in your .env file.")
            from langchain_groq import ChatGroq
            _llm = ChatGroq(
                model=settings.groq_model,
                groq_api_key=settings.groq_api_key,
                temperature=0,
            )
    return _llm


def needs_ticket_intent(question: str) -> bool:
    """Keyword heuristic for identifying explicit or operational IT issues that need a ticket instead of a direct answer."""
    text = (question or "").lower()
    ticket_markers = [
        "open a ticket",
        "raise a ticket",
        "create a ticket",
        "submit a ticket",
        "contact the help desk",
        "needs it help",
        "need support",
        "cannot login",
        "vpn is failing",
        "mfa not working",
        "password reset",
        "laptop issue",
        "computer issue",
        "outlook not working",
        "email issue",
        "can't access",
        "not working",
    ]
    return any(marker in text for marker in ticket_markers)


def add_trace(state: AgentState, message: str):
    return [*state.get("trace", []), message]


def route_question(state: AgentState):
    router = llm().with_structured_output(RouteDecision)
    decision = router.invoke(f"""
You route messages for an enterprise IT support assistant.
Use kb for questions about company IT policies, VPN, password reset, MFA, laptop setup,
software access, security, email, devices, troubleshooting, or technology support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
If the request explicitly asks to open, raise, or submit an IT support ticket, choose create_ticket.
Question: {state['question']}
""")
    route_val = getattr(decision, "route", "kb") if decision else "kb"
    intent_val = getattr(decision, "intent", "answer") if decision else "answer"
    if needs_ticket_intent(state["question"]):
        intent_val = "create_ticket"
    if intent_val == "create_ticket":
        return {
            "source_used": "ticket_intent",
            "intent": "create_ticket",
            "trace": add_trace(state, "Router → CREATE_TICKET"),
        }
    return {
        "source_used": route_val,
        "intent": "answer",
        "trace": add_trace(state, f"Router → {route_val.upper()}"),
    }


def route_after_router(state: AgentState) -> Literal["draft_ticket", "retrieve_kb", "direct_answer"]:
    if state.get("intent") == "create_ticket":
        return "draft_ticket"
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


def retrieve_kb(state: AgentState):
    q = (state.get("current_query") or state.get("question") or "").strip()
    if not q:
        q = state.get("question", "IT Support").strip()
    docs = get_retriever().invoke(q)
    return {"kb_docs": docs, "trace": add_trace(state, f"Private KB retrieval → {len(docs)} chunks")}


def grade_kb(state: AgentState):
    grader = llm().with_structured_output(EvidenceGrade)
    context = "\n\n".join(f"Source: {d.metadata.get('source','unknown')}\n{d.page_content}" for d in state["kb_docs"])
    grade = grader.invoke(f"""
You grade evidence for an enterprise IT support assistant.
Question: {state['question']}
Private company KB evidence:
{context}

Return good only if the evidence is sufficient to answer confidently and specifically.
Otherwise return weak.
""")
    grade_val = getattr(grade, "grade", "weak") if grade else "weak"
    return {"kb_grade": grade_val, "trace": add_trace(state, f"KB evidence grade → {grade_val.upper()}")}


def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


def search_web(state: AgentState):
    """Searches DuckDuckGo without requiring any API key or subscription."""
    query = (state.get("current_query") or state.get("question") or "").strip()
    lines, citations = [], []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            for item in results:
                title = item.get("title", "")
                url = item.get("href", "")
                snippet = item.get("body", "")
                lines.append(f"Title: {title}\nURL: {url}\nSnippet: {snippet}")
                citations.append({"title": title or url, "url": url, "type": "web"})
    except Exception as exc:
        logger.warning(f"DuckDuckGo search error: {exc}")
        lines.append(f"Search error: {exc}")

    return {
        "web_results": "\n\n".join(lines) if lines else "No relevant web search results found.",
        "citations": citations,
        "source_used": "web",
        "trace": add_trace(state, "Web fallback → DuckDuckGo search"),
    }


def grade_web(state: AgentState):
    grader = llm().with_structured_output(EvidenceGrade)
    grade = grader.invoke(f"""
Question: {state['question']}
Web evidence:
{state['web_results']}

Return good if the evidence is sufficient and directly relevant; otherwise weak.
""")
    grade_val = getattr(grade, "grade", "weak") if grade else "weak"
    return {"web_grade": grade_val, "trace": add_trace(state, f"Web evidence grade → {grade_val.upper()}")}


def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < settings.max_retries:
        return "rewrite_query"
    return "insufficient"


def rewrite_query(state: AgentState):
    rewritten = llm().invoke(f"""
Rewrite this IT support question for better private knowledge retrieval and vendor web search.
Preserve intent, add useful technical keywords, do not answer, return only the query.
Question: {state['question']}
""").content.strip()
    clean_query = rewritten.strip('"`\'\n ') or state['question']
    return {
        "current_query": clean_query,
        "retry_count": state["retry_count"] + 1,
        "trace": add_trace(state, f"Query rewrite → {clean_query}"),
    }


def generate_from_kb(state: AgentState):
    context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
    answer = llm().invoke(f"""
You are an enterprise IT support copilot. Answer ONLY from the private company KB below.
Be concise, actionable, and safe. If steps are present, present them clearly.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
Question: {state['question']}\n\nPrivate KB:\n{context}
""").content
    citations = []
    seen = set()
    for d in state["kb_docs"]:
        src = d.metadata.get("source", "Private KB")
        if src not in seen:
            seen.add(src)
            citations.append({"title": src.split("/")[-1], "url": "", "type": "private_kb"})
    return {"answer": answer, "source_used": "private_kb", "citations": citations, "trace": add_trace(state, "Answer generation → PRIVATE KB")}


def generate_from_web(state: AgentState):
    answer = llm().invoke(f"""
You are an enterprise IT support copilot. The private company KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external web information and may need IT validation before changing company-managed systems.
Question: {state['question']}\n\nWeb evidence:\n{state['web_results']}
""").content
    return {"answer": answer, "source_used": "web_search", "trace": add_trace(state, "Answer generation → WEB SEARCH")}


def direct_answer(state: AgentState):
    answer = llm().invoke(f"Respond briefly and naturally to: {state['question']}").content
    return {"answer": answer, "source_used": "direct", "trace": add_trace(state, "Direct response → no retrieval")}


def insufficient(state: AgentState):
    if needs_ticket_intent(state["question"]):
        return {
            "answer": "I couldn't find enough reliable evidence to answer confidently. I can draft an IT ticket for you.",
            "intent": "create_ticket",
            "source_used": "insufficient_evidence",
            "trace": add_trace(state, "Stopped → insufficient reliable evidence; ticket path offered"),
        }
    return {
        "answer": "I couldn't find enough reliable evidence in the company knowledge base or external search to answer confidently. Please contact the IT help desk or provide more details.",
        "source_used": "insufficient_evidence",
        "trace": add_trace(state, "Stopped → insufficient reliable evidence"),
    }


def after_insufficient(state: AgentState) -> Literal["draft_ticket", "end"]:
    return "draft_ticket" if state.get("intent") == "create_ticket" else END


def draft_ticket(state: AgentState):
    """Create the first-pass ticket draft from the issue and relevant KB context so a human can approve it before creation."""
    question = state["question"]
    kb_context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}\n{d.page_content}]" for d in state.get("kb_docs", []))
    prompt = f"""
You are drafting an internal IT support ticket.
Use the user's issue, and if relevant, include the strongest KB findings in the description so the support team can act quickly.
Question: {question}
KB evidence:
{kb_context or 'No private KB evidence was available.'}

Return a concise but complete ticket draft.
"""
    draft = llm().with_structured_output(TicketDraft).invoke(prompt)
    if draft is None:
        draft = TicketDraft(
            title=(question[:80] or "IT support request"),
            category="other",
            priority="medium",
            description=question,
        )
    draft_data = draft.model_dump() if hasattr(draft, "model_dump") else dict(draft)
    return {
        "intent": "create_ticket",
        "ticket_draft": draft_data,
        "approval_status": "pending",
        "trace": add_trace(state, f"Ticket draft created → {draft_data.get('title', 'Support request')}"),
    }


def await_approval(state: AgentState, resume_payload: dict | None = None):
    """Pause the workflow for human approval while preserving the draft and checkpoint state."""
    if resume_payload is not None:
        approved = bool(resume_payload.get("approved", False))
        draft = resume_payload.get("ticket_draft") or state.get("ticket_draft", {})
        if approved:
            return {
                "ticket_draft": draft,
                "approval_status": "approved",
                "ticket_id": "",
                "verification": {},
                "trace": add_trace(state, "Approval → APPROVED"),
            }
        return {
            "ticket_draft": draft,
            "approval_status": "rejected",
            "ticket_id": "",
            "verification": {},
            "trace": add_trace(state, "Approval → REJECTED"),
        }

    decision = interrupt({
        "ticket_draft": state.get("ticket_draft", {}),
        "message": "Review this draft and approve or reject it before creating a support ticket.",
    })
    if isinstance(decision, dict):
        approved = bool(decision.get("approved", False))
        if decision.get("draft"):
            decision = {**decision["draft"], "approved": approved}
        if decision.get("ticket_draft"):
            draft = decision["ticket_draft"]
        else:
            draft = state.get("ticket_draft", {})
        if approved:
            return {
                "ticket_draft": draft,
                "approval_status": "approved",
                "ticket_id": "",
                "verification": {},
                "trace": add_trace(state, "Approval → APPROVED"),
            }
        return {
            "ticket_draft": draft,
            "approval_status": "rejected",
            "ticket_id": "",
            "verification": {},
            "trace": add_trace(state, "Approval → REJECTED"),
        }
    return {"approval_status": "pending", "ticket_id": "", "verification": {}, "trace": add_trace(state, "Waiting for approval")}

def after_approval(state: AgentState) -> Literal["execute_ticket", "rejected"]:
    return "execute_ticket" if state.get("approval_status") == "approved" else "rejected"


def rejected(state: AgentState):
    return {
        "answer": "Ticket creation was cancelled. Nothing was created.",
        "source_used": "ticket_cancelled",
        "approval_status": "rejected",
        "trace": add_trace(state, "Stopped → ticket cancelled"),
    }


def execute_ticket(state: AgentState):
    """Create the ticket through the sandbox tool, retrying once on transient failure without pretending it succeeded."""
    draft = state.get("ticket_draft", {}) or {}
    title = draft.get("title") or state["question"]
    category = draft.get("category") or "other"
    priority = draft.get("priority") or "medium"
    description = draft.get("description") or state["question"]

    last_error = None
    for attempt in range(1, 3):
        try:
            ticket_id = create_ticket(title=title, category=category, priority=priority, description=description)
            return {
                "ticket_id": str(ticket_id),
                "verification": {},
                "trace": add_trace(state, f"Ticket execution → created #{ticket_id}"),
            }
        except Exception as exc:  # pragma: no cover - exercised by retry tests via monkeypatch
            last_error = exc
            if attempt == 1:
                state = {**state, "action_retry_count": 1, "trace": add_trace(state, f"Ticket execution failed ({exc}); retrying once")}
                continue
            return {
                "ticket_id": "",
                "action_retry_count": 1,
                "trace": add_trace(state, f"Ticket execution failed after retry ({last_error})"),
            }
    return {
        "ticket_id": "",
        "trace": add_trace(state, f"Ticket execution failed after retry ({last_error})"),
    }


def after_execute(state: AgentState) -> Literal["verify_ticket", "action_failed"]:
    return "verify_ticket" if state.get("ticket_id") else "action_failed"


def verify_ticket_record(ticket_draft: dict, ticket_id: str) -> dict:
    """Verify by reading the stored SQLite record back, not by trusting the create result."""
    stored = get_ticket(int(ticket_id)) if str(ticket_id).strip() else None
    if stored is None:
        return {
            "passed": False,
            "checks": [{"field": "ticket_exists", "passed": False, "expected": True, "actual": None}],
            "evidence": {"ticket_id": ticket_id, "stored": None, "draft": ticket_draft},
        }

    checks = []
    for field_name in ["title", "category", "priority"]:
        expected = ticket_draft.get(field_name)
        actual = stored.get(field_name)
        ok = expected == actual
        checks.append({"field": field_name, "passed": ok, "expected": expected, "actual": actual})

    expected_description = (ticket_draft.get("description") or "").strip()
    actual_description = (stored.get("description") or "").strip()
    description_ok = expected_description == actual_description or expected_description in actual_description or actual_description in expected_description
    checks.append({
        "field": "description",
        "passed": description_ok,
        "expected": expected_description,
        "actual": actual_description,
    })
    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "evidence": {
            "ticket_id": ticket_id,
            "stored": {
                "id": stored.get("id"),
                "title": stored.get("title"),
                "category": stored.get("category"),
                "priority": stored.get("priority"),
                "description": stored.get("description"),
            },
            "draft": ticket_draft,
        },
    }


def verify_ticket(state: AgentState):
    ticket_id = state.get("ticket_id") or ""
    verification = verify_ticket_record(state.get("ticket_draft", {}), ticket_id)
    return {
        "verification": verification,
        "trace": add_trace(state, "Verification → PASS" if verification["passed"] else "Verification → FAIL"),
    }


def after_verify(state: AgentState) -> Literal["summarize_action", "execute_ticket", "action_failed"]:
    verification = state.get("verification") or {}
    if verification.get("passed"):
        return "summarize_action"
    if state.get("action_retry_count", 0) < 1:
        return "execute_ticket"
    return "action_failed"


def summarize_action(state: AgentState):
    verification = state.get("verification") or {}
    ticket_id = state.get("ticket_id")
    ticket_draft = state.get("ticket_draft") or {}
    checks = verification.get("checks", [])
    pass_count = sum(1 for item in checks if item.get("passed"))
    if ticket_id and verification.get("passed"):
        message = (
            f"Ticket #{ticket_id} was created and verified. "
            f"Title: {ticket_draft.get('title', 'Support request')} | "
            f"Category: {ticket_draft.get('category', 'other')} | "
            f"Priority: {ticket_draft.get('priority', 'medium')} | "
            f"Verified checks: {pass_count}/{len(checks)}"
        )
        return {
            "answer": message,
            "source_used": "ticket_created",
            "trace": add_trace(state, "Action summary → verified ticket"),
        }
    return {
        "answer": "I could not complete the IT ticket creation reliably. The ticket was not verified as created, so no success was recorded.",
        "source_used": "ticket_failed",
        "trace": add_trace(state, "Action summary → ticket not verified"),
    }


def action_failed(state: AgentState):
    return {
        "answer": "The ticket workflow failed and no ticket was created. I did not claim a successful ticket action.",
        "source_used": "ticket_failed",
        "trace": add_trace(state, "Action failed → no ticket created"),
    }


def build_graph():
    graph = StateGraph(AgentState)
    for name, fn in {
        "route_question": route_question,
        "draft_ticket": draft_ticket,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb,
        "search_web": search_web,
        "grade_web": grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": direct_answer,
        "insufficient": insufficient,
        "await_approval": await_approval,
        "execute_ticket": execute_ticket,
        "verify_ticket": verify_ticket,
        "summarize_action": summarize_action,
        "action_failed": action_failed,
        "rejected": rejected,
    }.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "route_question")
    graph.add_conditional_edges("route_question", route_after_router, {
        "draft_ticket": "draft_ticket",
        "retrieve_kb": "retrieve_kb",
        "direct_answer": "direct_answer",
    })
    graph.add_edge("retrieve_kb", "grade_kb")
    graph.add_conditional_edges("grade_kb", after_kb, {
        "generate_from_kb": "generate_from_kb",
        "search_web": "search_web",
    })
    graph.add_edge("search_web", "grade_web")
    graph.add_conditional_edges("grade_web", after_web, {
        "generate_from_web": "generate_from_web",
        "rewrite_query": "rewrite_query",
        "insufficient": "insufficient",
    })
    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_conditional_edges("insufficient", after_insufficient, {
        "draft_ticket": "draft_ticket",
        "end": END,
    })
    graph.add_edge("draft_ticket", "await_approval")
    graph.add_conditional_edges("await_approval", after_approval, {
        "execute_ticket": "execute_ticket",
        "rejected": "rejected",
    })
    graph.add_conditional_edges("execute_ticket", after_execute, {
        "verify_ticket": "verify_ticket",
        "action_failed": "action_failed",
    })
    graph.add_conditional_edges("verify_ticket", after_verify, {
        "summarize_action": "summarize_action",
        "execute_ticket": "execute_ticket",
        "action_failed": "action_failed",
    })
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("summarize_action", END)
    graph.add_edge("action_failed", END)
    graph.add_edge("rejected", END)
    return graph.compile(checkpointer=MemorySaver())


agent_graph = build_graph()


def ask(question: str, thread_id: str | None = None):
    """Run a new conversation or resume from the stored checkpoint, surfacing approval pauses cleanly to the API layer."""
    config = {"configurable": {"thread_id": thread_id or uuid.uuid4().hex}}
    initial: AgentState = {
        "question": question,
        "current_query": question,
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "intent": "answer",
        "ticket_draft": {},
        "approval_status": "",
        "ticket_id": "",
        "verification": {},
        "action_retry_count": 0,
    }
    result = agent_graph.invoke(initial, config=config)
    if "__interrupt__" in result:
        interrupt_value = result["__interrupt__"][0].value if isinstance(result["__interrupt__"], list) else result["__interrupt__"]
        draft = interrupt_value.get("ticket_draft") if isinstance(interrupt_value, dict) else {}
        result["thread_id"] = config["configurable"]["thread_id"]
        result["status"] = "needs_approval"
        result["ticket_draft"] = draft
        result["trace"] = result.get("trace", [])
        return result
    result["thread_id"] = config["configurable"]["thread_id"]
    return result


def resume_thread(thread_id: str, resume_payload: dict):
    """Resume a thread after human approval or rejection using LangGraph Command checkpointing."""
    config = {"configurable": {"thread_id": thread_id}}
    result = agent_graph.invoke(Command(resume=resume_payload), config=config)
    result["thread_id"] = thread_id
    return result
