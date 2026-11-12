"""墙体拓扑修复（纯几何，不依赖模型）。

`analyze.py` 给出的是「按像素识别出的墙段」，门窗洞口、掉色的墙、T 形交接处常常断开，
直接渲染会得到一堆悬空墙、房间也围不起来。这里做三件保守的事：

1. 近似横平竖直的墙拉直（±60mm 内）；
2. 同一直线上、间距不大的断段合并，断口记为门窗；
3. 端点吸附到 450mm 内的垂直墙中线上（闭合 T 形 / L 形交接）。
"""
from __future__ import annotations

import copy

AXIS_TOL = 60
JOIN_TOL = 450
MERGE_TOL = 2000


def _is_h(w: dict) -> bool:
    return abs(w["a"][1] - w["b"][1]) <= abs(w["a"][0] - w["b"][0])


def _norm_orientation(walls: list[dict]) -> None:
    for w in walls:
        (x1, y1), (x2, y2) = w["a"], w["b"]
        if _is_h(w):
            y = round((y1 + y2) / 2)
            a, b = [min(x1, x2), y], [max(x1, x2), y]
        else:
            x = round((x1 + x2) / 2)
            a, b = [x, min(y1, y2)], [x, max(y1, y2)]
        w["a"], w["b"] = a, b


def _straighten(walls: list[dict]) -> list[dict]:
    out = []
    for w in walls:
        (x1, y1), (x2, y2) = w["a"], w["b"]
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if 0 < dy <= AXIS_TOL and dx >= AXIS_TOL:
            y = round((y1 + y2) / 2)
            w["a"], w["b"] = [min(x1, x2), y], [max(x1, x2), y]
        elif 0 < dx <= AXIS_TOL and dy >= AXIS_TOL:
            x = round((x1 + x2) / 2)
            w["a"], w["b"] = [x, min(y1, y2)], [x, max(y1, y2)]
        if w["a"] != w["b"]:
            out.append(w)
    return out


def _outline(walls: list[dict]) -> tuple[int, int, int, int]:
    xs = [p[0] for w in walls for p in (w["a"], w["b"])]
    ys = [p[1] for w in walls for p in (w["a"], w["b"])]
    return min(xs), min(ys), max(xs), max(ys)


def _merge_collinear(walls: list[dict]) -> list[dict]:
    box = _outline(walls)
    result: list[dict] = []
    for horizontal in (True, False):
        group = [w for w in walls if _is_h(w) == horizontal]
        group.sort(key=lambda w: (
            w["a"][1] if horizontal else w["a"][0],
            min(w["a"][0], w["b"][0]) if horizontal else min(w["a"][1], w["b"][1]),
        ))
        used = [False] * len(group)
        for i, w in enumerate(group):
            if used[i]:
                continue
            used[i] = True
            line = w["a"][1] if horizontal else w["a"][0]
            lo = min(w["a"][0], w["b"][0]) if horizontal else min(w["a"][1], w["b"][1])
            hi = max(w["a"][0], w["b"][0]) if horizontal else max(w["a"][1], w["b"][1])
            thick = float(w.get("t") or 120)
            bearing = bool(w.get("bearing"))
            ops = list(w.get("openings") or [])
            changed = True
            while changed:
                changed = False
                for j, o in enumerate(group):
                    if used[j]:
                        continue
                    oline = o["a"][1] if horizontal else o["a"][0]
                    if abs(oline - line) > AXIS_TOL * 1.5:
                        continue
                    olo = min(o["a"][0], o["b"][0]) if horizontal else min(o["a"][1], o["b"][1])
                    ohi = max(o["a"][0], o["b"][0]) if horizontal else max(o["a"][1], o["b"][1])
                    if olo > hi + MERGE_TOL or ohi < lo - MERGE_TOL:
                        continue
                    # 两个区间之间确实有断口才把它记成门窗
                    def note_gap(g0: float, g1: float) -> None:
                        width = g1 - g0
                        if width < 300:
                            return
                        if _on_outline(line, (g0 + g1) / 2, horizontal, box):
                            kind = "window"
                        elif width >= 1200:
                            kind = "hole"
                        else:
                            kind = "door"
                        ops.append({"type": kind, "from": int(g0), "to": int(g1)})

                    if olo > hi:
                        note_gap(hi, olo)
                    elif lo > ohi:
                        note_gap(ohi, lo)
                    lo, hi = min(lo, olo), max(hi, ohi)
                    thick = max(thick, float(o.get("t") or 120))
                    bearing = bearing or bool(o.get("bearing"))
                    ops += list(o.get("openings") or [])
                    line = round((line + oline) / 2) if abs(oline - line) <= AXIS_TOL else line
                    used[j] = True
                    changed = True
            if hi - lo < 100:
                continue
            wall = {"a": [int(lo), int(line)] if horizontal else [int(line), int(lo)],
                    "b": [int(hi), int(line)] if horizontal else [int(line), int(hi)],
                    "t": thick, "bearing": bearing}
            seen = set()
            clean_ops = []
            for op in ops:
                key = (op.get("type"), op.get("from"), op.get("to"))
                if key in seen:
                    continue
                seen.add(key)
                clean_ops.append(op)
            if clean_ops:
                wall["openings"] = clean_ops
            result.append(wall)
    return result


def _on_outline(line: float, mid: float, horizontal: bool, box: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = box
    if horizontal:
        return abs(line - y0) <= 260 or abs(line - y1) <= 260
    return abs(line - x0) <= 260 or abs(line - x1) <= 260


def _snap_joints(walls: list[dict]) -> None:
    verticals = [w for w in walls if not _is_h(w)]
    horizontals = [w for w in walls if _is_h(w)]
    for w in walls:
        if _is_h(w):
            for end in ("a", "b"):
                x, y = w[end]
                best = None
                for v in verticals:
                    vx = v["a"][0]
                    vlo, vhi = min(v["a"][1], v["b"][1]), max(v["a"][1], v["b"][1])
                    if vlo - 250 <= y <= vhi + 250 and abs(vx - x) <= JOIN_TOL:
                        d = abs(vx - x)
                        if best is None or d < best[0]:
                            best = (d, vx)
                if best:
                    w[end] = [best[1], y]
            if w["a"][0] > w["b"][0]:
                w["a"], w["b"] = w["b"], w["a"]
        else:
            for end in ("a", "b"):
                x, y = w[end]
                best = None
                for o in horizontals:
                    oy = o["a"][1]
                    olo, ohi = min(o["a"][0], o["b"][0]), max(o["a"][0], o["b"][0])
                    if olo - 250 <= x <= ohi + 250 and abs(oy - y) <= JOIN_TOL:
                        d = abs(oy - y)
                        if best is None or d < best[0]:
                            best = (d, oy)
                if best:
                    w[end] = [x, best[1]]
            if w["a"][1] > w["b"][1]:
                w["a"], w["b"] = w["b"], w["a"]


def repair(walls: list[dict]) -> list[dict]:
    fixed = _straighten(copy.deepcopy(walls))
    fixed = _merge_collinear(fixed)
    _snap_joints(fixed)
    # 吸附后可能出现零长度或重合墙，去掉
    out = []
    for w in fixed:
        if w["a"] == w["b"]:
            continue
        if any(o["a"] == w["a"] and o["b"] == w["b"] and abs(float(o.get("t", 0)) - float(w.get("t", 0))) < 1 for o in out):
            continue
        out.append(w)
    return out
