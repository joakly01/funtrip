from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from trading_agent.common import DomainError
from .growth import GrowthWorkspace


class NewItem(BaseModel):
    kind: str
    fields: dict[str, str]
    refs: list[str] = Field(default_factory=list, max_length=40)
    key: str = Field(min_length=1, max_length=150)


class EditItem(BaseModel):
    version: int = Field(ge=1)
    patch: dict[str, str]
    refs: list[str] | None = Field(default=None, max_length=40)
    refresh_refs: list[str] = Field(default_factory=list, max_length=40)
    key: str = Field(min_length=1, max_length=150)


class PracticeAction(BaseModel):
    version: int = Field(ge=1)
    action: str
    key: str = Field(min_length=1, max_length=150)


class Decision(BaseModel):
    decision: str
    fingerprint: str = ""


class RulesProposal(BaseModel):
    rules: list[dict] = Field(max_length=50)
    rationale: str = Field(min_length=1, max_length=8000)
    refs: list[str] = Field(default_factory=list, max_length=40)
    base_id: str | None = None
    key: str = Field(min_length=1, max_length=150)


def install_growth_routes(app, owner, store):
    workspace = GrowthWorkspace(store)
    router = APIRouter(prefix="/api/growth", dependencies=[Depends(owner)])

    def perform(fn, *args):
        try:
            return fn(*args)
        except DomainError as e:
            code = 404 if e.code == "NOT_FOUND" else 409 if e.code in {"VERSION_CONFLICT", "PROPOSAL_CHANGED"} else 400
            raise HTTPException(code, e.message) from None

    @router.get("")
    def overview():
        return perform(workspace.overview)

    @router.post("/items")
    def create(body: NewItem):
        return perform(workspace.create, body.kind, body.fields, body.refs, body.key)

    @router.get("/items/{identifier}")
    def detail(identifier: str):
        return perform(workspace.detail, identifier)

    @router.put("/items/{identifier}")
    def edit(identifier: str, body: EditItem):
        return perform(workspace.update, identifier, body.version, body.patch, body.refs, body.key, body.refresh_refs)

    @router.post("/items/{identifier}/action")
    def practice(identifier: str, body: PracticeAction):
        return perform(workspace.practice_action, identifier, body.version, body.action, body.key)

    @router.post("/suggestions/{identifier}/decision")
    def suggestion(identifier: str, body: Decision):
        return perform(workspace.decide_suggestion, identifier, body.decision)

    @router.post("/proposals")
    def propose(body: RulesProposal):
        return perform(workspace.propose_rules, body.rules, body.rationale, body.refs, body.base_id, body.key)

    @router.get("/proposals/{identifier}")
    def proposal(identifier: str):
        return perform(workspace.proposal_detail, identifier)

    @router.post("/proposals/{identifier}/decision")
    def decide(identifier: str, body: Decision):
        # Owner-only: intentionally absent from model tools.
        return perform(workspace.decide_proposal, identifier, body.decision, body.fingerprint)

    app.include_router(router)
    return workspace
