"""FPGA 寄存器 debug 工具的运行时数据目录。

仓库内 seed/ 保存初始内容（寄存器定义、模块说明等），首次启动时复制到运行时
目录。此后所有在线编辑都只写运行时目录，仓库副本保持为可回滚的基线。
"""

from __future__ import annotations

import shutil
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent / "seed"
SEEDED_SUBDIRS = ("regdefs", "vpu_overview", "intro_content", "module_info", "ivdc_config")


class Storage:
    """按数据根目录解析工具需要的各个子目录。"""

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root).resolve()

    def initialize(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        for name in SEEDED_SUBDIRS:
            target = self.data_root / name
            if target.exists():
                continue
            source = SEED_DIR / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.mkdir(parents=True, exist_ok=True)

    def subdir(self, name: str) -> Path:
        path = self.data_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def regdefs_dir(self) -> Path:
        return self.subdir("regdefs")

    @property
    def vpu_overview_dir(self) -> Path:
        return self.subdir("vpu_overview")

    @property
    def intro_content_dir(self) -> Path:
        return self.subdir("intro_content")

    @property
    def module_info_dir(self) -> Path:
        return self.subdir("module_info")

    @property
    def ivdc_config_dir(self) -> Path:
        return self.subdir("ivdc_config")

    @property
    def users_db_file(self) -> Path:
        return self.data_root / "users.db"
