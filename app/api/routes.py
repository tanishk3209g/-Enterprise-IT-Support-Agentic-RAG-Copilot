from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Header
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.rag.workflow import ask, resume_thread
from app.rag.vectorstore import add_documents
from app.services.audit import write_audit, write_ticket_event
from app.services.ingestion import load_file, chunk_documents, SUPPORTED
from app.services.tickets import list_tickets

router = APIRouter(prefix="/api")
settings = get_settings()


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)


class ApproveRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    approved: bool
    edits: dict | None = None


@router.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}


@router.post("/chat")
def chat(payload: ChatRequest):
    try:
        result = ask(payload.question)
        if result.get("status") == "needs_approval":
            write_ticket_event("approval_pending", payload.question, result.get("trace", []), {"thread_id": result.get("thread_id")})
            return {
                "status": "needs_approval",
                "thread_id": result.get("thread_id"),
                "ticket_draft": result.get("ticket_draft", {}),
                "trace": result.get("trace", []),
            }

        write_audit(payload.question, result.get("source_used", "answer"), result.get("trace", []))
        return {
            "answer": result["answer"],
            "source_used": result["source_used"],
            "trace": result.get("trace", []),
            "citations": result.get("citations", []),
            "rewritten_query": result.get("current_query", payload.question),
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/approve")
def approve(payload: ApproveRequest):
    resume = {"approved": payload.approved}
    if payload.edits:
        resume["ticket_draft"] = payload.edits
    result = resume_thread(payload.thread_id, resume)
    if result.get("approval_status") == "rejected":
        write_ticket_event("approval_rejected", "Ticket approval decision", result.get("trace", []), {"thread_id": payload.thread_id})
        return {
            "status": "cancelled",
            "answer": result.get("answer", "Ticket creation was cancelled."),
            "source_used": result.get("source_used", "ticket_cancelled"),
            "trace": result.get("trace", []),
            "ticket_id": None,
            "verification": result.get("verification", {}),
        }

    write_ticket_event(
        "approval_approved" if payload.approved else "approval_rejected",
        "Ticket approval decision",
        result.get("trace", []),
        {"thread_id": payload.thread_id, "approved": payload.approved},
    )
    if result.get("verification"):
        write_ticket_event("verification_result", "Ticket verification", result.get("trace", []), result.get("verification", {}))
    if result.get("ticket_id"):
        write_ticket_event("ticket_created", f"Ticket #{result['ticket_id']} created", result.get("trace", []), {"ticket_id": result.get("ticket_id")})

    return {
        "status": "completed",
        "answer": result.get("answer", ""),
        "source_used": result.get("source_used", "ticket_created"),
        "ticket_id": result.get("ticket_id"),
        "verification": result.get("verification", {}),
        "trace": result.get("trace", []),
    }


@router.get("/tickets")
def tickets():
    return {"tickets": list_tickets()}


@router.post("/ingest")
async def ingest(file: UploadFile = File(...), x_admin_key: str = Header(default="")):
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(status_code=400, detail=f"Supported: {', '.join(sorted(SUPPORTED))}")
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename).name
    dest.write_bytes(await file.read())
    docs = load_file(dest)
    chunks = chunk_documents(docs)
    ids = add_documents(chunks)
    return {"message": "Document indexed", "file": dest.name, "chunks": len(chunks), "ids_created": len(ids)}
