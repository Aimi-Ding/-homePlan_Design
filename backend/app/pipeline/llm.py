"""模型接入层：只依赖 OpenAI 兼容的 /chat/completions，方便换供应商。"""
from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .. import config


class LLMError(Exception):
    pass


def available() -> bool:
    return config.llm_enabled()


def _image_data_url(path: Path, max_side: int = 1400) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        if max(im.size) > max_side:
            k = max_side / max(im.size)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _parse_content(content: Any) -> dict:
    if isinstance(content, list):  # 部分供应商返回分段内容
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        raise LLMError("模型返回内容为空")
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"模型没有返回 JSON：{text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型返回的 JSON 解析失败：{exc}") from exc


def complete_json(system: str, user: str, images: list[Path] | None = None) -> dict:
    if not available():
        raise LLMError("未配置模型 key")
    content: list[dict] = [{"type": "text", "text": user}]
    for p in images or []:
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(p)}})

    payload = {
        "model": config.LLM_MODEL,
        "temperature": 0.1,
        "max_tokens": 8000,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}
    url = f"{config.LLM_BASE_URL}/chat/completions"

    last: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=config.LLM_TIMEOUT) as client:
                resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 400 and "response_format" in resp.text:
                payload.pop("response_format", None)
                continue
            if resp.status_code >= 500 or resp.status_code == 429:
                raise LLMError(f"模型服务暂时不可用（HTTP {resp.status_code}）")
            if resp.status_code != 200:
                raise LLMError(f"模型调用失败（HTTP {resp.status_code}）：{resp.text[:300]}")
            data = resp.json()
            return _parse_content(data["choices"][0]["message"].get("content"))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
        except LLMError as exc:
            last = exc
            if "暂时不可用" in str(exc):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise LLMError(f"模型调用失败：{last}")
