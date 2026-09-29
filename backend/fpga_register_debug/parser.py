"""FPGA Dump 文件解析器。

支持格式:
    [2026-07-13 16:27:23] IVDC:::0x13100224 = 0x00000000
解析规则:
    - 忽略行首的时间戳
    - 通过 ::: 定位模块名和地址偏移
    - 通过 = 提取寄存器值
"""

import re
from typing import Optional

# 匹配 dump 行的正则表达式
# 格式: [可选时间戳] 模块名:::地址偏移 = 寄存器值
_DUMP_LINE_RE = re.compile(
    r"(?:\[.*?\]\s*)?"   # 可选时间戳 [YYYY-MM-DD HH:MM:SS]
    r"(\S+)"             # 模块名（无空格）
    r":::"               # ::: 分隔符
    r"(\S+)"             # 地址偏移（如 0x13100224）
    r"\s*=\s*"           # = 号（前后允许空格）
    r"(\S+)"             # 寄存器值（如 0x00000000）
)


def _parse_hex(s: str) -> Optional[int]:
    """将十六进制字符串解析为 int，失败返回 None。"""
    s = s.strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    try:
        return int(s, 16)
    except ValueError:
        return None


def _extract_bitfield_value(reg_value: int, start_bit: int, end_bit: int) -> int:
    """从 32 bit 寄存器值中提取指定 bit 范围的值。"""
    mask = ((1 << (end_bit - start_bit + 1)) - 1) << start_bit
    return (reg_value & mask) >> start_bit


def parse_dump_line(line: str) -> Optional[dict]:
    """
    解析单行 dump 文本。
    返回: {"module": str, "address_offset": str, "raw_value": str, "value_int": int}
    解析失败: None
    """
    line = line.strip()
    if not line:
        return None

    m = _DUMP_LINE_RE.match(line)
    if not m:
        return None

    module_name = m.group(1)
    address_str = m.group(2)
    value_str = m.group(3)

    value_int = _parse_hex(value_str)
    if value_int is None:
        return None

    return {
        "module": module_name,
        "address_offset": address_str.upper(),
        "raw_value": value_str,
        "value_int": value_int,
    }


def parse_dump_file(content: str) -> list[dict]:
    """
    解析整个 dump 文件内容，返回解析后的寄存器行列表。
    """
    results = []
    for line in content.splitlines():
        parsed = parse_dump_line(line)
        if parsed:
            results.append(parsed)
    return results


def resolve_bitfields(reg_value: int, bitfields: list[dict]) -> list[dict]:
    """
    根据寄存器值 + bit 字段定义，解析出每个字段的实际值。

    Args:
        reg_value: 32 bit 寄存器整数值
        bitfields: 位字段定义列表
    Returns:
        [{start_bit, end_bit, field_name, field_value, enum_meaning?}, ...]
    """
    results = []
    for bf in bitfields:
        start = bf["start_bit"]
        end = bf["end_bit"]
        field_val = _extract_bitfield_value(reg_value, start, end)
        entry = {
            "start_bit": start,
            "end_bit": end,
            "field_name": bf.get("field_name", ""),
            "field_value": field_val,
        }
        # 枚举匹配
        enums = bf.get("enums", [])
        if enums:
            for enum_item in enums:
                ev = _parse_hex(enum_item.get("value", ""))
                if ev is not None and ev == field_val:
                    entry["enum_meaning"] = enum_item.get("meaning", "")
                    break
        results.append(entry)
    return results
