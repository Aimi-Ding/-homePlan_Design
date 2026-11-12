"""HTTP 层：上传、任务、进度、结果页。"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from . import analytics, auth, config, jobs, runner
from .db import SessionLocal, init_db
from .models import JobEvent, Project, User

ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".pdf", ".dxf", ".dwg"}
TERMINAL = {"succeeded", "partial", "failed", "rejected"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="户型图设计器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")


@app.middleware("http")
async def visitor_middleware(request: Request, call_next):
    vid = analytics.visitor_id(request)
    if vid:
        request.state.visitor_id = vid
        analytics.touch(vid, request)
    response = await call_next(request)
    if vid:
        # 设计器 iframe、下载这些直接打开的请求带不了自定义 header，用 cookie 兜住
        response.set_cookie("vid", vid, max_age=365 * 86400, samesite="lax", path="/")
    return response


def _vendor_commit() -> str:
    try:
        return (config.VENDOR.parent / "UPSTREAM_COMMIT").read_text().strip()[:12]
    except Exception:
        return ""


def _json(text: str, default):
    try:
        return json.loads(text) if text else default
    except json.JSONDecodeError:
        return default


def _project_out(p: Project) -> dict:
    spec = _json(p.spec_json, {})
    probe = _json(p.probe_json, {})
    return {
        "id": p.id,
        "name": p.name,
        "status": p.status,
        "stage": p.stage,
        "progress": p.progress,
        "message": p.message,
        "errorCode": p.error_code,
        "mode": p.mode,
        "input": {"name": p.input_name, "kind": p.input_kind},
        "probe": probe,
        "stats": {
            "walls": len(spec.get("walls") or []),
            "rooms": len(spec.get("rooms") or []),
            "furniture": len(spec.get("furniture") or []),
        },
        "issues": _json(p.issues_json, []),
        "urls": {
            "designer": f"/p/{p.id}",
            "html": f"/api/projects/{p.id}/download/html" if p.html_path else "",
            "zip": f"/api/projects/{p.id}/download/zip" if p.zip_path else "",
            "overlay": f"/api/projects/{p.id}/overlay" if p.overlay_path else "",
        },
        "createdAt": p.created_at.isoformat() if isinstance(p.created_at, datetime) else str(p.created_at),
    }


def _get(pid: str) -> Project:
    with SessionLocal() as s:
        p = s.get(Project, pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


def _optional_user(request: Request) -> User | None:
    try:
        return auth.current_user(request)
    except HTTPException:
        return None


def _vid(request: Request) -> str:
    return getattr(request.state, "visitor_id", "")


def _get_accessible(pid: str, request: Request) -> Project:
    """登录用户按 user_id 放行；匿名访客按 visitor_id 放行。"""
    p = _get(pid)
    user = _optional_user(request)
    vid = _vid(request)
    if (user and p.user_id == user.id) or (vid and p.visitor_id == vid):
        return p
    raise HTTPException(404, "项目不存在")


class PhoneIn(BaseModel):
    phone: str


class LoginIn(BaseModel):
    phone: str
    code: str


class LeadIn(BaseModel):
    phone: str
    projectId: str = ""
    consent: bool = False
    source: str = "download"


@app.post("/api/auth/send-code")
def send_code(payload: PhoneIn) -> dict:
    phone, code = auth.issue_code(payload.phone)
    out = {"ok": True, "phone": auth.mask_phone(phone), "ttl": config.CODE_TTL_SECONDS}
    if config.SMS_DEBUG:  # 本地没有短信通道时，把验证码带给前端
        out["debugCode"] = code
    return out


@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request, response: Response) -> dict:
    user, token = auth.verify_code(payload.phone, payload.code)
    # 匿名阶段的方案并入账号，登录后还能在「我的方案」里看到
    vid = _vid(request)
    if vid:
        with SessionLocal() as s:
            rows = s.scalars(select(Project).where(Project.visitor_id == vid, Project.user_id == "")).all()
            for p in rows:
                p.user_id = user.id
            s.commit()
    response.set_cookie(
        "sid", token,
        max_age=config.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=config.COOKIE_SECURE, path="/",
    )
    return {"user": auth.user_out(user)}


@app.get("/api/auth/me")
def me(request: Request) -> dict:
    """未登录时返回 user=null，而不是 401：前端启动时不用制造一条控制台报错。"""
    try:
        user = auth.current_user(request)
    except HTTPException:
        return {"user": None}
    return {"user": auth.user_out(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    auth.logout(request.cookies.get("sid"))
    response.delete_cookie("sid", path="/")
    return {"ok": True}


@app.post("/api/leads")
def create_lead(payload: LeadIn, request: Request) -> dict:
    phone = re.sub(r"\D", "", payload.phone or "")
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise HTTPException(400, "请输入 11 位手机号")
    if not payload.consent:
        raise HTTPException(400, "请先勾选同意隐私说明")
    analytics.save_lead(phone, _vid(request), payload.projectId[:32], request, payload.source[:40])
    return {"ok": True, "phone": f"{phone[:3]}****{phone[-4:]}"}


@app.get("/api/config")
def api_config() -> dict:
    return {
        "mode": config.effective_mode(),
        "llmEnabled": config.llm_enabled(),
        "model": config.LLM_MODEL if config.llm_enabled() else "",
        "maxUploadMb": config.MAX_UPLOAD_MB,
        "maxJobs": config.MAX_JOBS,
        "vendorCommit": _vendor_commit(),
        "accept": sorted(x.lstrip(".") for x in ALLOWED if x != ".dwg"),
        "auth": {"smsProvider": config.SMS_PROVIDER, "smsDebug": config.SMS_DEBUG},
        "leadRequired": config.LEAD_REQUIRED,
    }


@app.post("/api/projects")
async def create_project(
    file: UploadFile = File(...),
    name: str = Form(""),
    total_width: str = Form(""),
    total_height: str = Form(""),
    furniture: str = Form("false"),
    crop: str = Form(""),
    request: Request = None,
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"不支持的文件类型 {suffix or '（无扩展名）'}")
    if suffix == ".dwg":
        raise HTTPException(400, "DWG 无法直接解析，请另存为 DXF 或 PDF 后上传")

    data = await file.read()
    if not data:
        raise HTTPException(400, "文件是空的")
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {config.MAX_UPLOAD_MB}MB")

    user = _optional_user(request)
    vid = _vid(request)
    project = Project(name=(name.strip() or Path(file.filename).stem or "我的户型")[:80],
                      user_id=user.id if user else "", visitor_id=vid,
                      input_name=file.filename or "", input_kind=suffix.lstrip("."))
    with SessionLocal() as s:
        s.add(project)
        s.commit()
        pid = project.id

    pdir = config.PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)
    target = pdir / f"input{suffix}"
    target.write_bytes(data)

    options: dict = {"name": project.name, "furniture": furniture == "true"}
    try:
        if total_width.strip():
            options["totalWidth"] = float(total_width)
        if total_height.strip():
            options["totalHeight"] = float(total_height)
    except ValueError:
        raise HTTPException(400, "总宽/总深要填数字（毫米）")
    if crop.strip():
        nums = [int(v) for v in re.split(r"[,\s]+", crop.strip()) if v]
        if len(nums) == 4:
            options["crop"] = nums

    runner.set_state(pid, event="已上传，等待生成", status="queued", stage="queued",
                     progress=0, input_path=str(target), options_json=json.dumps(options, ensure_ascii=False))
    jobs.submit(pid)
    analytics.log_event(vid, "upload", pid, {"kind": suffix, "bytes": len(data), "name": project.name})
    analytics.log_event(vid, "generate_start", pid)
    return {"id": pid}


@app.get("/api/projects")
def list_projects(request: Request) -> list[dict]:
    user = _optional_user(request)
    vid = _vid(request)
    with SessionLocal() as s:
        if user:
            rows = s.scalars(select(Project).where(Project.user_id == user.id)
                             .order_by(Project.created_at.desc()).limit(60)).all()
        elif vid:
            rows = s.scalars(select(Project).where(Project.visitor_id == vid)
                             .order_by(Project.created_at.desc()).limit(60)).all()
        else:
            return []
        return [{"id": p.id, "name": p.name, "status": p.status, "progress": p.progress,
                 "stage": p.stage, "createdAt": p.created_at.isoformat()} for p in rows]


@app.get("/api/projects/{pid}")
def get_project(pid: str, request: Request) -> dict:
    return _project_out(_get_accessible(pid, request))


@app.get("/api/projects/{pid}/events")
def get_events(pid: str, request: Request, after: int = 0) -> dict:
    p = _get_accessible(pid, request)
    with SessionLocal() as s:
        rows = s.scalars(select(JobEvent).where(JobEvent.project_id == pid, JobEvent.id > after)
                         .order_by(JobEvent.id)).all()
        events = [{"id": e.id, "level": e.level, "stage": e.stage, "message": e.message,
                   "at": e.created_at.isoformat()} for e in rows]
    return {"events": events, "status": p.status, "progress": p.progress, "stage": p.stage,
            "terminal": p.status in TERMINAL}


@app.get("/api/projects/{pid}/stream")
async def stream(pid: str, request: Request, after: int = 0):
    _get_accessible(pid, request)
    async def gen():
        last = after
        while True:
            with SessionLocal() as s:
                p = s.get(Project, pid)
                if not p:
                    yield "event: error\ndata: {}\n\n"
                    return
                rows = s.scalars(select(JobEvent).where(JobEvent.project_id == pid, JobEvent.id > last)
                                 .order_by(JobEvent.id)).all()
                events = [{"id": e.id, "level": e.level, "stage": e.stage, "message": e.message}
                          for e in rows]
                payload = {"events": events, "status": p.status, "progress": p.progress,
                           "stage": p.stage, "terminal": p.status in TERMINAL}
            if events:
                last = events[-1]["id"]
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if payload["terminal"]:
                return
            await asyncio.sleep(0.8)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/projects/{pid}/retry")
def retry(pid: str, request: Request) -> dict:
    p = _get_accessible(pid, request)
    if p.status not in TERMINAL:
        raise HTTPException(409, "任务还在进行中")
    if not p.input_path or not Path(p.input_path).exists():
        raise HTTPException(400, "原始图纸已丢失，请重新上传")
    runner.set_state(pid, event="重新生成", status="queued", stage="queued", progress=0,
                     error_code="", message="", issues_json="[]")
    jobs.submit(pid)
    return {"id": pid}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, request: Request) -> dict:
    _get_accessible(pid, request)
    if pid in jobs.running():
        raise HTTPException(409, "任务进行中，无法删除")
    with SessionLocal() as s:
        p = s.get(Project, pid)
        if p:
            s.delete(p)
            s.commit()
    shutil.rmtree(config.PROJECTS_DIR / pid, ignore_errors=True)
    return {"ok": True}


def _file(p: Project, attr: str, filename: str, media: str) -> FileResponse:
    path = Path(getattr(p, attr) or "")
    if not path.exists():
        raise HTTPException(404, "文件还没有生成")
    return FileResponse(str(path), filename=filename, media_type=media)


@app.get("/p/{pid}")
def designer_page(pid: str, request: Request):
    p = _get_accessible(pid, request)
    if not p.html_path or not Path(p.html_path).exists():
        raise HTTPException(404, "方案还没有生成完成")
    analytics.log_event(_vid(request), "designer_open", pid)
    return FileResponse(p.html_path, media_type="text/html")


@app.get("/api/projects/{pid}/download/html")
def download_html(pid: str, request: Request):
    p = _get_accessible(pid, request)
    analytics.log_event(_vid(request), "download", pid, {"format": "html"})
    base = p.name.replace("/", "_")
    return _file(p, "html_path", f"{base}-装修设计器.html", "text/html")


@app.get("/api/projects/{pid}/download/zip")
def download_zip(pid: str, request: Request):
    p = _get_accessible(pid, request)
    analytics.log_event(_vid(request), "download", pid, {"format": "zip"})
    base = p.name.replace("/", "_")
    return _file(p, "zip_path", f"{base}-装修设计器.zip", "application/zip")


@app.get("/api/projects/{pid}/overlay")
def overlay(pid: str, request: Request):
    p = _get_accessible(pid, request)
    return _file(p, "overlay_path", "overlay.png", "image/png")


@app.get("/api/projects/{pid}/spec")
def spec(pid: str, request: Request):
    p = _get_accessible(pid, request)
    if not p.spec_json:
        raise HTTPException(404, "还没有户型数据")
    return JSONResponse(_json(p.spec_json, {}))


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    ok = (config.TEMPLATE.exists(), (config.SCRIPTS / "build.py").exists())
    return {"ok": all(ok), "template": ok[0], "scripts": ok[1], "mode": config.effective_mode()}
