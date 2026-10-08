from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from .config import Config
from .runtime import assemble
from .library import GROUPS, library
from .knowledge import KnowledgeLibrary, SHELVES, TOPICS
from .workspace import system_workspace, rule_form, check_current_rules
from .growth_api import install_growth_routes
from .companion_api import install_companion_routes
from .chart_tools import install_chart_routes
from .attachments import AttachmentStore, AttachmentError, MAX_BYTES, MAX_FILES
from trading_agent.metadata import describe_record
from trading_agent.common import DomainError
from trading_agent.catalog import ClassicCatalog
from audit_agent_runtime.context.query_models import QueryRequest, ToolPolicy
from audit_agent_runtime.persistence.models import SessionRecord, MessageRecord
from trading_agent.service import Service

STATIC = Path(__file__).parent / "static"
ID = r"^[a-zA-Z0-9_-]{1,100}$"


class Login(BaseModel):
    password: str


class Ask(BaseModel):
    session_id: str = Field(pattern=ID)
    message_id: str = Field(pattern=ID)
    text: str = Field(default="", max_length=16000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=MAX_FILES)
    remember: bool = True
    model: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    context_id: str | None = Field(default=None, pattern=ID)


class SelectModel(BaseModel):
    model: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")


class OrganizeKnowledge(BaseModel):
    shelf: str
    topic: str
    starred: bool


class RiskPosition(BaseModel):
    security_id: str = Field(min_length=1, max_length=60)
    quantity: float = Field(gt=0, allow_inf_nan=False)
    price: float = Field(gt=0, allow_inf_nan=False)
    scenario_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class RiskInput(BaseModel):
    positions: list[RiskPosition] = Field(min_length=1, max_length=20)
    account_equity: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class RuleInput(BaseModel):
    inputs: dict = Field(default_factory=dict, max_length=30)


