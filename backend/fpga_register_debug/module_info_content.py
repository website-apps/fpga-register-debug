"""模块说明页内容管理器 —— JSON 文件持久化，支持管理员在线富文本编辑。"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_HTML = '<p style="font-size:16px;color:#5a7a9a;">待填写</p><p style="font-size:14px;color:#a0b8d0;">模块说明内容将在后续补充</p>'


def _filepath(content_dir: Path, module_name: str) -> Path:
    safe = "".join(c for c in module_name if c.isalnum() or c in "_-")
    return Path(content_dir) / f"{safe}.json"


def load_content(content_dir: Path, module_name: str) -> str:
    """加载模块说明 HTML 内容，无自定义内容则返回默认。"""
    fp = _filepath(content_dir, module_name)
    if fp.is_file():
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("html", DEFAULT_HTML)
    return DEFAULT_HTML


def save_content(content_dir: Path, module_name: str, html: str) -> None:
    """保存编辑后的模块说明 HTML 内容。"""
    content_dir = Path(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    with open(_filepath(content_dir, module_name), "w", encoding="utf-8") as f:
        json.dump({"html": html}, f, ensure_ascii=False, indent=2)
