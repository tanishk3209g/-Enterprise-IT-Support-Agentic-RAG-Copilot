# Enterprise IT Support Agentic RAG Copilot

This project is an autonomous task worker for an enterprise IT support workflow: it answers questions from a private KB, falls back to web evidence when needed, and can complete a real task by drafting and creating an IT support ticket with human approval and verification.

It is intentionally small, readable, and built around a LangGraph workflow so the decision path remains visible in the UI and in the trace log.

## Architecture

```text
                  ┌──────────────────────────────┐
                  │ Employee / Helpdesk user     │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                    ┌────────────────────────────┐
                    │ Web UI (HTML + vanilla JS)│
                    └──────────────┬─────────────┘
                                   │ REST API
                                   ▼
                    ┌────────────────────────────┐
                    │ FastAPI routes / audit     │
                    │  /api/chat /api/approve    │
                    │  /api/tickets              │
                    └──────────────┬─────────────┘
                                   │
                                   ▼
                    ┌────────────────────────────┐
                    │ LangGraph state machine    │
                    │  route -> KB/web -> grade  │
                    │  -> answer OR draft ticket │
                    │  -> approval interrupt     │
                    │  -> execute -> verify      │
                    └───────┬────────────────────┘
                            │
                ┌───────────┴────────────┐
                │                        │
                ▼                        ▼
      ┌─────────────────┐      ┌──────────────────────┐
      │ Private KB      │      │ SQLite sandbox       │
      │ ChromaDB        │      │ data/tickets.db      │
      └────────┬────────┘      └──────────┬───────────┘
               │                         │
               ▼                         ▼
      ┌─────────────────┐      ┌──────────────────────┐
      │ DuckDuckGo      │      │ Ticket verification  │
      │ fallback search │      │ reads record back    │
      └─────────────────┘      └──────────────────────┘
```

## Key design decisions

- Grade before generate: evidence is scored as good or weak before the model writes an answer, which keeps the answer grounded and reduces hallucination.
- Interrupt-based approval: the ticket drafting step pauses in LangGraph with a checkpoint and resumes only after a human approves or rejects the draft.
- Verify by reading back: the workflow does not trust the successful create call. It reads the ticket back from SQLite and compares the stored content with the approved draft fields before claiming success.
- Sandbox SQLite ticket system: ticket creation is deliberately local and constrained to a SQLite database rather than a live enterprise ticketing backend, which keeps the demo reliable and auditable.

## Setup and run instructions

1. Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables in a local `.env` file if you plan to call the LLM providers. The app can start without a live provider for the health endpoint, but answer generation and ticket drafting require a valid provider configuration.

Example:

```bash
GROQ_API_KEY=your_key_here
LLM_PROVIDER=groq
ADMIN_API_KEY=change-me
SIMULATE_TICKET_FAILURE_RATE=0
```

4. Start the app:

```bash
uvicorn app.main:app --reload
```

5. Open the UI at http://localhost:8000/ and use the demo flow.

## Demo script

### Scenario 1: KB answer
- Ask: “How do I connect to the company VPN from home?”
- The workflow routes to private KB retrieval, grades the evidence, and returns the answer with citations.

### Scenario 2: Web fallback
- Ask: “What is the latest Microsoft Teams outage guidance?”
- If the private KB is insufficient, the workflow performs a DuckDuckGo fallback and clearly labels the result as web-based rather than internal policy.

### Scenario 3: Ticket creation with approval and verification
- Ask: “Please open a ticket for the VPN issue.”
- The assistant drafts a ticket, pauses for approval, then resumes after the user approves.
- The workflow creates the record in SQLite, reads it back, checks the title/category/priority, and then shows the verified result.

### Fault injection demo
- Set `SIMULATE_TICKET_FAILURE_RATE=0.5` and retry the ticket flow.
- The first ticket creation attempt fails, the workflow retries once, and the trace clearly shows the retry path.

## Models, APIs, and frameworks used

- LangGraph for the stateful agent workflow and human approval interrupt
- FastAPI for the HTTP layer
- ChromaDB for the private knowledge base
- Groq / Google Gemini via LangChain providers for routing, grading, ticket drafting, and answer generation
- DuckDuckGo Search for public web fallback
- SQLite for sandboxed ticket persistence and verification audit
- Vanilla HTML/CSS/JavaScript for the demo UI

## Known limitations

- Single-tool action space: the agent only creates support tickets; it does not yet call other external tools.
- Simulated ticket system: the ticket backend is SQLite-backed and sandboxed rather than a production ticketing system.
- No auth beyond admin key: the upload endpoint uses a simple key check, not real identity or RBAC.
- DuckDuckGo reliability: public search results can fluctuate and may not always be authoritative.
- LLM grading is imperfect: the grader can be wrong on weak evidence and might route a question incorrectly.

## What I would build next

- Browser/computer-use actions for real troubleshooting and operational tasks
- A tool registry pattern so new actions can be added without rewriting the graph
- Persistent memory across sessions with thread metadata and user context
- An eval harness measuring task success, verification pass rate, and retry reliability

## Assumptions

- The project is a demo for an internal IT support workflow and not a production ticketing integration.
- The private KB is intentionally small and local for demonstration purposes.
- Human approval is required before any ticket is created.
- Verification is implemented by reading the stored record back from SQLite before the workflow reports success.

## Production honesty

This is a working demo of an autonomous task worker that can answer questions and complete a ticketing task in a controlled environment. It does not implement enterprise SSO, role-based access control, a real ticketing backend, or true autonomous operations beyond the local sandbox workflow.
