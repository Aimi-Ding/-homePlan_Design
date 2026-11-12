"""设计器自查：用无头 Chrome 打开生成的 HTML，读 report.json。

`qa.mjs` 要 11~40 分钟，线上不跑；这里只用 check.mjs（分钟级以内），
拿墙是否连上、门窗是否画出来、尺寸链是否超差这类硬指标。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .. import config

CHECK_TIMEOUT = 300


def available() -> tuple[bool, str]:
    if shutil.which("node") is None:
        return False, "服务端没有 node"
    if not (config.SCRIPTS / "node_modules").exists():
        return False, "缺少 node_modules（在 scripts 目录执行 npm install）"
    if not config.TEMPLATE.exists():
        return False, "缺少设计器模板"
    return True, ""


def run_check(html: Path, out_dir: Path) -> dict:
    ok, why = available()
    if not ok:
        return {"available": False, "error": why}
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["node", str(config.SCRIPTS / "check.mjs"), str(html), str(out_dir)]
    try:
        p = subprocess.run(
            cmd, cwd=str(config.SCRIPTS), capture_output=True, text=True, timeout=CHECK_TIMEOUT,
            env={**os.environ, "CHROME_NO_SANDBOX": os.environ.get("CHROME_NO_SANDBOX", "")},
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "error": f"自查超时（>{CHECK_TIMEOUT}s）"}
    report_path = out_dir / "report.json"
    if not report_path.exists():
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        return {"available": False, "error": f"自查没有产出报告：{tail[-1] if tail else '未知错误'}"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["available"] = True
    return report


def summarize(report: dict) -> tuple[list[dict], bool]:
    """把 report 收敛成给前端和模型看的短清单。第二个返回值表示是否「干净」。"""
    if not report.get("available"):
        return ([{"level": "info", "title": "未执行自查", "detail": report.get("error", "")}], True)

    issues: list[dict] = []

    def add(level: str, title: str, detail: str = "") -> None:
        issues.append({"level": level, "title": title, "detail": detail})

    for err in report.get("errors") or []:
        add("error", "页面报错", str(err))
    for key, title in (("wallsDropped", "墙没有画出来"), ("wallsSkewed", "墙被吸附歪了"),
                       ("openingsHidden", "门窗没有画出来"), ("labelsOutsideRooms", "房间标签落在墙外")):
        val = report.get(key) or []
        if val:
            add("error", title, f"{len(val)} 处：{json.dumps(val[:3], ensure_ascii=False)}")
    for msg in report.get("issues") or []:
        text = str(msg)
        level = "error" if text.startswith("[error]") else "warn" if text.startswith("[warn]") else "info"
        add(level, "设计器检查", text.replace("[error]", "").replace("[warn]", "").strip())
    for msg in report.get("dimIssues") or []:
        add("warn", "尺寸对不上", str(msg))
    for msg in report.get("dimConflicts") or []:
        add("info", "图纸自身尺寸矛盾", str(msg))
    free = report.get("freeWallEnds") or []
    if free:
        add("warn", "有悬空的墙端", f"{len(free)} 处（可能是半截隔断）")
    if report.get("openingsDropped"):
        add("warn", "门窗放不下被丢弃", f"{report['openingsDropped']} 处")

    clean = not any(i["level"] == "error" for i in issues)
    return issues, clean


def feedback_text(report: dict, issues: list[dict]) -> str:
    lines = []
    for i in issues[:20]:
        lines.append(f"- [{i['level']}] {i['title']}：{i['detail']}")
    rooms = report.get("rooms") or []
    if rooms:
        lines.append("房间：" + "；".join(f"{r.get('name')} {r.get('area_m2')}㎡ {r.get('size_mm')}" for r in rooms[:12]))
    return "\n".join(lines)