def create_app(config=None, model_client=None):
    config = config or Config.from_env()
    services = assemble(config, model_client)
    business = Service(config.data_dir)
    knowledge = KnowledgeLibrary(business.store, config.runtime_dir)
    attachments = AttachmentStore(config.runtime_dir, config.asr_model_dir)
    services.model_client.attachments = attachments
    services.model_client.session_factory = services.session_factory
    tokens = {}
    gate = threading.Lock()
    cache_path = config.runtime_dir / "runtime.sqlite"
    with sqlite3.connect(cache_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS cloud_requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, events TEXT NOT NULL)")
        # 上次进程中断的请求不自动重跑，以免重复已经成功的外部或业务写入。
        db.execute("UPDATE cloud_requests SET status='interrupted' WHERE status='running'")

    @asynccontextmanager
    async def lifespan(app):
        yield
        services.query._post_turn_maintenance_executor.shutdown(wait=True)
        services.model_client.close()
        services.engine.dispose()

    app = FastAPI(title="我的交易伙伴", lifespan=lifespan)
    app.state.services = services
    app.state.config = config
    app.state.attachments = attachments

    def owner(request: Request):
        token = request.cookies.get("trading_session", "")
        if tokens.get(token, 0) < time.time():
            tokens.pop(token, None)
            raise HTTPException(401, "请登录。")
        return "owner"

    def call(name, arguments):
        result = business.call(name, arguments)
        if result["status"] != "ok":
            raise HTTPException(400, result["error"]["message"])
        return result["data"]

    growth = install_growth_routes(app, owner, business.store)
    companion = install_companion_routes(app, owner, business.store, services.capabilities)
    install_chart_routes(app, owner, call, config.runtime_dir)

    def sse(event):
        return "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    @app.get("/")
    def index():
        if (STATIC / "unified/index.html").is_file():
            return RedirectResponse("/next/")
        return FileResponse(STATIC / "index.html")

    @app.get("/legacy")
    def legacy():
        return FileResponse(STATIC / "index.html")

    if (STATIC / "unified/index.html").is_file():
        app.mount("/next", StaticFiles(directory=STATIC / "unified", html=True), name="unified")

    @app.get("/assets/{filename}")
    def asset(filename: str):
        if filename not in {"app.js", "style.css", "attachments.js"}:
            raise HTTPException(404)
        return FileResponse(STATIC / filename)

    @app.get("/health")
    def health():
        return {"status": "ok", "model_configured": services.model_client.ready,
                "login_configured": bool(config.password)}

    @app.post("/api/login")
    def login(body: Login, response: Response):
        if not config.password:
            raise HTTPException(503, "请在启动配置中设置TRADING_PASSWORD。")
        if not hmac.compare_digest(body.password.encode(), config.password.encode()):
            raise HTTPException(401, "密码不正确。")
        token = secrets.token_urlsafe(32)
        tokens[token] = time.time() + 86400
        response.set_cookie("trading_session", token, httponly=True, samesite="lax",
                            secure=config.secure_cookie, max_age=86400)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        tokens.pop(request.cookies.get("trading_session", ""), None)
        response.delete_cookie("trading_session")
        return {"ok": True}

    @app.get("/api/status", dependencies=[Depends(owner)])
    def status():
        return {"model_configured": services.model_client.ready, "model": services.model_client.model_name,
                "agent": "trading_partner", "tools": len(services.tools.definitions()),
                "scope": "按需对话与研究；未启用后台跟踪或交易执行。"}

    @app.get("/api/models", dependencies=[Depends(owner)])
    def models():
        return services.model_client.public()

    @app.get("/api/attachments/status", dependencies=[Depends(owner)])
    def attachment_status():
        return {"voice_ready": attachments.speech.ready, "max_files": MAX_FILES,
                "max_bytes": MAX_BYTES, "max_audio_seconds": 120}

    @app.post("/api/attachments", dependencies=[Depends(owner)])
    async def upload_attachment(file: UploadFile):
        try:
            content = bytearray()
            while chunk := await file.read(1024 * 1024):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise HTTPException(413, "单个附件不能超过 20 MB。")
            return await run_in_threadpool(attachments.save, file.filename, bytes(content))
        except AttachmentError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            await file.close()

    @app.get("/api/attachments/{identifier}", dependencies=[Depends(owner)])
    def attachment_info(identifier: str):
        try:
            return attachments.public(attachments.get(identifier))
        except AttachmentError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/attachments/{identifier}/content", dependencies=[Depends(owner)])
    def attachment_content(identifier: str):
        try:
            item = attachments.get(identifier)
            return FileResponse(attachments.path(identifier), media_type=item["mime"],
                                filename=item["name"], content_disposition_type="inline" if item["kind"] == "audio" else "attachment")
        except AttachmentError as error:
            raise HTTPException(404, str(error)) from None

    @app.get("/api/attachments/{identifier}/preview", dependencies=[Depends(owner)])
    def attachment_preview(identifier: str):
        try:
            item = attachments.get(identifier)
            if item["kind"] != "image":
                raise HTTPException(404)
            return FileResponse(attachments.path(identifier, preview=True), media_type="image/jpeg")
        except AttachmentError as error:
            raise HTTPException(404, str(error)) from None

    @app.post("/api/models/select", dependencies=[Depends(owner)])
    def select_model(body: SelectModel):
        if not gate.acquire(blocking=False):
            raise HTTPException(409, "请等待当前回复完成后切换模型。")
        try:
            try:
                services.model_client.select(body.model)
            except KeyError:
                raise HTTPException(404, "模型不存在。")
            except ValueError:
                raise HTTPException(503, "此模型尚未配置。")
            return services.model_client.public()
        finally:
            gate.release()

    @app.get("/api/sessions", dependencies=[Depends(owner)])
    def sessions():
        with services.session_factory() as db:
            rows = db.scalars(select(SessionRecord).where(SessionRecord.user_id == "owner")
                              .order_by(SessionRecord.updated_at.desc()).limit(100)).all()
            return [{"id": r.session_id, "title": r.title, "updated_at": r.updated_at} for r in rows]

    @app.get("/api/sessions/{session_id}", dependencies=[Depends(owner)])
    def messages(session_id: str):
        with services.session_factory() as db:
            row = db.get(SessionRecord, session_id)
            if row is None or row.user_id != "owner":
                raise HTTPException(404, "会话不存在。")
            rows = db.scalars(select(MessageRecord).where(MessageRecord.session_id == session_id)
                              .order_by(MessageRecord.id)).all()
            return [{"role": r.role, "text": r.payload_json.get("text", ""),
                     "attachments": attachments.from_content(r.payload_json.get("content")) if r.role == "user" else []}
                    for r in rows if r.role in {"user", "assistant"}
                    and (r.payload_json.get("text") or r.payload_json.get("content"))]

    @app.get("/api/records", dependencies=[Depends(owner)])
    def records(query: str = "", kind: str | None = None):
        args = {"query": query[:500], "limit": 50}
        if kind:
            args["kind"] = kind
        return call("memory_search", args)

    @app.get("/api/records/{record_id}", dependencies=[Depends(owner)])
    def record(record_id: str, version: int | None = Query(default=None, ge=1)):
        row = call("memory_read", {"object_id": record_id, **({"version": version} if version else {})})
        related = {r["id"]: r for r in business.store.snapshot(limit=None)["records"]
                   if r["id"] in {*row["reference_ids"], *row["dependent_ids"]}}
        links = {identifier: {"kind": r["kind"], "title": str(r["body"].get("title") or r["body"].get("text")
                                                          or r["body"].get("name") or "关联资料").split("\n")[0][:70]}
                 for identifier, r in related.items()}
        return {**row, **describe_record(row), "collection": knowledge.describe(row), "links": links}

    @app.get("/api/knowledge", dependencies=[Depends(owner)])
    def knowledge_browse(query: str = Query(default="", max_length=500), shelf: str = "outcomes",
                         topic: str = "", kind: str = "", state: str = "", starred: bool = False,
                         page: int = Query(default=1, ge=1), size: int = Query(default=12, ge=1, le=48)):
        if shelf not in {*SHELVES, "all"} or topic not in {*TOPICS, ""} or state not in {"", "needs_review"}:
            raise HTTPException(400, "请选择已有的资料层级、主题与状态")
        return knowledge.browse(query, shelf, topic, kind, state, starred, page, size)

    @app.put("/api/knowledge/{record_id}", dependencies=[Depends(owner)])
    def knowledge_organize(record_id: str, body: OrganizeKnowledge):
        try:
            return knowledge.organize(record_id, body.shelf, body.topic, body.starred)
        except DomainError as error:
            raise HTTPException(404 if error.code == "NOT_FOUND" else 400, error.message)

    @app.get("/api/system", dependencies=[Depends(owner)])
    def system():
        names = {definition["name"] for definition in services.tools.definitions()}
        return system_workspace(knowledge, set(services.skill_names), names, services.model_client.ready)

    @app.post("/api/tools/risk", dependencies=[Depends(owner)])
    def risk_calculate(body: RiskInput):
        return call("risk_compute", {"positions": [p.model_dump(exclude_none=True) for p in body.positions],
                                     "account_equity": body.account_equity})

    @app.get("/api/tools/quote", dependencies=[Depends(owner)])
    def quote_lookup(query: str = Query(default="", max_length=40),
                     security_id: str = Query(default="", pattern=r"^(?:(?:SSE|SZSE|BSE):\d{6})?$")):
        if security_id:
            return {"quote": call("market_quote", {"security_id": security_id, "persist": False})["data"]}
        if not query.strip():
            raise HTTPException(400, "请输入股票名称或代码")
        resolved = call("security_resolve", {"query": query})
        if not resolved.get("resolved"):
            return {"quote": None, **resolved}
        return {"quote": call("market_quote", {"security_id": resolved["resolved"]["security_id"], "persist": False})["data"]}

    @app.get("/api/tools/journal", dependencies=[Depends(owner)])
    def journal():
        rows = [{**r, **describe_record(r)} for r in business.store.snapshot(limit=None)["records"]
                if r["kind"] in {"position", "trade"}]
        return {"records": rows, "scope": "个人记录的历史持仓和成交，未同步券商，不代表当前完整账户。"}

    @app.get("/api/tools/rules", dependencies=[Depends(owner)])
    def rule_fields():
        return rule_form(business.store)

    @app.post("/api/tools/rules", dependencies=[Depends(owner)])
    def rule_check(body: RuleInput):
        return check_current_rules(business.store, body.inputs)

    @app.get("/api/library", dependencies=[Depends(owner)])
    def collection(query: str = Query(default="", max_length=500), group: str = "all",
                   kind: str = "", state: str = "", page: int = Query(default=1, ge=1),
                   size: int = Query(default=12, ge=1, le=48)):
        if group not in {"all", *GROUPS} or state not in {"", "needs_review"}:
            raise HTTPException(400, "请使用资料页提供的分类与状态。")
        return {**library(business.store, query, group, kind, state, page, size),
                "classic_count": len(ClassicCatalog().entries())}

    @app.get("/api/classics", dependencies=[Depends(owner)])
    def classics(query: str = Query(default="", max_length=500)):
        catalog = ClassicCatalog()
        entries = catalog.entries()
        words = query.casefold().split()
        matching = []
        for entry in entries:
            haystack = " ".join([entry["id"], entry["title"], *entry["tags"]]).casefold()
            score = sum(word in haystack for word in words)
            if not words or score:
                matching.append((score, entry["id"]))
        matching.sort(key=lambda item: (-item[0], item[1]))
        return {"catalog_size": len(entries),
                "entries": [{key: value for key, value in catalog.get(identifier).items() if key != "path"}
                            for _, identifier in matching]}

    @app.get("/api/overview", dependencies=[Depends(owner)])
    def overview():
        return call("system_overview", {})

    @app.get("/api/positions", dependencies=[Depends(owner)])
    def positions():
        return call("positions_get", {"limit": 50})

    @app.get("/api/charts/{filename}", dependencies=[Depends(owner)])
    def chart(filename: str):
        if not filename.startswith("market_snapshot_") or not filename.endswith(".svg") or Path(filename).name != filename:
            raise HTTPException(404)
        path = config.data_dir / "exports" / filename
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/svg+xml")

    @app.post("/api/router/ask", dependencies=[Depends(owner)])
    def ask(body: Ask):
        if not body.text.strip() and not body.attachment_ids:
            raise HTTPException(400, "请输入内容或添加附件。")
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
        with sqlite3.connect(cache_path) as db:
            old = db.execute("SELECT fingerprint,status,events FROM cloud_requests WHERE id=?", (body.message_id,)).fetchone()
        if old:
            if old[0] != fingerprint:
                raise HTTPException(409, "同一消息标识不能提交不同内容。")
            if old[1] == "running":
                raise HTTPException(409, "这条消息正在处理，请稍后查看历史。")
            events = json.loads(old[2])
            if not events:
                events = [{"type": "error", "payload": {"text": "上次处理被中断。请先查看历史和已保存资料，再发送后续问题。"}}]
            return StreamingResponse((sse(e) for e in events), media_type="text/event-stream")
        page_context = None
        if body.context_id:
            try:
                page_context = growth.context(body.context_id)
            except DomainError as error:
                raise HTTPException(404, error.message)
        model_id = body.model or services.model_client.active
        try:
            services.model_client.validate(model_id)
        except KeyError:
            raise HTTPException(404, "模型不存在。")
        except ValueError:
            raise HTTPException(503, "真实模型尚未配置。请设置模型地址、密钥和名称后重启。")
        try:
            resolved = attachments.resolve(body.attachment_ids)
        except AttachmentError as error:
            raise HTTPException(400, str(error)) from None
        if any(item["formatter"] == "image" for item in resolved) and not services.model_client.profiles[model_id].vision:
            raise HTTPException(400, "当前模型不支持读图，请切换到支持图片的模型后发送。")
        if not gate.acquire(blocking=False):
            raise HTTPException(409, "正在处理上一条消息，请稍后再试。")
        try:
            with sqlite3.connect(cache_path) as db:
                db.execute("INSERT INTO cloud_requests VALUES(?,?,?,?)", (body.message_id, fingerprint, "running", "[]"))
        except Exception:
            gate.release()
            raise

        def stream():
            events = []
            state = "interrupted"
            iterator = None
            try:
                request = QueryRequest(session_id=body.session_id, user_id="owner", input_text=body.text or "请读取我上传的附件。",
                    metadata={"agent_id": "trading_partner", "cloud_input": body.text,
                              "cloud_message_id": body.message_id, "cloud_remember": body.remember,
                              "cloud_model": model_id, "cloud_session_id": body.session_id,
                              "cloud_context": page_context,
                              "cloud_learning": companion.context(body.context_id),
                              "resolved_attachments": resolved},
                    skip_slash_commands=True, max_turns=config.max_turns,
                    tool_policy=ToolPolicy(mode="allow_list", tools=[t.name for t in services.tools.list()]))
                iterator = services.query.submit(request)
                for event in iterator:
                    entry = {"type": event.type, "payload": event.payload}
                    if event.type == "assistant" and str(event.payload.get("text", "")).startswith("Model error:"):
                        entry = {"type": "assistant", "payload": {"text": "模型连接出现问题，请检查设置；已保存的内容仍可查看。"}}
                    if event.type == "done" and event.payload.get("stop_reason") == "model_error":
                        state = "failed"
                        failure = {"type": "error", "payload": {"text": "本次模型调用失败，原始消息已保留。"}}
                        events.append(failure)
                        yield sse(failure)
                    events.append(entry)
                    yield sse(entry)
                if state != "failed":
                    state = "completed"
            except Exception:
                state = "failed"
                entry = {"type": "error", "payload": {"text": "本次处理失败，请检查模型连接。此前已保存的内容仍可在历史和资料库中查看。"}}
                events.append(entry)
                yield sse(entry)
            finally:
                if iterator is not None:
                    iterator.close()
                try:
                    with sqlite3.connect(cache_path) as db:
                        db.execute("UPDATE cloud_requests SET status=?,events=? WHERE id=?",
                                   (state, json.dumps(events, ensure_ascii=False, default=str), body.message_id))
                finally:
                    gate.release()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
