"""IVDC 配置指导内容管理器 —— 按 step 分段管理。"""

from __future__ import annotations

import json
from pathlib import Path

SECTION_FILENAME = "guide_sections.json"


def load_sections(content_dir: Path) -> list[dict]:
    """加载所有 step 分段，每段含 heading 和 content (HTML)。"""
    section_file = Path(content_dir) / SECTION_FILENAME
    if section_file.is_file():
        with open(section_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_sections(content_dir: Path, sections: list[dict]) -> None:
    """保存所有 step 分段。"""
    content_dir = Path(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    with open(content_dir / SECTION_FILENAME, "w", encoding="utf-8") as f:
        json.dump(sections, f, ensure_ascii=False, indent=2)
