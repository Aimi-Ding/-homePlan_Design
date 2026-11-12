"""HouseSpec 的轻量校验与清洗。

上游没有 JSON Schema，字段又多；这里只做「不合法就丢弃/裁剪」的保守处理，
保证模型输出不会把整条流水线搞崩，同时记录问题给前端展示。
"""
from __future__ import annotations

import math
from typing import Any


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and abs(f) < 1e6 else None


def _pt(v: Any) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        return None
    x, y = _num(v[0]), _num(v[1])
    if x is None or y is None:
        return None
    return [round(x, 1), round(y, 1)]


def strip_private(obj: Any) -> Any:
    """去掉上游脚本内部用的 `_xxx` 字段（draft 里有 `_sure` / `_gap`）。"""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


def validate(spec: Any, draft_walls: list[dict]) -> tuple[dict, list[str]]:
    problems: list[str] = []
    if not isinstance(spec, dict):
        return {"walls": draft_walls, "rooms": [], "name": "我的户型"}, ["模型输出不是 JSON 对象"]
    out: dict = {}

    out["name"] = str(spec.get("name") or "我的户型")[:80]
    out["height"] = int(_num(spec.get("height")) or 2800)
    if not 2000 <= out["height"] <= 6000:
        out["height"] = 2800
    out["phase"] = spec.get("phase") if spec.get("phase") in ("renovate", "original") else "renovate"
    out["autoElec"] = bool(spec.get("autoElec", True))
    for key in ("north", "site", "look"):
        if key in spec:
            out[key] = spec[key]

    walls = []
    for w in spec.get("walls") or []:
        if not isinstance(w, dict):
            continue
        a, b = _pt(w.get("a")), _pt(w.get("b"))
        if not a or not b or a == b:
            continue
        t = _num(w.get("t")) or 100
        t = max(40, min(600, t))
        wall = {"a": a, "b": b, "t": round(t, 1)}
        if "bearing" in w:
            wall["bearing"] = bool(w["bearing"])
        if w.get("flush") in ("N", "S", "E", "W", "C"):
            wall["flush"] = w["flush"]
        ops = []
        horizontal = abs(a[1] - b[1]) <= abs(a[0] - b[0])
        for o in w.get("openings") or []:
            if not isinstance(o, dict):
                continue
            f, to = _num(o.get("from")), _num(o.get("to"))
            if f is None or to is None or abs(to - f) < 100:
                continue
            lo, hi = min(f, to), max(f, to)
            length = abs(b[0] - a[0]) if horizontal else abs(b[1] - a[1])
            origin = min(a[0], b[0]) if horizontal else min(a[1], b[1])
            if lo < origin - 50 or hi > origin + length + 50:
                problems.append("门窗洞口超出墙段范围，已丢弃")
                continue
            op = {"type": str(o.get("type") or "door"), "from": round(lo, 1), "to": round(hi, 1)}
            for k in ("hinge", "sill", "head", "entry", "depth"):
                if k in o:
                    op[k] = o[k]
            if _pt(o.get("into")):
                op["into"] = _pt(o["into"])
            ops.append(op)
        if ops:
            wall["openings"] = ops
        walls.append(wall)

    if len(walls) < max(3, int(len(draft_walls) * 0.8)):
        problems.append(f"模型只给出 {len(walls)} 面墙（初稿 {len(draft_walls)} 面），已回退到几何初稿的墙")
        walls = draft_walls
    out["walls"] = walls or draft_walls

    rooms = []
    for r in spec.get("rooms") or []:
        if not isinstance(r, dict):
            continue
        at = _pt(r.get("at"))
        if not at:
            continue
        room = {"at": at, "name": str(r.get("name") or "房间")[:20]}
        for k in ("mat", "wallMat", "skirting", "ceil", "tile", "lighting"):
            if k in r:
                room[k] = r[k]
        rooms.append(room)
    out["rooms"] = rooms

    structs = []
    for s in spec.get("structs") or []:
        if isinstance(s, dict) and _pt(s.get("at")):
            item = {"kind": str(s.get("kind") or "column"), "at": _pt(s["at"])}
            for k in ("w", "d", "rot"):
                if k in s:
                    item[k] = s[k]
            structs.append(item)
    if structs:
        out["structs"] = structs

    furniture = []
    for f in spec.get("furniture") or []:
        if not isinstance(f, dict) or not f.get("key") or not _pt(f.get("at")):
            continue
        item = {"key": str(f["key"]), "at": _pt(f["at"])}
        rot = _num(f.get("rot"))
        if rot is not None:
            item["rot"] = round(rot % 360, 1)
        for k in ("w", "d", "h", "opt", "quad"):
            if k in f:
                item[k] = f[k]
        furniture.append(item)
    if furniture:
        out["furniture"] = furniture

    if isinstance(spec.get("elec"), list):
        out["elec"] = [e for e in spec["elec"] if isinstance(e, dict) and _pt(e.get("at"))]

    for k in ("beams", "comments", "bearingFrom", "heightFrom", "dims", "expectDims"):
        if k in spec:
            out[k] = spec[k]

    # 底图必须来自几何脚本算好的坐标，模型无权修改
    return out, problems
