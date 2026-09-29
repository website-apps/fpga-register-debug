"""寄存器定义管理器 —— 负责 JSON 文件的读取、写入、CRUD 操作。

按芯片和模块组分目录管理：{root}/{chip}/{group}/{module}.json
group = ISP / IVDC / VPU
"""

from __future__ import annotations

import json
from pathlib import Path


def _safe_segment(value: str) -> str:
    """芯片名和模块组名来自 URL，去掉分隔符避免逃出数据根目录。"""
    return "".join(c for c in value if c.isalnum() or c in "_-")


def _safe_filename(module_name: str) -> str:
    """将模块名转换为安全的文件名（去除空格和特殊字符）。"""
    return _safe_segment(module_name) + ".json"


def _group_dir(root: Path, chip: str, group: str) -> Path:
    return Path(root) / _safe_segment(chip) / _safe_segment(group)


def _module_filepath(root: Path, chip: str, group: str, module_name: str) -> Path:
    return _group_dir(root, chip, group) / _safe_filename(module_name)


# --------------------- 读取 / 列出 ---------------------

def list_modules(root: Path, chip: str, group: str = "") -> list[str]:
    """列出某芯片某组下所有已定义的模块名（去掉 .json 后缀）。

    如果 group 为空，则扫描芯片目录下所有子目录。
    """
    if group:
        gdir = _group_dir(root, chip, group)
        if not gdir.is_dir():
            return []
        return sorted(f.stem for f in gdir.iterdir() if f.suffix == ".json")

    chip_dir = Path(root) / _safe_segment(chip)
    if not chip_dir.is_dir():
        return []
    modules = []
    for sub in chip_dir.iterdir():
        if sub.is_dir():
            modules.extend(f.stem for f in sub.iterdir() if f.suffix == ".json")
    return sorted(modules)


def list_chips_with_modules(root: Path) -> dict[str, list[str]]:
    """列出所有芯片及其下的模块组（ISP/IVDC/VPU等）。"""
    result: dict[str, list[str]] = {}
    root = Path(root)
    if not root.is_dir():
        return result
    for chip_dir in sorted(root.iterdir()):
        if not chip_dir.is_dir():
            continue
        groups = sorted(sub.name for sub in chip_dir.iterdir() if sub.is_dir())
        if groups:
            result[chip_dir.name] = groups
    return result


def load_module(root: Path, chip: str, group: str, module_name: str) -> dict:
    """加载某个模块的寄存器定义，返回 dict。"""
    filepath = _module_filepath(root, chip, group, module_name)
    if not filepath.is_file():
        return {"module": module_name, "registers": []}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------- 写入 ---------------------

def save_module(root: Path, chip: str, group: str, module_data: dict) -> None:
    """保存某个模块的寄存器定义到 JSON 文件。"""
    module_name = module_data.get("module", "unknown")
    filepath = _module_filepath(root, chip, group, module_name)
    _group_dir(root, chip, group).mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(module_data, f, ensure_ascii=False, indent=2)


# --------------------- 寄存器 CRUD ---------------------

def _normalize_address(address_offset: str) -> str:
    """统一地址偏移格式：去掉空格，标准化为 0x 前缀 + 大写十六进制。"""
    addr = address_offset.strip().replace(" ", "")
    if addr.lower().startswith("0x"):
        return "0x" + addr[2:].upper()
    return "0x" + addr.upper()


def add_register(root: Path,
                 chip: str,
                 group: str,
                 module_name: str,
                 reg_name: str,
                 address_offset: str,
                 bitfields: list[dict],
                 updated_by: str = "") -> dict:
    """
    向模块添加一条寄存器定义。

    bitfields 格式: [{"start_bit": 0, "end_bit": 0, "field_name": "enable",
                      "enums": [{"value": "0x0", "meaning": "IDLE"}, ...]}, ...]
    返回更新后的模块数据。
    """
    module_data = load_module(root, chip, group, module_name)
    addr = _normalize_address(address_offset)

    # 自动补齐：如果地址不足8位hex，从已有寄存器推导基地址
    hex_part = addr[2:]
    if len(hex_part) < 8:
        for reg in module_data.get("registers", []):
            ref = reg.get("address_offset", "").replace(" ", "")
            ref_hex = ref[2:].upper() if ref.lower().startswith("0x") else ref.upper()
            if len(ref_hex) >= 8:
                prefix_len = len(ref_hex) - len(hex_part)
                if prefix_len > 0:
                    addr = "0x" + ref_hex[:prefix_len] + hex_part
                break

    # 如果已有同地址偏移的寄存器，先删除旧的（大小写不敏感）
    addr_upper = addr.upper()
    module_data["registers"] = [
        r for r in module_data.get("registers", [])
        if r.get("address_offset", "").strip().replace(" ", "").upper() != addr_upper
    ]

    # 清理 bitfields：确保 enums 存在（多 bit 字段才保留枚举）
    clean_bitfields = []
    for bf in bitfields:
        start = bf["start_bit"]
        end = bf["end_bit"]
        entry = {
            "start_bit": start,
            "end_bit": end,
            "field_name": bf.get("field_name", ""),
        }
        if bf.get("expected_value"):
            entry["expected_value"] = bf["expected_value"]
        if bf.get("abnormal_value"):
            entry["abnormal_value"] = bf["abnormal_value"]
        if start != end and bf.get("enums"):
            entry["enums"] = bf["enums"]
        clean_bitfields.append(entry)

    module_data.setdefault("module", module_name)
    module_data.setdefault("registers", []).append({
        "name": reg_name,
        "address_offset": addr,
        "bitfields": clean_bitfields,
        "updated_by": updated_by,
    })

    save_module(root, chip, group, module_data)
    return module_data


def delete_register(root: Path, chip: str, group: str, module_name: str, address_offset: str) -> dict:
    """删除某个寄存器定义。"""
    module_data = load_module(root, chip, group, module_name)
    addr = address_offset.strip().replace(" ", "").upper()
    module_data["registers"] = [
        r for r in module_data.get("registers", [])
        if r.get("address_offset", "").strip().replace(" ", "").upper() != addr
    ]
    save_module(root, chip, group, module_data)
    return module_data


def import_json(root: Path, chip: str, group: str, file_content: str, updated_by: str = "") -> dict:
    """导入 JSON 内容（可以是单模块或多模块），保存到指定芯片组目录。"""
    data = json.loads(file_content)
    modules = data if isinstance(data, list) else [data]
    for m in modules:
        for reg in m.get("registers", []):
            if "updated_by" not in reg:
                reg["updated_by"] = updated_by
        save_module(root, chip, group, m)
    return {"modules_saved": [m.get("module", "unknown") for m in modules]}
