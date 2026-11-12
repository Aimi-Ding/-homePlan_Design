"""规则版户型生成：不依赖任何模型。

用途有两个：
1. 作为模型输出的「基线」，保证墙体和毫米坐标一定来自几何脚本；
2. 没配模型 key 时，网站仍能跑通完整链路（墙体 + 房间 + 轻量家具）。
"""
from __future__ import annotations

from collections import deque

CELL = 100  # mm，栅格精度；对房间识别足够，且 12m×12m 只有 1.4 万个格子


def _rect_of(wall: dict) -> tuple[float, float, float, float]:
    (x1, y1), (x2, y2) = wall["a"], wall["b"]
    t = float(wall.get("t") or 100)
    return (min(x1, x2) - t / 2, min(y1, y2) - t / 2, max(x1, x2) + t / 2, max(y1, y2) + t / 2)


def detect_rooms(walls: list[dict], min_area_m2: float = 2.5) -> list[dict]:
    """把墙栅格化后做连通域，得到真正的「墙围合出来的房间」。"""
    if not walls:
        return []
    xs, ys = [], []
    for w in walls:
        for p in (w["a"], w["b"]):
            xs.append(p[0])
            ys.append(p[1])
    pad = 800
    x0, y0 = min(xs) - pad, min(ys) - pad
    x1, y1 = max(xs) + pad, max(ys) + pad
    w_cells = max(1, int((x1 - x0) / CELL))
    h_cells = max(1, int((y1 - y0) / CELL))
    if w_cells * h_cells > 400_000:  # 超大户型降精度，避免卡死
        return []

    blocked = bytearray(w_cells * h_cells)
    for w in walls:
        bx0, by0, bx1, by1 = _rect_of(w)
        i0 = max(0, int((bx0 - x0) / CELL))
        i1 = min(w_cells - 1, int((bx1 - x0) / CELL))
        j0 = max(0, int((by0 - y0) / CELL))
        j1 = min(h_cells - 1, int((by1 - y0) / CELL))
        for j in range(j0, j1 + 1):
            row = j * w_cells
            for i in range(i0, i1 + 1):
                blocked[row + i] = 1

    outside = bytearray(w_cells * h_cells)
    q: deque[int] = deque()
    for i in range(w_cells):
        for idx in (i, (h_cells - 1) * w_cells + i):
            if not blocked[idx] and not outside[idx]:
                outside[idx] = 1
                q.append(idx)
    for j in range(h_cells):
        for idx in (j * w_cells, j * w_cells + w_cells - 1):
            if not blocked[idx] and not outside[idx]:
                outside[idx] = 1
                q.append(idx)
    while q:
        idx = q.popleft()
        i, j = idx % w_cells, idx // w_cells
        for ni, nj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
            if 0 <= ni < w_cells and 0 <= nj < h_cells:
                n = nj * w_cells + ni
                if not blocked[n] and not outside[n]:
                    outside[n] = 1
                    q.append(n)

    seen = bytearray(w_cells * h_cells)
    rooms = []
    for start in range(w_cells * h_cells):
        if blocked[start] or outside[start] or seen[start]:
            continue
        cells = []
        q = deque([start])
        seen[start] = 1
        while q:
            idx = q.popleft()
            cells.append(idx)
            i, j = idx % w_cells, idx // w_cells
            for ni, nj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if 0 <= ni < w_cells and 0 <= nj < h_cells:
                    n = nj * w_cells + ni
                    if not blocked[n] and not outside[n] and not seen[n]:
                        seen[n] = 1
                        q.append(n)
        area = len(cells) * CELL * CELL / 1e6
        if area < min_area_m2:
            continue
        cx = sum(c % w_cells for c in cells) / len(cells)
        cy = sum(c // w_cells for c in cells) / len(cells)
        # 质心可能落在凹形房间外，取离质心最近的格子
        best = min(cells, key=lambda c: (c % w_cells - cx) ** 2 + (c // w_cells - cy) ** 2)
        at = [round(x0 + (best % w_cells + 0.5) * CELL), round(y0 + (best // w_cells + 0.5) * CELL)]
        left = x0 + min(c % w_cells for c in cells) * CELL
        right = x0 + (max(c % w_cells for c in cells) + 1) * CELL
        top = y0 + min(c // w_cells for c in cells) * CELL
        bottom = y0 + (max(c // w_cells for c in cells) + 1) * CELL
        rooms.append({"at": at, "area": round(area, 1), "bbox": [round(left), round(top), round(right), round(bottom)]})
    rooms.sort(key=lambda r: -r["area"])
    return rooms


ROOM_NAMES = ["客厅", "主卧", "次卧", "厨房", "卫生间", "餐厅", "阳台", "书房", "儿童房", "入户"]


def _place(room: dict, key: str, w: float, d: float, rot: int, cx: float, cy: float) -> dict:
    left, top, right, bottom = room["bbox"]
    cx = min(max(cx, left + w / 2 + 50), right - w / 2 - 50)
    cy = min(max(cy, top + d / 2 + 50), bottom - d / 2 - 50)
    return {"key": key, "at": [round(cx), round(cy)], "rot": rot}


def _furnish(rooms: list[dict]) -> list[dict]:
    """按房间类型放最必要的家具。刻意保守：只贴墙放，避免挡门和挡窗。"""
    furn: list[dict] = []
    for r in rooms:
        name = r["name"]
        left, top, right, bottom = r["bbox"]
        w, h = right - left, bottom - top
        cx = (left + right) / 2
        if name in ("主卧", "次卧", "儿童房"):
            bw, bd = (1800, 2100) if w >= 2000 else (1500, 2050)
            if h >= bd + 200:
                furn.append(_place(r, "bed18" if bw == 1800 else "bed15", bw, bd, 0, cx, top + bd / 2 + 60))
        elif name == "客厅":
            if w >= 2400:
                furn.append(_place(r, "sofa", 2200, 950, 0, cx, top + 950 / 2 + 60))
            if h >= 3600 and w >= 2600:
                furn.append(_place(r, "cabTV", 2400, 400, 180, cx, bottom - 400 / 2 - 60))
                furn.append(_place(r, "coffeeTable", 1200, 600, 0, cx, top + 950 + 600 + 400))
        elif name == "餐厅" and w >= 1800 and h >= 1200:
            furn.append(_place(r, "diningTable", 1600, 900, 0, cx, (top + bottom) / 2))
        elif name == "厨房" and w >= 1800:
            furn.append(_place(r, "cabBase", 1600, 600, 0, cx, top + 600 / 2 + 60))
        elif name == "卫生间" and w >= 1200 and h >= 1500:
            furn.append(_place(r, "toilet", 400, 700, 270, left + 400 / 2 + 60, top + h * 0.35))
            if w >= 1600 and h >= 1800:
                furn.append(_place(r, "shower", 900, 900, 180, right - 900 / 2 - 60, bottom - 900 / 2 - 60))
    return furn


def build(draft: dict, options: dict, detected: list[dict] | None = None) -> dict:
    walls = draft.get("walls") or []
    detected = detected if detected is not None else detect_rooms(walls)
    if detected:
        for i, r in enumerate(detected):
            r["name"] = ROOM_NAMES[i] if i < len(ROOM_NAMES) else f"房间{i + 1}"
    else:
        # 墙没有围合（自由端多）时退化成一个大标签，至少让设计器有房间可命名
        xs = [p[0] for w in walls for p in (w["a"], w["b"])] or [0, 4000]
        ys = [p[1] for w in walls for p in (w["a"], w["b"])] or [0, 4000]
        detected = [{"at": [round((min(xs) + max(xs)) / 2), round((min(ys) + max(ys)) / 2)],
                     "area": 0, "bbox": [min(xs), min(ys), max(xs), max(ys)], "name": "客厅"}]

    rooms = [{"at": r["at"], "name": r["name"]} for r in detected]
    spec = {
        "name": str(options.get("name") or draft.get("name") or "我的户型")[:80],
        "height": int(draft.get("height") or 2800),
        "phase": "renovate",
        "autoElec": True,
        "walls": walls,
        "rooms": rooms,
    }
    if draft.get("underlay"):
        spec["underlay"] = draft["underlay"]
    if options.get("furniture"):
        spec["furniture"] = _furnish(detected)
    spec["comments"] = ["当前为几何初稿：墙体、门窗洞口由识别脚本给出，房间名为自动编号，家具为规则摆放。"]
    return spec
