"""岗位介绍页内容管理器 —— JSON 文件持久化，支持管理员在线编辑。"""

from __future__ import annotations

import json
from pathlib import Path

ROLES = ("product", "software", "hardware")

# 默认内容
DEFAULTS = {
    "product": {
        "title": "🏢 产品人员",
        "sections": [
            {
                "heading": "工具用途",
                "content": "<p>当客户遇到芯片挂住或异常问题时，可让客户通过串口或调试接口打印芯片寄存器信息。设计与验证人员将寄存器定义录入本工具后，上传客户提供的寄存器 dump 文件，即可<b>自动解析并标记异常点</b>，快速定位问题根因。</p>"
            },
            {
                "heading": "典型使用场景",
                "content": "<ul><li>客户反馈<b>编码画面卡住</b> → 打印 VPU/IVDC 寄存器 → 导入工具 → 快速定位异常模块</li><li>客户反馈<b>ISP 图像异常</b> → 打印 ISP 寄存器 → 对比正常值 → 发现配置偏差</li></ul>"
            },
            {
                "heading": "您需要做的",
                "content": "<ul><li>协调客户提供芯片寄存器 dump 日志</li><li>将 dump 文件和问题描述同步给 IPT 软件/设计与验证同事</li></ul>"
            }
        ]
    },
    "software": {
        "title": "💻 软件人员",
        "sections": [
            {
                "heading": "工具用途",
                "content": "<p>产品人员反馈客户寄存器和问题现象后，软件人员通过本工具导入寄存器 dump 文件，自动完成 <b>bit 级字段解析、多 dump 对比、异常值筛查</b>，快速定位出问题的模块，并转交设计与验证同事进行详细分析。</p>"
            },
            {
                "heading": "使用流程",
                "content": "<ol><li>确认设计与验证同事已在对应芯片目录下录入相关模块的寄存器定义（含正常值/预期值）</li><li>将客户提供的 dump 文件上传到工具中（支持同时上传多个 dump 对比）</li><li>点击「解析」，自动完成 bit 字段拆解和异常标记</li><li>查看「疑似异常点」段落，重点关注标红的字段</li><li>导出 Excel 报告，附上分析结论转交设计与验证同事</li></ol>"
            },
            {
                "heading": "软件人员也可参与的维护工作",
                "content": "<ul><li>根据客户问题支持经验，补充常见故障寄存器的「正常值」</li><li>协助设计与验证人员完善寄存器描述和枚举映射，提升可读性</li><li>遇到新的未定义寄存器时，提醒设计与验证同事补充到工具中</li></ul>"
            }
        ]
    },
    "hardware": {
        "title": "🔧 设计与验证人员",
        "sections": [
            {
                "heading": "您的核心职责",
                "content": "<p>设计与验证人员是寄存器定义的第一责任人。您需要将芯片各模块的寄存器规格录入到本工具中，包括每位字段的描述、枚举值映射，以及关键的<b>正常值/预期值</b>。这些信息是软件人员进行问题初步筛查的基础。</p>"
            },
            {
                "heading": "如何录入寄存器",
                "content": "<ol><li>选择对应的芯片类型（T42 等）</li><li>选择模块（IVDC / CFGC 等）</li><li>点击「+ 寄存器录入」，填写：<ul><li><b>模块名字</b>：与 dump 文件中 ::: 前的模块名一致</li><li><b>寄存器名字</b>：助记名，如 CTRL_REG</li><li><b>地址偏移</b>：含基地址的完整地址，如 0x02B00000</li><li><b>Bit 字段定义</b>：每位的含义，多 bit 字段需填写枚举映射</li><li><b>正常值</b>：该字段在正常工作状态下的预期值（重要！）</li></ul></li></ol>"
            },
            {
                "heading": "注意事项",
                "content": "<ul><li>尽可能增加文字性描述，让软件人员一目了然</li><li>对有明确正常值的寄存器位（如使能位 = 1、状态位 = 0），务必填写「正常值」</li><li>填写正常值后，工具会自动比对 dump 值与预期值，不一致时标记为疑似异常</li><li>不同芯片的寄存器定义相互隔离，请在正确的芯片目录下操作</li></ul>"
            }
        ]
    }
}


def _filepath(content_dir: Path, role: str) -> Path:
    return Path(content_dir) / f"{role}.json"


def load_content(content_dir: Path, role: str) -> dict:
    """加载指定岗位的介绍内容，若无自定义内容则返回默认。"""
    if role not in ROLES:
        return {"title": "", "sections": []}
    filepath = _filepath(content_dir, role)
    if filepath.is_file():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULTS.get(role, {"title": "", "sections": []})


def save_content(content_dir: Path, role: str, data: dict) -> None:
    """保存编辑后的介绍内容。"""
    if role not in ROLES:
        raise ValueError(f"unknown intro role: {role}")
    content_dir = Path(content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)
    with open(_filepath(content_dir, role), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
