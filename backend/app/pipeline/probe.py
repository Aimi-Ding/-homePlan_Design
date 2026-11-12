"""入口预检：判断这是哪类图纸、能不能生成。

C 端的核心闸门——不合格的图在这里就拦掉，不花模型钱、也不给用户一个错方案。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

try:  # 可选依赖，缺了只是少一条路线
    import pymupdf
except Exception:  # pragma: no cover
    pymupdf = None

try:
    import ezdxf
except Exception:  # pragma: no cover
    ezdxf = None


@dataclass
class Probe:
    tier: str = "ok"           # ok | risky | reject
    kind: str = "image"        # image | image_dark | pdf_vector | pdf_raster | dxf
    reason: str = ""
    width: int = 0
    height: int = 0
    pages: int = 1
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ProbeRejected(Exception):
    def __init__(self, reason: str, code: str = "INPUT_REJECTED"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _outlined_style(im: Image.Image) -> bool:
    """判断墙体是不是「两条细线中间留白」的画法。

    依据：这类图的深色像素基本都是 1~3px 细线，用 9×9 腐蚀后剩不下多少；
    实心填色的墙腐蚀后仍会保留大片区域。
    """
    try:
        import cv2
        import numpy as np

        arr = np.array(im.convert("RGB"))
        mx, mn = arr.max(2), arr.min(2)
        thin = (((mx - mn) < 40) & (mx < 160)).astype(np.uint8)
        base = int(thin.sum())
        if base < 20_000:
            return False
        eroded = int(cv2.erode(thin, np.ones((9, 9), np.uint8)).sum())
        return eroded / base < 0.30
    except Exception:
        return False


def _image_info(path: Path) -> Probe:
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            w, h = im.size
            rgb = im.convert("RGB")
            small = rgb.resize((min(64, w), min(64, h)))
            px = list(small.getdata())
            dark = sum(max(p) for p in px) / (len(px) * 255) < 0.35
            outlined = _outlined_style(rgb)
    except Exception as exc:
        raise ProbeRejected(f"图片打不开：{exc}", "UNREADABLE_INPUT") from exc

    long_side = max(w, h)
    details = {"longSide": long_side, "darkBackground": dark, "outlinedWalls": outlined}
    if long_side < 600:
        raise ProbeRejected(
            f"图片太小（{w}×{h}）：尺寸标注会糊成一片，请上传长边 ≥ 1200px 的图", "TOO_SMALL"
        )
    tier, reason = "ok", ""
    if long_side < 1200:
        tier = "risky"
        reason = "分辨率偏低，尺寸刻度可能读不准"
    return Probe(tier=tier, kind="image_dark" if dark else "image", reason=reason,
                 width=w, height=h, details=details)


def _pdf_info(path: Path) -> Probe:
    if pymupdf is None:
        raise ProbeRejected("服务端缺少 pymupdf，暂时无法处理 PDF", "PDF_UNSUPPORTED")
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ProbeRejected(f"PDF 打不开：{exc}", "UNREADABLE_INPUT") from exc
    pages = doc.page_count
    if pages == 0:
        raise ProbeRejected("PDF 没有页面", "UNREADABLE_INPUT")
    if pages > 6:
        raise ProbeRejected(f"PDF 有 {pages} 页，无法判断哪一页是目标户型，请先裁成单页", "TOO_MANY_PAGES")

    page = doc[0]
    try:
        drawings = len(page.get_drawings())
    except Exception:
        drawings = 0
    text = page.get_text("text") or ""
    digits = sum(ch.isdigit() for ch in text)
    rect = page.rect
    details = {"drawings": drawings, "textChars": len(text.strip()), "digitChars": digits,
               "pageSize": [round(rect.width), round(rect.height)], "pages": pages}

    if drawings >= 300:
        return Probe(tier="ok", kind="pdf_vector", reason="检测到矢量图元，按 CAD 图纸处理",
                     width=int(rect.width), height=int(rect.height), pages=pages, details=details)

    tier, reason = "risky", "PDF 没有矢量图元，按位图识别，精度取决于图片分辨率"
    return Probe(tier=tier, kind="pdf_raster", reason=reason,
                 width=int(rect.width), height=int(rect.height), pages=pages, details=details)


def _dxf_info(path: Path) -> Probe:
    if ezdxf is None:
        raise ProbeRejected("服务端缺少 ezdxf，暂时无法处理 DXF", "DXF_UNSUPPORTED")
    try:
        doc = ezdxf.readfile(str(path))
        entities = sum(1 for _ in doc.modelspace())
    except Exception as exc:
        raise ProbeRejected(f"DXF 解析失败：{exc}", "UNREADABLE_INPUT") from exc
    if entities < 20:
        raise ProbeRejected("DXF 里几乎没有实体，可能不是平面图", "EMPTY_DXF")
    return Probe(tier="ok", kind="dxf", reason="", details={"entities": entities})


def probe(path: Path) -> Probe:
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"):
        return _image_info(path)
    if suffix == ".pdf":
        return _pdf_info(path)
    if suffix == ".dxf":
        return _dxf_info(path)
    if suffix == ".dwg":
        raise ProbeRejected("DWG 是二进制格式，服务端无法解析，请另存为 DXF 或 PDF 后重试", "DWG_UNSUPPORTED")
    raise ProbeRejected(f"不支持的文件类型 {suffix or '（无扩展名）'}", "UNSUPPORTED_TYPE")


def probe_json(path: Path) -> dict:
    return probe(path).to_dict()


if __name__ == "__main__":  # 方便手工验证
    import sys
    print(json.dumps(probe_json(Path(sys.argv[1])), ensure_ascii=False, indent=2))
