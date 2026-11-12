"""任务执行器：把一次生成跑完，并把每个阶段写进数据库（前端靠它做进度条）。"""
from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path

from . import analytics, config
from .db import SessionLocal
from .models import JobEvent, Project
from .pipeline import agent
from .pipeline.errors import PipelineError
from .pipeline.probe import ProbeRejected, probe


def _project(pid: str) -> Project | None:
    with SessionLocal() as s:
        return s.get(Project, pid)


def set_state(pid: str, event: str | None = None, level: str = "info", **fields) -> None:
    with SessionLocal() as s:
        p = s.get(Project, pid)
        if not p:
            return
        for k, v in fields.items():
            setattr(p, k, v)
        if event:
            s.add(JobEvent(project_id=pid, level=level, stage=p.stage, message=event))
        s.commit()


def _fail(pid: str, code: str, message: str, hint: str = "") -> None:
    full = message if not hint else f"{message}。{hint}"
    set_state(pid, event=full, level="error", status="failed", stage="failed",
              progress=100, error_code=code, message=full)
    if hint:
        with SessionLocal() as s:
            s.add(JobEvent(project_id=pid, level="info", stage="failed", message=f"建议：{hint}"))
            s.commit()


def run(pid: str) -> None:
    p = _project(pid)
    if not p:
        return
    project_dir = config.PROJECTS_DIR / pid
    input_path = Path(p.input_path)
    options = json.loads(p.options_json or "{}")

    try:
        set_state(pid, event="正在检查图纸", status="running", stage="probe", progress=5, message="")
        try:
            pr = probe(input_path)
        except ProbeRejected as exc:
            set_state(pid, event=exc.reason, level="error", status="rejected", stage="rejected",
                      progress=100, error_code=exc.code, message=exc.reason)
            return
        set_state(pid, probe_json=json.dumps(pr.to_dict(), ensure_ascii=False))
        if pr.reason:
            set_state(pid, event=pr.reason, level="warn")

        def on_stage(stage: str, pct: int, msg: str) -> None:
            set_state(pid, event=msg, stage=stage, progress=pct, message=msg)

        result = agent.generate(project_dir, input_path, pr, options, on_stage)

        has_error = any(i.get("level") == "error" for i in result["issues"])
        low_quality = (result.get("quality") or {}).get("coverage", 1) < 0.45
        status = "partial" if (has_error or low_quality) else "succeeded"
        set_state(
            pid,
            event="生成完成" if status == "succeeded" else "生成完成（需人工确认）",
            level="info" if status == "succeeded" else "warn",
            status=status, stage="done", progress=100,
            spec_json=json.dumps(result["spec"], ensure_ascii=False),
            report_json=json.dumps(result["report"], ensure_ascii=False),
            issues_json=json.dumps(result["issues"], ensure_ascii=False),
            html_path=result["html"], zip_path=result["zip"], overlay_path=result["overlay"],
            mode=result["mode"], message="；".join(result["notes"])[:2000],
        )
        for note in result["notes"]:
            with SessionLocal() as s:
                s.add(JobEvent(project_id=pid, level="info", stage="done", message=note))
                s.commit()
        analytics.log_event(p.visitor_id, "generate_done", pid,
                            {"status": status, **(result.get("stats") or {}),
                             "coverage": (result.get("quality") or {}).get("coverage")})
    except PipelineError as exc:
        _fail(pid, exc.code, exc.message, exc.hint)
    except Exception as exc:  # 兜底，避免任务静默消失
        traceback.print_exc()
        _fail(pid, "INTERNAL", f"生成失败：{exc}", "请重试；如果反复失败，把这张图反馈给我们。")


def remove_project_files(pid: str) -> None:
    shutil.rmtree(config.PROJECTS_DIR / pid, ignore_errors=True)
