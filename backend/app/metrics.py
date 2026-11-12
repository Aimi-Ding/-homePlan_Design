"""对外暴露的标准指标接口，供「工具中台看板」拉取。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from .db import SessionLocal
from .models import EventLog, Lead, Project, Visitor

EVENT_KEYS = {
    "visits": "visit",
    "uploads": "upload",
    "generations": "generate_done",
    "downloads": "download",
    "designerOpens": "designer_open",
}


def summary(days: int = 7) -> dict:
    days = max(1, min(90, int(days)))
    today = datetime.now().date()
    since = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())

    with SessionLocal() as s:
        daily: dict[str, dict] = {}
        for i in range(days):
            d = (today - timedelta(days=days - 1 - i)).isoformat()
            daily[d] = {"date": d, "uv": 0, "visits": 0, "uploads": 0, "generations": 0,
                        "downloads": 0, "designerOpens": 0, "leads": 0}

        # 每日访问量 / UV
        rows = s.execute(
            select(func.date(EventLog.created_at), func.count(),
                   func.count(func.distinct(EventLog.visitor_id)))
            .where(EventLog.event == "visit", EventLog.created_at >= since)
            .group_by(func.date(EventLog.created_at))
        ).all()
        for day, cnt, uv in rows:
            key = str(day)[:10]
            if key in daily:
                daily[key]["visits"] = int(cnt)
                daily[key]["uv"] = int(uv)

        # 其它事件
        for field, event in EVENT_KEYS.items():
            if field == "visits":
                continue
            rows = s.execute(
                select(func.date(EventLog.created_at), func.count())
                .where(EventLog.event == event, EventLog.created_at >= since)
                .group_by(func.date(EventLog.created_at))
            ).all()
            for day, cnt in rows:
                key = str(day)[:10]
                if key in daily:
                    daily[key][field] = int(cnt)

        # 留资
        rows = s.execute(
            select(func.date(Lead.created_at), func.count())
            .where(Lead.created_at >= since)
            .group_by(func.date(Lead.created_at))
        ).all()
        for day, cnt in rows:
            key = str(day)[:10]
            if key in daily:
                daily[key]["leads"] = int(cnt)

        total_visitors = s.scalar(
            select(func.count(func.distinct(EventLog.visitor_id)))
            .where(EventLog.created_at >= since, EventLog.visitor_id != "")
        ) or 0
        total_projects = s.scalar(
            select(func.count()).select_from(Project).where(Project.created_at >= since)
        ) or 0
        all_visitors = s.scalar(select(func.count()).select_from(Visitor)) or 0

    daily_list = [daily[k] for k in sorted(daily)]
    totals = {
        # UV 用区间内去重，不能把每天 UV 相加（同一个人多天访问会重复计算）
        "uv": int(total_visitors),
        "visits": sum(d["visits"] for d in daily_list),
        "uploads": sum(d["uploads"] for d in daily_list),
        "generations": sum(d["generations"] for d in daily_list),
        "downloads": sum(d["downloads"] for d in daily_list),
        "designerOpens": sum(d["designerOpens"] for d in daily_list),
        "leads": sum(d["leads"] for d in daily_list),
        "projects": int(total_projects),
        "visitorsInRange": int(total_visitors),
        "visitorsAllTime": int(all_visitors),
    }
    return {"days": days, "totals": totals, "daily": daily_list,
            "generatedAt": datetime.now().isoformat(timespec="seconds")}
