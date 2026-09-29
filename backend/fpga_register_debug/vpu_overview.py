"""VPU / ISP 各模块概述内容管理器 —— 按芯片独立存储。"""

from __future__ import annotations

import json
from pathlib import Path

# 旧全局文件（用于一次性迁移）
LEGACY_FILENAME = "overviews.json"


def _safe_segment(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in "_-")


def _overview_file(content_dir: Path, chip: str, module_type: str = "VPU") -> Path:
    """获取某芯片某类型模块的概述文件路径。

    module_type: "VPU" 或 "ISP"
    """
    chip = _safe_segment(chip)
    if module_type == "ISP":
        return Path(content_dir) / f"{chip}_isp_overviews.json"
    return Path(content_dir) / f"{chip}_overviews.json"


def load_overviews(content_dir: Path, chip: str = "", module_type: str = "VPU") -> dict[str, dict]:
    """
    加载模块概述（按芯片独立，按模块类型区分）。

    module_type: "VPU" 或 "ISP"

    兼容旧格式（值为纯字符串）和新格式（值为含 desc/updated_by/updated_at 的字典）。
    返回统一的新格式：{mod: {desc, updated_by, updated_at}}
    """
    content_dir = Path(content_dir)
    filepath = _overview_file(content_dir, chip, module_type)
    legacy_file = content_dir / LEGACY_FILENAME

    # 兼容旧数据：如果芯片文件不存在但旧全局文件存在，则从旧文件迁移
    if not filepath.is_file() and legacy_file.is_file() and module_type == "VPU":
        data = _load_json(legacy_file)
        if data:
            content_dir.mkdir(parents=True, exist_ok=True)
            _save_json(filepath, data)

    return _load_json(filepath)


def _load_json(filepath: Path) -> dict[str, dict]:
    """加载并标准化 JSON 文件。"""
    if not filepath.is_file():
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for mod, val in data.items():
        if isinstance(val, str):
            result[mod] = {"desc": val, "updated_by": "", "updated_at": ""}
        else:
            result[mod] = {
                "desc": val.get("desc", ""),
                "updated_by": val.get("updated_by", ""),
                "updated_at": val.get("updated_at", ""),
            }
    return result


def _save_json(filepath: Path, data: dict[str, dict]) -> None:
    """保存 JSON 文件。"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_desc(overviews: dict, mod: str) -> str:
    """从概述数据中提取模块描述文本。"""
    info = overviews.get(mod, {})
    return info.get("desc", "") if isinstance(info, dict) else info


def save_overviews(content_dir: Path, chip: str, data: dict[str, dict], module_type: str = "VPU") -> None:
    """保存某芯片的模块概述（新格式）。

    module_type: "VPU" 或 "ISP"
    """
    content_dir = Path(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    _save_json(_overview_file(content_dir, chip, module_type), data)
