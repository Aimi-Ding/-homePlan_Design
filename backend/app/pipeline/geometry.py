"""几何识别：调用上游脚本把图纸变成 draft.json（墙 + 比例 + 底图坐标）。

这一层是纯算法、无模型，决定了「毫米坐标」准不准，后面的模型只负责语义。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .. import config
from .errors import PipelineError
from .probe import Probe
from .schema import strip_private

GEOMETRY_TIMEOUT = 240


def _last_json(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def _run(cmd: list[str], cwd: Path, timeout: int = GEOMETRY_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )


def _raise_from(p: subprocess.CompletedProcess) -> None:
    err = (p.stderr or "").strip().splitlines()
    tail = err[-1] if err else "未知错误"
    if "需要比例" in tail:
        raise PipelineError(
            "缺少比例：需要知道户型的实际尺寸才能换算成毫米",
            code="NEEDS_SCALE",
            hint="请填写户型外轮廓的总宽（或总深），单位为毫米；图上标注的是轴线尺寸也可以填总宽。",
        )
    if "没有识别到墙体" in tail:
        raise PipelineError(
            "这张图里没有识别到墙体",
            code="NO_WALLS",
            hint="可能是纯线稿、扫描件或墙体颜色太浅。请上传带尺寸标注的彩色户型图，或 CAD 导出的 PDF/DXF。",
        )
    raise PipelineError(f"几何识别失败：{tail}", code="GEOMETRY_FAILED", hint="换一张更清晰的图，或改用 CAD 图纸。")


def render_pdf_first_page(pdf: Path, out_png: Path, dpi: int = 200) -> Path:
    try:
        import pymupdf
    except Exception as exc:  # pragma: no cover
        raise PipelineError("服务端缺少 pymupdf，无法渲染 PDF", "PDF_UNSUPPORTED") from exc
    doc = pymupdf.open(pdf)
    pix = doc[0].get_pixmap(dpi=dpi)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_png))
    return out_png


def _analyze(image: Path, out_dir: Path, options: dict, pr: Probe) -> dict:
    cmd = [sys.executable, str(config.SCRIPTS / "analyze.py"), str(image), "--out", str(out_dir)]
    if options.get("totalWidth"):
        cmd += ["--total-width", str(options["totalWidth"])]
    elif options.get("totalHeight"):
        cmd += ["--total-height", str(options["totalHeight"])]
    elif options.get("mmPerPx"):
        cmd += ["--scale", str(options["mmPerPx"])]
    if options.get("crop"):
        box = options["crop"]
        cmd += ["--crop", *[str(int(v)) for v in box]]
    outlined = options.get("outlined")
    if outlined is None:
        outlined = (pr.details or {}).get("outlinedWalls")
        if outlined is None:  # PDF 渲染出来的位图没有预检结果，这里补判一次
            try:
                from PIL import Image

                from .probe import _outlined_style

                outlined = _outlined_style(Image.open(image))
            except Exception:
                outlined = False
    if outlined:
        cmd += ["--outlined", "--outline-max", str(options.get("outlineMax") or 600),
                "--outline-min-len", str(options.get("outlineMinLen") or 600)]
    p = _run(cmd, cwd=config.SCRIPTS)
    if p.returncode != 0:
        _raise_from(p)
    return _last_json(p.stdout)


def _cadvec(file: Path, out_dir: Path, options: dict) -> dict:
    cmd = [sys.executable, str(config.SCRIPTS / "cadvec.py"), str(file), "--out", str(out_dir)]
    if options.get("totalWidth"):
        cmd += ["--total-width", str(options["totalWidth"])]
    if options.get("totalHeight"):
        cmd += ["--total-height", str(options["totalHeight"])]
    p = _run(cmd, cwd=config.SCRIPTS)
    if p.returncode != 0:
        _raise_from(p)
    return _last_json(p.stdout)


def _read_draft(out_dir: Path) -> dict:
    path = out_dir / "draft.json"
    if not path.exists():
        raise PipelineError("识别没有产出户型初稿", code="NO_DRAFT", hint="请换一张图重试。")
    draft = strip_private(json.loads(path.read_text(encoding="utf-8")))
    for w in draft.get("walls") or []:
        w.pop("id", None)
    if not draft.get("walls"):
        raise PipelineError("识别结果里没有墙", code="NO_WALLS", hint="换一张更清晰的户型图。")
    return draft


def prepare(project_dir: Path, input_path: Path, pr: Probe, options: dict) -> dict:
    """返回 {draft, analysis, image, work_dir}。所有路径都是绝对路径。"""
    work = project_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    analysis: dict = {}
    image = input_path

    if pr.kind == "image_dark":
        # analyze.py 自己会检测深色背景并反相，这里只标记，方便前端提示
        pass

    if pr.kind == "dxf":
        analysis = _cadvec(input_path, work / "geom", options)
        draft = _read_draft(work / "geom")
    elif pr.kind == "pdf_vector":
        try:
            analysis = _cadvec(input_path, work / "geom", options)
            draft = _read_draft(work / "geom")
        except PipelineError:
            # 矢量识别兜底：渲染成位图再走图片路线
            image = render_pdf_first_page(input_path, work / "page1.png")
            analysis = _analyze(image, work / "geom_img", options, pr)
            draft = _read_draft(work / "geom_img")
    elif pr.kind == "pdf_raster":
        image = render_pdf_first_page(input_path, work / "page1.png")
        analysis = _analyze(image, work / "geom", options, pr)
        draft = _read_draft(work / "geom")
    else:
        analysis = _analyze(input_path, work / "geom", options, pr)
        draft = _read_draft(work / "geom")

    underlay = draft.get("underlay") or {}
    if underlay.get("image"):
        p = Path(underlay["image"])
        if not p.is_absolute():
            p = work / p
        underlay["image"] = str(p.resolve())
        draft["underlay"] = underlay

    return {"draft": draft, "analysis": analysis, "image": str(image), "work": work}
