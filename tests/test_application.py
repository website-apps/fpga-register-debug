import io
import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask
from openpyxl import load_workbook

from backend.fpga_register_debug import regdef_manager
from backend.fpga_register_debug.auth import UserStore
from backend.fpga_register_debug.blueprint import DEFAULT_URL_PREFIX, create_blueprint
from backend.fpga_register_debug.parser import parse_dump_file, resolve_bitfields
from backend.fpga_register_debug.storage import SEEDED_SUBDIRS, Storage

from test_support import connect_sqlite
import backend.fpga_register_debug.auth as auth
auth.connect_database = connect_sqlite

MODULE_DEFINITION = {
    "module": "CFGC",
    "registers": [
        {
            "name": "CFGC_GLB_CTRL",
            "address_offset": "0x02B00000",
            "bitfields": [
                {
                    "start_bit": 0,
                    "end_bit": 1,
                    "field_name": "clke：使能时钟",
                    "enums": [
                        {"value": "0x0", "meaning": "时钟关闭"},
                        {"value": "0x3", "meaning": "全部时钟使能"},
                    ],
                    "expected_value": "0x3",
                },
                {"start_bit": 2, "end_bit": 2, "field_name": "rst：软件复位"},
            ],
        }
    ],
}


class ParserTests(unittest.TestCase):
    def test_parses_lines_with_and_without_timestamp(self) -> None:
        content = "\n".join(
            [
                "[2026-08-19 10:00:00] IVDC:::0x13100224 = 0x0000000F",
                "CFGC:::0x02B00000=0x00000003",
                "not a register line",
                "",
            ]
        )
        parsed = parse_dump_file(content)
        self.assertEqual(
            [(item["module"], item["address_offset"], item["value_int"]) for item in parsed],
            [("IVDC", "0X13100224", 15), ("CFGC", "0X02B00000", 3)],
        )

    def test_resolve_bitfields_extracts_values_and_enum_meaning(self) -> None:
        resolved = resolve_bitfields(0b111, MODULE_DEFINITION["registers"][0]["bitfields"])
        self.assertEqual(resolved[0]["field_value"], 3)
        self.assertEqual(resolved[0]["enum_meaning"], "全部时钟使能")
        self.assertEqual(resolved[1]["field_value"], 1)
        self.assertNotIn("enum_meaning", resolved[1])


class StorageTests(unittest.TestCase):
    def test_initialize_seeds_content_once_and_keeps_edits(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        storage = Storage(Path(tempdir.name))
        storage.initialize()

        for name in SEEDED_SUBDIRS:
            self.assertTrue((storage.data_root / name).is_dir(), name)

        marker = storage.regdefs_dir / "T42M" / "VPU" / "CFGC.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}")
        self.assertTrue(marker.is_file())
        marker.write_text('{"module": "CFGC", "registers": []}', encoding="utf-8")

        # 二次启动不能覆盖用户改过的内容
        Storage(Path(tempdir.name)).initialize()
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["registers"], [])


class RegdefManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def test_add_normalizes_address_and_replaces_same_offset(self) -> None:
        regdef_manager.add_register(
            self.root, "T42M", "VPU", "CFGC", "CTRL", " 0x02b00000 ", [], updated_by="张三"
        )
        regdef_manager.add_register(
            self.root, "T42M", "VPU", "CFGC", "CTRL_V2", "0X02B00000", [], updated_by="李四"
        )

        registers = regdef_manager.load_module(self.root, "T42M", "VPU", "CFGC")["registers"]
        self.assertEqual(len(registers), 1)
        self.assertEqual(registers[0]["address_offset"], "0x02B00000")
        self.assertEqual(registers[0]["name"], "CTRL_V2")
        self.assertEqual(registers[0]["updated_by"], "李四")

    def test_short_address_inherits_base_from_existing_register(self) -> None:
        regdef_manager.add_register(self.root, "T42M", "VPU", "CFGC", "BASE", "0x02B00000", [])
        regdef_manager.add_register(self.root, "T42M", "VPU", "CFGC", "NEAR", "0x40", [])

        addresses = {
            reg["address_offset"]
            for reg in regdef_manager.load_module(self.root, "T42M", "VPU", "CFGC")["registers"]
        }
        self.assertEqual(addresses, {"0x02B00000", "0x02B00040"})

    def test_delete_and_list(self) -> None:
        regdef_manager.import_json(self.root, "T42M", "VPU", json.dumps(MODULE_DEFINITION))
        self.assertEqual(regdef_manager.list_modules(self.root, "T42M", "VPU"), ["CFGC"])
        self.assertEqual(
            regdef_manager.list_chips_with_modules(self.root), {"T42M": ["VPU"]}
        )

        regdef_manager.delete_register(self.root, "T42M", "VPU", "CFGC", "0x02B00000")
        self.assertEqual(
            regdef_manager.load_module(self.root, "T42M", "VPU", "CFGC")["registers"], []
        )

    def test_chip_and_group_cannot_escape_the_data_root(self) -> None:
        regdef_manager.save_module(self.root, "../../etc", "..", {"module": "evil", "registers": []})
        written = list(self.root.rglob("evil.json"))
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].resolve().is_relative_to(self.root.resolve()))


class UserStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.users = UserStore(Path(self.tempdir.name) / "users.db")
        self.users.initialize()

    def test_registration_requires_approval_before_login(self) -> None:
        ok, _ = self.users.register_user("童文心", "software", "hunter2")
        self.assertTrue(ok)

        allowed, message, user = self.users.login_user("童文心", "hunter2")
        self.assertFalse(allowed)
        self.assertIn("审核", message)
        self.assertIsNone(user)

        pending = self.users.get_pending_users()
        self.assertEqual(len(pending), 1)
        self.users.approve_user(pending[0]["id"])

        allowed, _, user = self.users.login_user("童文心", "hunter2")
        self.assertTrue(allowed)
        self.assertEqual(user["role"], "software")

    def test_admin_role_cannot_self_register(self) -> None:
        ok, message = self.users.register_user("冒牌管理员", "admin", "hunter2")
        self.assertFalse(ok)
        self.assertIn("管理员", message)

    def test_bootstrap_admin_only_created_when_password_given(self) -> None:
        self.assertEqual(self.users.get_all_users(), [])
        self.users.initialize(bootstrap_admin_password="first-admin-pw")
        self.assertEqual([u["name"] for u in self.users.get_all_users()], ["admin"])

        # 已有管理员时不再重复创建
        self.users.initialize(bootstrap_admin_password="another-pw")
        self.assertEqual(len(self.users.get_all_users()), 1)
        allowed, _, _ = self.users.login_user("admin", "first-admin-pw")
        self.assertTrue(allowed)

    def test_password_reset_needs_admin_approval(self) -> None:
        self.users.register_user("测试用户", "hardware", "oldpass")
        self.users.approve_user(self.users.get_pending_users()[0]["id"])

        ok, _ = self.users.request_password_reset("测试用户", "newpass")
        self.assertTrue(ok)
        self.assertFalse(self.users.login_user("测试用户", "newpass")[0])

        self.users.approve_password_reset(self.users.get_reset_requests()[0]["id"])
        self.assertTrue(self.users.login_user("测试用户", "newpass")[0])


class BlueprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_root = Path(self.tempdir.name)

        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(
            create_blueprint(self.data_root, bootstrap_admin_password="admin-pw")
        )
        app.testing = True
        self.app = app
        self.client = app.test_client()

        self.users = UserStore(self.data_root / "users.db")
        for name, role in [("产品甲", "product"), ("硬件乙", "hardware")]:
            self.users.register_user(name, role, "hunter2")
        for pending in self.users.get_pending_users():
            self.users.approve_user(pending["id"])

        regdef_manager.import_json(
            self.data_root / "regdefs", "T42M", "VPU", json.dumps(MODULE_DEFINITION)
        )

    def url(self, path: str) -> str:
        return f"{DEFAULT_URL_PREFIX}{path}"

    def login(self, name: str, password: str = "hunter2") -> None:
        response = self.client.post(
            self.url("/login"), data={"name": name, "password": password}
        )
        self.assertEqual(response.status_code, 302)

    def test_pages_require_login(self) -> None:
        for path in ["/", "/chips", "/debug/T42M/VPU"]:
            response = self.client.get(self.url(path))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"], self.url("/login"))

    def test_admin_page_is_restricted_to_admin_role(self) -> None:
        self.login("硬件乙")
        self.assertEqual(self.client.get(self.url("/admin")).status_code, 403)

        self.client.get(self.url("/logout"))
        self.login("admin", "admin-pw")
        self.assertEqual(self.client.get(self.url("/admin")).status_code, 200)

    def test_debug_page_injects_url_prefix_for_the_frontend(self) -> None:
        self.login("硬件乙")
        response = self.client.get(self.url("/debug/T42M/VPU"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'base: "{DEFAULT_URL_PREFIX}"'.encode(), response.data)

    def test_product_role_cannot_edit_register_definitions(self) -> None:
        self.login("产品甲")
        response = self.client.post(
            self.url("/api/T42M/VPU/register"),
            json={"module_name": "CFGC", "reg_name": "X", "address_offset": "0x10", "bitfields": []},
        )
        self.assertEqual(response.status_code, 403)

    def test_overlapping_bitfields_are_rejected(self) -> None:
        self.login("硬件乙")
        response = self.client.post(
            self.url("/api/T42M/VPU/register"),
            json={
                "module_name": "CFGC",
                "reg_name": "OVERLAP",
                "address_offset": "0x20",
                "bitfields": [
                    {"start_bit": 0, "end_bit": 4, "field_name": "a"},
                    {"start_bit": 3, "end_bit": 6, "field_name": "b"},
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("交叉", response.get_json()["error"])

    def test_parse_marks_values_outside_the_expected_range(self) -> None:
        self.login("产品甲")
        dump = b"[2026-08-19 10:00:00] CFGC:::0x02B00000 = 0x00000004"
        response = self.client.post(
            self.url("/api/T42M/VPU/parse"),
            data={"dump_files": (io.BytesIO(dump), "dump1.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["dump_names"], ["dump1.txt"])

        clke, rst = payload["results"]
        self.assertEqual(clke["field_values"], [0])
        self.assertTrue(clke["is_abnormal"], "clke=0 与期望值 0x3 不符")
        self.assertEqual(rst["field_values"], [1])
        self.assertFalse(rst["is_abnormal"], "rst 没有期望值，不应判异常")

    def test_parse_reports_undefined_registers(self) -> None:
        self.login("产品甲")
        dump = b"CFGC:::0x02B0FFFF = 0x12345678"
        response = self.client.post(
            self.url("/api/T42M/VPU/parse"),
            data={"dump_files": (io.BytesIO(dump), "dump1.txt")},
            content_type="multipart/form-data",
        )
        results = response.get_json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["reg_name"], "[未定义寄存器]")

    def test_product_role_cannot_import_definitions_while_parsing(self) -> None:
        self.login("产品甲")
        response = self.client.post(
            self.url("/api/T42M/VPU/parse"),
            data={
                "dump_files": (io.BytesIO(b"CFGC:::0x02B00000 = 0x3"), "dump1.txt"),
                "regdef_files": (io.BytesIO(b"{}"), "defs.json"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 403)

    def test_parse_results_are_not_shared_between_sessions(self) -> None:
        self.login("产品甲")
        self.client.post(
            self.url("/api/T42M/VPU/parse"),
            data={"dump_files": (io.BytesIO(b"CFGC:::0x02B00000 = 0x3"), "dump1.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(self.client.post(self.url("/api/T42M/VPU/export_excel")).status_code, 200)

        other = self.app.test_client()
        other.post(self.url("/login"), data={"name": "硬件乙", "password": "hunter2"})
        response = other.post(self.url("/api/T42M/VPU/export_excel"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("请先解析", response.get_json()["error"])

    def test_export_abnormal_returns_a_workbook(self) -> None:
        self.login("产品甲")
        self.client.post(
            self.url("/api/T42M/VPU/parse"),
            data={"dump_files": (io.BytesIO(b"CFGC:::0x02B00000 = 0x0"), "dump1.txt")},
            content_type="multipart/form-data",
        )
        response = self.client.post(self.url("/api/T42M/VPU/export_abnormal_excel"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("abnormal_registers.xlsx", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"PK"))

    def test_export_writes_each_bitfield_value_not_just_the_first(self) -> None:
        # 0xA5 -> 低 4 位 lo = 0x5，高 4 位 hi = 0xA
        regdef_manager.import_json(
            self.data_root / "regdefs",
            "T42M",
            "VPU",
            json.dumps({
                "module": "SPLIT",
                "registers": [{
                    "name": "SPLIT_REG",
                    "address_offset": "0x00001000",
                    "bitfields": [
                        {"start_bit": 0, "end_bit": 3, "field_name": "lo"},
                        {"start_bit": 4, "end_bit": 7, "field_name": "hi"},
                    ],
                }],
            }),
        )
        self.login("产品甲")
        self.client.post(
            self.url("/api/T42M/VPU/parse"),
            data={"dump_files": (io.BytesIO(b"SPLIT:::0x00001000 = 0x000000A5"), "d.txt")},
            content_type="multipart/form-data",
        )

        response = self.client.post(self.url("/api/T42M/VPU/export_excel"))
        self.assertEqual(response.status_code, 200)

        workbook = load_workbook(io.BytesIO(response.data))
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        value_column = headers.index("d.txt 值")
        exported = {
            row[headers.index("字段名")]: row[value_column]
            for row in sheet.iter_rows(min_row=2, values_only=True)
        }
        self.assertEqual(exported, {"lo": "0x5", "hi": "0xA"})

    def test_export_leaves_a_blank_cell_when_a_dump_lacks_the_register(self) -> None:
        self.login("产品甲")
        self.client.post(
            self.url("/api/T42M/VPU/parse"),
            data={
                "dump_files": [
                    (io.BytesIO(b"CFGC:::0x02B00000 = 0x00000003"), "first.txt"),
                    (io.BytesIO(b"CFGC:::0x02B00004 = 0x00000001"), "second.txt"),
                ]
            },
            content_type="multipart/form-data",
        )
        response = self.client.post(self.url("/api/T42M/VPU/export_excel"))
        self.assertEqual(response.status_code, 200)

        sheet = load_workbook(io.BytesIO(response.data)).active
        headers = [cell.value for cell in sheet[1]]
        first = headers.index("first.txt 值")
        second = headers.index("second.txt 值")
        by_register = {
            row[headers.index("地址偏移")]: (row[first], row[second])
            for row in sheet.iter_rows(min_row=2, values_only=True)
        }

        # 只有 first.txt 里有的寄存器，second.txt 那列必须留空而不是抄一份
        self.assertEqual(by_register["0X02B00000"][1], None)
        self.assertIsNotNone(by_register["0X02B00000"][0])
        self.assertEqual(by_register["0X02B00004"], (None, "0x00000001"))

    def test_static_assets_are_served_under_the_blueprint_prefix(self) -> None:
        response = self.client.get(self.url("/static/js/main.js"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"${APP_BASE}/api/", response.data)

    AUTHENTICATED_PAGES = [
        "/", "/intro", "/intro/product", "/intro/software", "/intro/hardware",
        "/intro/product/edit", "/chips", "/chips/T42M", "/chips/T42M/VPU",
        "/chips/T42M/VPU/info", "/chips/T42M/VPU/info/edit", "/chips/T42M/VPU/check",
        "/chips/T42M/IVDC/config", "/chips/T42M/IVDC/config-guide",
        "/chips/T42M/IVDC/config-guide/edit", "/chips/T42M/IVDC/config-check",
        "/debug/T42M/VPU", "/admin",
    ]

    def test_every_page_renders_the_shared_site_chrome(self) -> None:
        self.login("admin", "admin-pw")
        for path in self.AUTHENTICATED_PAGES:
            with self.subTest(path=path):
                response = self.client.get(self.url(path))
                self.assertEqual(response.status_code, 200)
                body = response.data
                self.assertIn(b'class="top-nav"', body, "缺少统一导航栏")
                self.assertIn(b"LobsterAI", body)
                self.assertIn(self.url("/static/css/style.css").encode(), body)

    def test_session_controls_sit_in_the_page_header_not_the_site_nav(self) -> None:
        """退出/审核管理属于本应用，应落在页头标题右侧，不能混进站点导航。"""
        self.login("admin", "admin-pw")
        for path in self.AUTHENTICATED_PAGES:
            with self.subTest(path=path):
                body = self.client.get(self.url(path)).data
                self.assertIn(b'class="page-head-session"', body, "页面缺少应用级登录态")
                self.assertIn(b'class="app-user-badge"', body)
                self.assertIn(self.url("/logout").encode(), body)

                nav = body.split(b'<header class="top-nav">')[1].split(b"</header>")[0]
                self.assertNotIn("退出".encode(), nav, "退出不应出现在站点导航里")
                self.assertNotIn("审核管理".encode(), nav, "审核管理不应出现在站点导航里")

                # 登录态必须在页头分隔线之上，也就是 page-head 元素内部
                head = body.split(b'<header class="page-head">')[1].split(b"</header>")[0]
                self.assertIn(b'class="page-head-session"', head)

    def test_auth_pages_render_chrome_without_session_controls(self) -> None:
        for path in ["/login", "/register", "/reset-password", "/change-password"]:
            with self.subTest(path=path):
                response = self.client.get(self.url(path))
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'class="top-nav"', response.data)
                self.assertNotIn(b'class="page-head-session"', response.data)

    def test_config_check_guide_link_carries_the_url_prefix(self) -> None:
        self.login("硬件乙")
        response = self.client.get(self.url("/chips/T42M/IVDC/config-check"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'window.IVDC_GUIDE_URL = "{DEFAULT_URL_PREFIX}/chips/T42M/IVDC/config-guide"'.encode(),
            response.data,
        )


if __name__ == "__main__":
    unittest.main()
