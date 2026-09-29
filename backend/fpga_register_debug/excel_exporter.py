"""Excel 导出模块 —— 将解析结果导出为 .xlsx 文件，保留颜色高亮。"""

import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


# 颜色定义
FILL_DIFF = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")   # 差异高亮：黄色
FILL_ZERO = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")   # 全 0：灰色
FILL_ALL_F = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")   # 全 F：红色
FILL_HEADER = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")  # 表头：蓝色
FONT_HEADER = Font(color="FFFFFF", bold=True)
FONT_NORMAL = Font(color="000000")
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
CENTER_ALIGN = Alignment(horizontal="center", vertical="center")
LEFT_ALIGN = Alignment(horizontal="left", vertical="center")


def _is_all_zero(val: int, width: int) -> bool:
    return val == 0


def _is_all_f(val: int, width: int) -> bool:
    mask = (1 << width) - 1
    return (val & mask) == mask


def _format_field_value(field_val: int, width: int) -> str:
    """格式化字段值为十六进制字符串。"""
    if width == 1:
        return str(field_val)  # 单 bit 直接用 0/1
    return f"0x{field_val:0{(width + 3) // 4}X}"


def export_to_excel(parsed_results: list[dict],
                    dump_names: list[str],
                    abnormal_only: bool = False) -> bytes:
    """
    将解析结果导出为 Excel 文件。

    Args:
        parsed_results: [
            {
                "module": "IVDC",
                "address_offset": "0x13100224",
                "reg_name": "CTRL_REG",
                "bitfields": [...],
                "dump_values": [0x00000000, 0x00000001, ...],  # 每个 dump 的原始值
            },
            ...
        ]
        dump_names: ["Dump1.txt", "Dump2.txt", ...]

    Returns:
        .xlsx 文件的 bytes
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "异常寄存器报告" if abnormal_only else "寄存器解析结果"

    # ---- 表头 ----
    headers = ["模块", "地址偏移", "寄存器名", "Bit范围", "字段名"]
    if abnormal_only:
        headers.append("期望值")
    # 记录值起始列号
    value_start_col = len(headers) + 1  # 5 或 6
    for dname in dump_names:
        headers.append(f"{dname} 值")
    if len(dump_names) > 1:
        headers.append("是否变化")
    headers.append("枚举含义")

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = CENTER_ALIGN
        cell.border = THIN_BORDER

    # ---- 数据行 ----
    row = 2
    for result in parsed_results:
        module = result.get("module", "")
        address = result.get("address_offset", "")
        reg_name = result.get("reg_name", "")
        dump_values = result.get("dump_values", [])

        for bf in result.get("bitfields", []):
            start = bf["start_bit"]
            end = bf["end_bit"]
            width = end - start + 1
            bit_range = f"[{end}:{start}]" if end > start else f"[{start}]"

            col = 1
            ws.cell(row=row, column=col, value=module).border = THIN_BORDER; col += 1
            ws.cell(row=row, column=col, value=address).border = THIN_BORDER; col += 1
            ws.cell(row=row, column=col, value=reg_name).border = THIN_BORDER; col += 1
            cell = ws.cell(row=row, column=col, value=bit_range)
            cell.border = THIN_BORDER
            cell.alignment = CENTER_ALIGN; col += 1
            ws.cell(row=row, column=col, value=bf.get("field_name", "")).border = THIN_BORDER; col += 1

            if abnormal_only:
                expected = bf.get("expected_value", "")
                ws.cell(row=row, column=col, value=expected).border = THIN_BORDER
                ws.cell(row=row, column=col).alignment = CENTER_ALIGN; col += 1

            field_values = []
            value_col_start = col
            for di, dval in enumerate(dump_values):
                # 该 dump 里没有这个寄存器时留空
                if dval is None:
                    field_values.append(None)
                    cell = ws.cell(row=row, column=col + di, value="")
                else:
                    # 提取该 bit 段的值
                    mask = ((1 << width) - 1) << start
                    fv = (dval & mask) >> start
                    field_values.append(fv)
                    cell = ws.cell(row=row, column=col + di, value=_format_field_value(fv, width))
                cell.border = THIN_BORDER
                cell.alignment = CENTER_ALIGN
            col += len(dump_values)

            # 差异检测
            if len(dump_names) > 1:
                changed = len(set(field_values)) > 1
                ws.cell(row=row, column=col,
                        value="是" if changed else "否").border = THIN_BORDER
                ws.cell(row=row, column=col).alignment = CENTER_ALIGN

                if changed:
                    # 高亮所有该行的值列
                    for ci in range(value_col_start, col + 1):
                        ws.cell(row=row, column=ci).fill = FILL_DIFF
                col += 1

            # 枚举含义
            enum_meaning = bf.get("enum_meaning", "")
            if enum_meaning:
                ws.cell(row=row, column=col, value=enum_meaning).border = THIN_BORDER

            # 全 0 / 全 F 高亮
            for di, fv in enumerate(field_values):
                if fv is None:
                    continue
                cell = ws.cell(row=row, column=value_col_start + di)
                if _is_all_zero(fv, width):
                    cell.fill = FILL_ZERO
                elif _is_all_f(fv, width):
                    cell.fill = FILL_ALL_F

            # 异常模式：整行标黄
            if abnormal_only:
                for ci in range(1, col + 1):
                    check_cell = ws.cell(row=row, column=ci)
                    if check_cell.value is not None:
                        check_cell.fill = FILL_DIFF

            row += 1

    # ---- 列宽自适应 ----
    col_widths = {
        1: 12, 2: 16, 3: 18, 4: 12, 5: 22,
    }
    if abnormal_only:
        col_widths[6] = 12  # 期望值列
    for ci, w in col_widths.items():
        ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = w
    # 值列
    for di in range(len(dump_names)):
        cl = ws.cell(row=1, column=value_start_col + di).column_letter
        ws.column_dimensions[cl].width = 16
    # 变化列
    diff_start = value_start_col + len(dump_names)
    if len(dump_names) > 1:
        cl = ws.cell(row=1, column=diff_start).column_letter
        ws.column_dimensions[cl].width = 10
    # 枚举列
    enum_col_idx = diff_start + (1 if len(dump_names) > 1 else 0)
    cl = ws.cell(row=1, column=enum_col_idx).column_letter
    ws.column_dimensions[cl].width = 20

    # 冻结首行
    ws.freeze_panes = "A2"

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()
