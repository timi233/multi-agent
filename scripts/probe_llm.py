#!/usr/bin/env python3
"""探测 cliproxy 当前可用的模型（只做一次最小 chat）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.runtime.gateway import Gateway

CANDIDATES = ["gpt-5.6-luna", "gpt-5.5", "deepseek-v4-flash", "deepseek-v4-pro", "qwen3-vl:8b", "glm-ocr"]

gw = Gateway(settings.cliproxy_base_url, settings.cliproxy_api_key, "probe", timeout=30)
for model in CANDIDATES:
    gw.model = model
    try:
        choice = gw.chat([{"role": "user", "content": "只回复两个字：可用"}], max_tokens=16, retries=0)
        print(f"{model:22s} OK   -> {str(choice.get('message', {}).get('content', ''))[:40]!r}")
    except Exception as exc:
        print(f"{model:22s} FAIL -> {str(exc)[:120]}")