"""匿名访客与转化埋点。

不依赖登录：前端生成 visitor_id，后端记录来源、行为和留资手机号，
后面用户登录了还可以用 visitor_id 把匿名阶段和账号阶段串起来。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from fastapi import Request

from .db import SessionLocal
from .models import EventLog, Lead, Visitor

VID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
TOUCH_INTERVAL = 600  # 同一个访客 10 分钟内只更新一次 last_seen，避免每次请求都写库
_last_touch: dict[str, float] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or ""
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "")
    return ip[:64]


def visitor_id(request: Request) -> str:
    vid = request.headers.get("x-visitor-id") or request.cookies.get("vid") or ""
    return vid if VID_RE.match(vid) else ""


def touch(vid: str, request: Request) -> None:
    if not vid:
        return
    now = time.time()
    if now - _last_touch.get(vid, 0) < TOUCH_INTERVAL:
        return
    _last_touch[vid] = now
    referer = (request.headers.get("referer") or "")[:500]
    ua = (request.headers.get("user-agent") or "")[:500]
    with SessionLocal() as s:
        v = s.get(Visitor, vid)
        if not v:
            s.add(Visitor(visitor_id=vid, ip=client_ip(request), ua=ua, referer=referer))
            s.add(EventLog(visitor_id=vid, event="visit",
                           payload=json.dumps({"referer": referer[:200]}, ensure_ascii=False)))
        else:
            v.last_seen = _now()
            if client_ip(request):
                v.ip = client_ip(request)
        s.commit()


def log_event(vid: str, event: str, project_id: str = "", payload: dict | None = None) -> None:
    if not vid:
        return
    with SessionLocal() as s:
        s.add(EventLog(visitor_id=vid, project_id=project_id, event=event,
                       payload=json.dumps(payload or {}, ensure_ascii=False)))
        s.commit()


def save_lead(phone: str, vid: str, project_id: str, request: Request, source: str = "download") -> None:
    with SessionLocal() as s:
        s.add(Lead(phone=phone, visitor_id=vid, project_id=project_id, source=source,
                   ip=client_ip(request), ua=(request.headers.get("user-agent") or "")[:500]))
        s.commit()
    log_event(vid, "lead", project_id, {"source": source})
