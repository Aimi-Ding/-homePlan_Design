"""生成编排：几何初稿 → （可选）模型补全 → 渲染 → 自查 → 修正。

设计原则：
- 墙和毫米坐标永远来自几何脚本，模型只做语义（门窗类型、房间名、家具）；
- 模型只提「增量修正」，出现墙数明显变少就整体回退；
- 自查有硬错误就带着报告再修一轮，最多 LLM_MAX_ROUNDS 轮。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .. import config
from . import checker, geometry, llm, render, repair, rule_spec, schema
from .probe import Probe
from .prompts import CORE_SYSTEM, user_text

Stage = Callable[[str, int, str], None]


def _noop(_stage: str, _pct: int, _msg: str) -> None:
    pass


def _draft_summary(draft: dict, analysis: dict) -> str:
    walls = [{k: w[k] for k in ("a", "b", "t", "bearing", "openings", "flush") if k in w}
             for w in draft.get("walls") or []]
    info = {
        "mmPerPx": analysis.get("mmPerPx"),
        "scaleFrom": analysis.get("scale_from"),
        "scaleCheck": analysis.get("scale_check"),
        "outlineMm": analysis.get("outline_mm"),
        "bearingBy": analysis.get("bearing_by"),
        "bearingUnsureWalls": analysis.get("bearing_unsure"),
        "dimLines": analysis.get("dim_lines"),
    }
    return json.dumps({"walls": walls, "scale": info}, ensure_ascii=False)


def _fallback_note(notes: list[str], exc: Exception) -> None:
    notes.append(f"模型补全失败，已退回几何初稿：{exc}")


def generate(project_dir: Path, input_path: Path, pr: Probe, options: dict,
             on_stage: Stage | None = None) -> dict:
    stage: Stage = on_stage or _noop
    notes: list[str] = []
    mode = config.effective_mode()

    stage("geometry", 15, "正在识别墙体与比例")
    geo = geometry.prepare(project_dir, input_path, pr, options)
    draft, analysis, image = geo["draft"], geo["analysis"], Path(geo["image"])
    raw_count = len(draft.get("walls") or [])
    draft["walls"] = repair.repair(draft.get("walls") or [])
    stage("geometry", 30, f"识别到 {len(draft['walls'])} 段墙（修复前 {raw_count} 段）")

    detected = rule_spec.detect_rooms(draft["walls"])
    outline = (analysis.get("outline_mm") or [0, 0])
    outline_area = max(1.0, float(outline[0]) * float(outline[1]) / 1e6)
    room_area = sum(r.get("area") or 0 for r in detected)
    coverage = round(min(1.0, room_area / outline_area), 3)

    spec = rule_spec.build(draft, options, detected)
    if coverage < 0.45:
        notes.append(
            f"户型只围合出 {room_area:.1f}㎡（约占外轮廓 {coverage:.0%}），可能有墙体没识别出来；"
            "建议换一张带完整尺寸标注的清晰户型图，或启用模型补全。"
        )
    if not mode == "llm":
        notes.append("未配置视觉模型：当前只还原墙体与房间，家具和门窗类型需要模型补全。")

    if mode == "llm":
        stage("spec", 40, "模型正在读图补全门窗与房间")
        try:
            raw = llm.complete_json(
                CORE_SYSTEM,
                user_text(_draft_summary(draft, analysis), "", ""),
                [image],
            )
            candidate, problems = schema.validate(raw, draft["walls"])
            candidate["underlay"] = draft.get("underlay", {})
            candidate["name"] = str(options.get("name") or candidate.get("name") or "我的户型")[:80]
            spec = candidate
            notes.extend(problems)
        except Exception as exc:  # 模型不可用不该让整单失败
            _fallback_note(notes, exc)
            mode = "rule"

    work = geo["work"]
    out_html = project_dir / "designer.html"
    report: dict = {}
    issues: list[dict] = []
    max_rounds = config.LLM_MAX_ROUNDS if mode == "llm" else 1

    for round_no in range(1, max_rounds + 1):
        stage("build", 55, f"正在生成设计器（第 {round_no} 版）")
        render.build_html(spec, work, out_html)
        stage("check", 70, "正在自查墙体、门窗与尺寸")
        report = checker.run_check(out_html, work / "check")
        issues, clean = checker.summarize(report)
        if clean:
            break
        if mode != "llm" or round_no >= max_rounds:
            notes.append("自查仍有未解决的问题，已在结果页列出。")
            break

        overlay = work / "check" / "overlay.png"
        stage("spec", 62, f"模型正在按自查报告修正（第 {round_no + 1} 轮）")
        try:
            raw = llm.complete_json(
                CORE_SYSTEM,
                user_text(_draft_summary(draft, analysis), "", checker.feedback_text(report, issues)),
                [image] + ([overlay] if overlay.exists() else []),
            )
            candidate, problems = schema.validate(raw, draft["walls"])
            candidate["underlay"] = draft.get("underlay", {})
            candidate["name"] = str(options.get("name") or candidate.get("name") or "我的户型")[:80]
            spec = candidate
            notes.extend(problems)
        except Exception as exc:
            notes.append(f"第 {round_no + 1} 轮修正失败：{exc}")
            break

    stage("finalize", 90, "正在打包交付文件")
    if coverage < 0.45:
        issues.insert(0, {
            "level": "warn",
            "title": "户型识别不完整",
            "detail": f"只围合出 {room_area:.1f}㎡（约占外轮廓 {coverage:.0%}），缺的墙需要在设计器里补，"
                      "或换一张带完整尺寸标注的清晰户型图。",
        })
    info = render.build_html(spec, work, out_html, with_zip=True)
    overlay = work / "check" / "overlay.png"
    return {
        "spec": spec,
        "report": report,
        "issues": issues,
        "html": str(out_html),
        "zip": info.get("zip", ""),
        "overlay": str(overlay) if overlay.exists() else "",
        "notes": notes,
        "mode": mode,
        "quality": {"coverage": coverage, "roomArea": round(room_area, 1),
                    "outlineArea": round(outline_area, 1)},
        "stats": {"walls": len(spec.get("walls") or []), "rooms": len(spec.get("rooms") or []),
                  "furniture": len(spec.get("furniture") or [])},
    }
