from __future__ import annotations

from fastapi import FastAPI

from app.models import Alert, PostmortemDraft, PostmortemRequest, RunbookSummary, TriageResponse
from app.postmortem import draft_postmortem
from app.runbook_retriever import RunbookRetriever
from app.triage import TriageService


app = FastAPI(
    title="SRE Incident Copilot",
    description="A governed, read-only incident triage assistant for SRE workflows.",
    version="0.1.0",
)

triage_service = TriageService()
runbook_retriever = RunbookRetriever()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "read-only"}


@app.post("/triage", response_model=TriageResponse)
def triage(alert: Alert) -> TriageResponse:
    return triage_service.triage(alert)


@app.get("/runbooks/{service}", response_model=list[RunbookSummary])
def runbooks(service: str) -> list[RunbookSummary]:
    return runbook_retriever.list_for_service(service)


@app.post("/postmortem/draft", response_model=PostmortemDraft)
def postmortem(request: PostmortemRequest) -> PostmortemDraft:
    return draft_postmortem(request)
