"""调用上游 build.py 把 HouseSpec 渲染成单文件设计器 HTML。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .. import config
from .errors import PipelineError


def _last_json(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def build_html(spec: dict, work_dir: Path, out_html: Path, with_zip: bool = False) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_path = work_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    out_html.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(config.SCRIPTS / "build.py"), str(spec_path), "-o", str(out_html)]
    if with_zip:
        cmd.append("--zip")
    p = subprocess.run(
        cmd, cwd=str(config.SCRIPTS), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        raise PipelineError(
            f"渲染失败：{tail[-1] if tail else '未知错误'}",
            code="BUILD_FAILED",
            hint="这通常是生成的户型数据有问题，请重试或换一张图。",
        )
    info = _last_json(p.stdout)
    info.setdefault("out", str(out_html))
    info["spec_path"] = str(spec_path)
    if with_zip:
        zp = out_html.with_suffix(".zip")
        if zp.exists():
            info["zip"] = str(zp)
    return info
