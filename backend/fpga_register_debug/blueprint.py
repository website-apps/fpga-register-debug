"""IPT寄存器debug小帮手 —— 以 Flask Blueprint 形式挂载到主站。

原工具是独立 Flask 应用，这里改为 Blueprint：共用主站的进程、端口和 session
cookie，但保留它自己的用户库和页面。
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint, flash, jsonify, redirect, render_template, request, send_file, session, url_for
)

from . import regdef_manager, intro_content, ivdc_guide, module_info_content, vpu_overview
from .auth import (
    ROLES, UserStore, current_user, end_session, is_edit_role, login_required,
    require_role, start_session,
)
from .excel_exporter import export_to_excel
from .parser import parse_dump_file, resolve_bitfields
from .storage import Storage

BLUEPRINT_NAME = "fpga_register_debug"
TEMPLATE_DIR = "fpga_register_debug"
DEFAULT_URL_PREFIX = "/apps/fpga-register-debug"

MAX_DUMP_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_REGDEF_UPLOAD_BYTES = 10 * 1024 * 1024
# 解析结果只放内存，限制条目数避免长时间运行后无限增长
MAX_CACHED_PARSE_RESULTS = 64
CHIP_ORDER = ("T32V", "T33", "T32P", "T42M")
INTRO_ROLE_LABELS = {"product": "产品人员", "software": "软件人员", "hardware": "设计与验证人员"}
INTRO_ENDPOINTS = {"product": "intro_product", "software": "intro_sw", "hardware": "intro_hw"}
OVERVIEW_GROUPS = ("VPU", "ISP")


def _template(name: str) -> str:
    return f"{TEMPLATE_DIR}/{name}"


def create_blueprint(
    data_root: Path,
    url_prefix: str = DEFAULT_URL_PREFIX,
    bootstrap_admin_password: str = "",
) -> Blueprint:
    storage = Storage(data_root)
    storage.initialize()
    users = UserStore(storage.users_db_file)
    users.initialize(bootstrap_admin_password=bootstrap_admin_password)

    bp = Blueprint(
        BLUEPRINT_NAME,
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
        url_prefix=url_prefix,
    )

    # 每个会话独立保存最近一次解析结果，避免多人同时调试互相覆盖
    parse_cache: OrderedDict[tuple[str, str, str], dict] = OrderedDict()

    def session_token() -> str:
        token = session.get("fpga_debug_parse_token")
        if not isinstance(token, str):
            token = uuid4().hex
            session["fpga_debug_parse_token"] = token
        return token

    def remember_parsed(chip: str, group: str, payload: dict) -> None:
        parse_cache[(session_token(), chip, group)] = payload
        parse_cache.move_to_end((session_token(), chip, group))
        while len(parse_cache) > MAX_CACHED_PARSE_RESULTS:
            parse_cache.popitem(last=False)

    def latest_parsed(chip: str, group: str) -> dict:
        return parse_cache.get((session_token(), chip, group), {"results": [], "dump_names": []})

    @bp.after_request
    def add_no_cache(response):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @bp.context_processor
    def inject_chrome():
        # 工作报告跑在同一台机器的另一个端口，导航栏要能跳过去
        hostname = request.host.split(":")[0] if request.host else "localhost"
        return {
            "base": url_prefix.rstrip("/"),
            "site_home_url": "/",
            "report_site_url": f"{request.scheme}://{hostname}:3211/reports",
            "role_labels": ROLES,
        }

    def template_context(**extra):
        """构建模板上下文，注入用户信息。"""
        user = current_user()
        return {
            "user": user,
            "can_edit": is_edit_role(user.get("role", "")),
            "is_admin": user.get("role") == "admin",
            "pending_count": users.get_pending_count() if user.get("role") == "admin" else 0,
            **extra,
        }

    # ==================== 认证路由 ====================

    @bp.route("/login", methods=["GET", "POST"])
    def login_page():
        """登录页。"""
        if request.method == "POST":
            name = request.form.get("name", "")
            password = request.form.get("password", "")
            ok, msg, user = users.login_user(name, password)
            if ok:
                start_session(user)
                return redirect(url_for(".landing"))
            flash(msg)
        return render_template(_template("login.html"))

    @bp.route("/register", methods=["GET", "POST"])
    def register_page():
        """注册页。"""
        if request.method == "POST":
            name = request.form.get("name", "")
            role = request.form.get("role", "")
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")
            if password != password2:
                flash("两次输入的密码不一致")
            else:
                ok, msg = users.register_user(name, role, password)
                if ok:
                    return render_template(_template("register.html"), success_msg=msg)
                flash(msg)
        return render_template(_template("register.html"))

    @bp.route("/logout")
    def logout():
        """退出登录。"""
        end_session()
        return redirect(url_for(".login_page"))

    @bp.route("/admin")
    @require_role("admin")
    def admin_page():
        """管理员审核页。"""
        pending = users.get_pending_users()
        all_users = users.get_all_users()
        reset_requests = users.get_reset_requests()
        return render_template(
            _template("admin.html"),
            pending=pending,
            all_users=all_users,
            reset_requests=reset_requests,
            total_pending=len(pending) + len(reset_requests),
            role_map=ROLES,
            **template_context(),
        )

    @bp.route("/admin/approve/<int:user_id>")
    @require_role("admin")
    def admin_approve(user_id: int):
        """通过用户审核。"""
        users.approve_user(user_id)
        flash("已通过审核")
        return redirect(url_for(".admin_page"))

    @bp.route("/admin/reject/<int:user_id>")
    @require_role("admin")
    def admin_reject(user_id: int):
        """拒绝用户审核。"""
        users.reject_user(user_id)
        flash("已拒绝该用户")
        return redirect(url_for(".admin_page"))

    @bp.route("/admin/delete/<int:user_id>")
    @require_role("admin")
    def admin_delete(user_id: int):
        """删除用户。"""
        users.delete_user(user_id)
        flash("已删除该用户")
        return redirect(url_for(".admin_page"))

    @bp.route("/admin/approve_reset/<int:reset_id>")
    @require_role("admin")
    def admin_approve_reset(reset_id: int):
        """通过密码重置申请。"""
        users.approve_password_reset(reset_id)
        flash("密码重置已通过")
        return redirect(url_for(".admin_page"))

    @bp.route("/admin/reject_reset/<int:reset_id>")
    @require_role("admin")
    def admin_reject_reset(reset_id: int):
        """拒绝密码重置申请。"""
        users.reject_password_reset(reset_id)
        flash("已拒绝密码重置申请")
        return redirect(url_for(".admin_page"))

    # ==================== 密码相关路由 ====================

    @bp.route("/reset-password", methods=["GET", "POST"])
    def reset_password_page():
        """忘记密码 - 申请重置（需管理员审批）。"""
        if request.method == "POST":
            name = request.form.get("name", "")
            new_password = request.form.get("new_password", "")
            new_password2 = request.form.get("new_password2", "")
            if new_password != new_password2:
                flash("两次输入的密码不一致")
            else:
                ok, msg = users.request_password_reset(name, new_password)
                if ok:
                    return render_template(_template("reset_password.html"), success_msg=msg)
                flash(msg)
        return render_template(_template("reset_password.html"))

    @bp.route("/change-password", methods=["GET", "POST"])
    def change_password_page():
        """修改密码 - 自助（需验证旧密码）。"""
        if request.method == "POST":
            name = request.form.get("name", "")
            old_password = request.form.get("old_password", "")
            new_password = request.form.get("new_password", "")
            new_password2 = request.form.get("new_password2", "")
            if new_password != new_password2:
                flash("两次输入的新密码不一致")
            else:
                ok, msg = users.change_password(name, old_password, new_password)
                if ok:
                    return render_template(_template("change_password.html"), success_msg=msg)
                flash(msg)
        return render_template(_template("change_password.html"))

    # ==================== 门户页面路由 ====================

    @bp.route("/")
    @login_required
    def landing():
        """主页。"""
        return render_template(_template("landing.html"), **template_context())

    @bp.route("/intro")
    @login_required
    def intro():
        """功能介绍 - 角色选择。"""
        return render_template(_template("intro.html"), **template_context())

    @bp.route("/intro/product")
    @login_required
    def intro_product():
        """产品人员说明。"""
        content = intro_content.load_content(storage.intro_content_dir, "product")
        return render_template(_template("intro_product.html"), content=content, **template_context())

    @bp.route("/intro/software")
    @login_required
    def intro_sw():
        """软件人员说明。"""
        content = intro_content.load_content(storage.intro_content_dir, "software")
        return render_template(_template("intro_sw.html"), content=content, **template_context())

    @bp.route("/intro/hardware")
    @login_required
    def intro_hw():
        """设计与验证人员说明。"""
        content = intro_content.load_content(storage.intro_content_dir, "hardware")
        return render_template(_template("intro_hw.html"), content=content, **template_context())

    @bp.route("/intro/<role>/edit", methods=["GET", "POST"])
    @require_role("admin")
    def intro_edit(role: str):
        """管理员编辑岗位介绍页。"""
        if role not in INTRO_ROLE_LABELS:
            return "无效的角色", 404

        if request.method == "POST":
            title = request.form.get("title", "")
            sections = _collect_sections()
            intro_content.save_content(
                storage.intro_content_dir, role, {"title": title.strip(), "sections": sections}
            )
            return redirect(url_for(f".{INTRO_ENDPOINTS[role]}"))

        content = intro_content.load_content(storage.intro_content_dir, role)
        return render_template(
            _template("intro_edit.html"),
            role=role,
            role_label=INTRO_ROLE_LABELS[role],
            content=content,
            **template_context(),
        )

    @bp.route("/chips")
    @login_required
    def chips():
        """芯片类型选择。"""
        chip_data = regdef_manager.list_chips_with_modules(storage.regdefs_dir)
        # 按发布顺序排列，未在列表中的芯片追加到末尾
        ordered = {c: chip_data[c] for c in CHIP_ORDER if c in chip_data}
        for c in chip_data:
            ordered.setdefault(c, chip_data[c])
        return render_template(_template("chips.html"), chips=ordered, **template_context())

    @bp.route("/chips/<chip>")
    @login_required
    def chip_modules(chip: str):
        """模块选择页 —— 选择 ISP / IVDC / VPU 等模块。"""
        modules = regdef_manager.list_modules(storage.regdefs_dir, chip)
        return render_template(
            _template("chip_modules.html"), chip=chip, modules=modules, **template_context()
        )

    @bp.route("/chips/<chip>/<module>")
    @login_required
    def module_home(chip: str, module: str):
        """模块功能页 —— 模块说明 / 配置检查 / 寄存器debug分析。"""
        return render_template(
            _template("module_home.html"), chip=chip, module=module, **template_context()
        )

    @bp.route("/chips/<chip>/<module>/info")
    @login_required
    def module_info(chip: str, module: str):
        """模块说明页。VPU / ISP 使用多模块卡片展示。"""
        if module in OVERVIEW_GROUPS:
            overviews = vpu_overview.load_overviews(storage.vpu_overview_dir, chip, module)
            reg_counts = {
                m: len(
                    regdef_manager.load_module(storage.regdefs_dir, chip, module, m).get("registers", [])
                )
                for m in overviews
            }
            return render_template(
                _template("vpu_overview.html"),
                chip=chip,
                module=module,
                overviews=overviews,
                registers_count=reg_counts,
                can_edit_vpu=is_edit_role(current_user().get("role", "")),
                **template_context(),
            )
        modinfo_html = module_info_content.load_content(storage.module_info_dir, module)
        return render_template(
            _template("module_info.html"),
            chip=chip,
            module=module,
            modinfo_html=modinfo_html,
            **template_context(),
        )

    @bp.route("/chips/<chip>/<module>/info/edit", methods=["GET", "POST"])
    @require_role("admin", "hardware")
    def module_info_edit(chip: str, module: str):
        """编辑模块说明页。VPU / ISP 支持编辑单个模块概述。"""
        mod_name = request.args.get("mod", "")
        if module in OVERVIEW_GROUPS and mod_name:
            if request.method == "POST":
                overviews = vpu_overview.load_overviews(storage.vpu_overview_dir, chip, module)
                overviews[mod_name] = {
                    "desc": request.form.get("desc", "").strip(),
                    "updated_by": current_user().get("name", ""),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                vpu_overview.save_overviews(storage.vpu_overview_dir, chip, overviews, module)
                return redirect(url_for(".module_info", chip=chip, module=module))
            overviews = vpu_overview.load_overviews(storage.vpu_overview_dir, chip, module)
            return render_template(
                _template("vpu_overview_edit.html"),
                chip=chip,
                module=module,
                mod_name=mod_name,
                desc=vpu_overview.get_desc(overviews, mod_name),
                **template_context(),
            )

        if request.method == "POST":
            module_info_content.save_content(
                storage.module_info_dir, module, request.form.get("content", "")
            )
            return redirect(url_for(".module_info", chip=chip, module=module))
        modinfo_html = module_info_content.load_content(storage.module_info_dir, module)
        return render_template(
            _template("module_info_edit.html"),
            chip=chip,
            module=module,
            modinfo_html=modinfo_html,
            **template_context(),
        )

    @bp.route("/chips/<chip>/<module>/check")
    @login_required
    def module_check(chip: str, module: str):
        """配置检查页（待建设）。"""
        return render_template(
            _template("module_check.html"), chip=chip, module=module, **template_context()
        )

    @bp.route("/chips/<chip>/<module>/config")
    @login_required
    def ivdc_config_hub(chip: str, module: str):
        """IVDC 配置中心页（配置指导 + 配置检查入口）。"""
        return render_template(
            _template("ivdc_config.html"), chip=chip, module=module, **template_context()
        )

    @bp.route("/chips/<chip>/<module>/config-guide")
    @login_required
    def config_guide(chip: str, module: str):
        """IVDC 配置指导页（按 step 分段展示）。"""
        sections = ivdc_guide.load_sections(storage.ivdc_config_dir)
        return render_template(
            _template("config_guide.html"),
            chip=chip,
            module=module,
            sections=sections,
            **template_context(),
        )

    @bp.route("/chips/<chip>/<module>/config-guide/edit", methods=["GET", "POST"])
    @require_role("admin")
    def config_guide_edit(chip: str, module: str):
        """编辑 IVDC 配置指导（逐个 step 编辑）。"""
        if request.method == "POST":
            ivdc_guide.save_sections(storage.ivdc_config_dir, _collect_sections())
            return redirect(url_for(".config_guide", chip=chip, module=module))
        sections = ivdc_guide.load_sections(storage.ivdc_config_dir)
        return render_template(
            _template("config_guide_edit.html"),
            chip=chip,
            module=module,
            sections=sections,
            **template_context(),
        )

    @bp.route("/chips/<chip>/<module>/config-check")
    @login_required
    def config_check_page(chip: str, module: str):
        """IVDC 配置检查页（固定 IVDC 模块）。"""
        return render_template(
            _template("config_check.html"), chip=chip, module=module, **template_context()
        )

    @bp.route("/debug/<chip>/<module>")
    @login_required
    def debug_page(chip: str, module: str):
        """寄存器debug页面（参数化）。"""
        return render_template(
            _template("debug_v2.html"), chip=chip, module=module, **template_context()
        )

    # ==================== 寄存器定义 API ====================

    @bp.get("/api/<chip>/<group>/modules")
    @login_required
    def api_list_modules(chip: str, group: str):
        """列出某芯片某组下所有已定义的模块。"""
        return jsonify({"modules": regdef_manager.list_modules(storage.regdefs_dir, chip, group)})

    @bp.get("/api/<chip>/<group>/module/<module_name>")
    @login_required
    def api_get_module(chip: str, group: str, module_name: str):
        """获取某个模块的寄存器定义。"""
        return jsonify(regdef_manager.load_module(storage.regdefs_dir, chip, group, module_name))

    @bp.post("/api/<chip>/<group>/module/import")
    @require_role("software", "hardware", "admin")
    def api_import_module(chip: str, group: str):
        """导入寄存器定义 JSON 文件。"""
        if "file" not in request.files:
            return jsonify({"error": "未上传文件"}), 400
        file = request.files["file"]
        payload = file.read(MAX_REGDEF_UPLOAD_BYTES + 1)
        if len(payload) > MAX_REGDEF_UPLOAD_BYTES:
            return jsonify({"error": "寄存器定义文件过大"}), 413
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"error": "寄存器定义文件必须是 UTF-8 编码"}), 400
        try:
            result = regdef_manager.import_json(
                storage.regdefs_dir, chip, group, content, updated_by=current_user().get("name", "")
            )
            return jsonify(result)
        except Exception as exc:  # noqa: BLE001 - 回报导入失败原因即可
            return jsonify({"error": f"导入失败: {exc}"}), 400

    # ==================== 寄存器 CRUD API ====================

    @bp.post("/api/<chip>/<group>/register")
    @require_role("software", "hardware", "admin")
    def api_add_register(chip: str, group: str):
        """添加一条寄存器定义。"""
        data = request.get_json(silent=True) or {}
        module_name = data.get("module_name", "")
        reg_name = data.get("reg_name", "")
        address_offset = data.get("address_offset", "")
        bitfields = data.get("bitfields", [])

        if not module_name or not reg_name:
            return jsonify({"error": "模块名和寄存器名不能为空"}), 400

        for bf in bitfields:
            if bf.get("start_bit", 0) > bf.get("end_bit", 0):
                return jsonify({
                    "error": f"字段 '{bf.get('field_name', '')}' 起始 bit ({bf['start_bit']}) 大于结束 bit ({bf['end_bit']})"
                }), 400

        # bit 交叉检测
        sorted_bf = sorted(bitfields, key=lambda b: b["start_bit"])
        for i in range(1, len(sorted_bf)):
            if sorted_bf[i]["start_bit"] <= sorted_bf[i - 1]["end_bit"]:
                return jsonify({
                    "error": f"Bit 范围交叉！字段 '{sorted_bf[i-1].get('field_name','')}' 与字段 '{sorted_bf[i].get('field_name','')}' 存在重叠"
                }), 400

        try:
            # 如果前端传了原始地址且与新地址不同，先按原地址删除旧记录
            original_addr = data.get("original_address_offset", "")
            if original_addr and original_addr.strip().upper() != address_offset.strip().upper():
                regdef_manager.delete_register(storage.regdefs_dir, chip, group, module_name, original_addr)

            result = regdef_manager.add_register(
                storage.regdefs_dir,
                chip,
                group,
                module_name,
                reg_name,
                address_offset,
                bitfields,
                updated_by=current_user().get("name", ""),
            )
            return jsonify({"success": True, "module": result})
        except Exception as exc:  # noqa: BLE001 - 回报保存失败原因即可
            return jsonify({"error": f"保存失败: {exc}"}), 500

    @bp.delete("/api/<chip>/<group>/register")
    @require_role("software", "hardware", "admin")
    def api_delete_register(chip: str, group: str):
        """删除一条寄存器定义。"""
        data = request.get_json(silent=True) or {}
        try:
            result = regdef_manager.delete_register(
                storage.regdefs_dir, chip, group, data.get("module_name", ""), data.get("address_offset", "")
            )
            return jsonify({"success": True, "module": result})
        except Exception as exc:  # noqa: BLE001 - 回报删除失败原因即可
            return jsonify({"error": f"删除失败: {exc}"}), 500

    # ==================== 解析 API ====================

    @bp.post("/api/<chip>/<group>/parse")
    @login_required
    def api_parse(chip: str, group: str):
        """上传 dump 文件并解析。"""
        dump_files = request.files.getlist("dump_files")
        regdef_files = request.files.getlist("regdef_files")

        if not dump_files:
            return jsonify({"error": "未上传 dump 文件"}), 400

        # 1. 导入寄存器定义 JSON（如果有上传）—— 只有编辑角色才能上传
        if regdef_files and not is_edit_role(current_user().get("role", "")):
            return jsonify({"error": "您没有导入寄存器定义的权限"}), 403

        for f in regdef_files:
            payload = f.read(MAX_REGDEF_UPLOAD_BYTES + 1)
            if len(payload) > MAX_REGDEF_UPLOAD_BYTES:
                return jsonify({"error": f"寄存器定义文件过大 ({f.filename})"}), 413
            try:
                regdef_manager.import_json(
                    storage.regdefs_dir, chip, group, payload.decode("utf-8")
                )
            except Exception as exc:  # noqa: BLE001 - 回报导入失败原因即可
                return jsonify({"error": f"寄存器定义导入失败 ({f.filename}): {exc}"}), 400

        # 2. 解析每个 dump 文件
        dump_data_list = []
        dump_names = []
        for f in dump_files:
            payload = f.read(MAX_DUMP_UPLOAD_BYTES + 1)
            if len(payload) > MAX_DUMP_UPLOAD_BYTES:
                return jsonify({"error": f"dump 文件过大 ({f.filename})"}), 413
            dump_data_list.append(parse_dump_file(payload.decode("utf-8", errors="replace")))
            dump_names.append(f.filename)

        # 3. 合并所有 dump 中的寄存器（按 (module, address_offset) 去重）
        merged: dict[tuple[str, str], dict] = {}
        for di, dump_lines in enumerate(dump_data_list):
            for line in dump_lines:
                key = (line["module"], line["address_offset"])
                if key not in merged:
                    merged[key] = {
                        "module": line["module"],
                        "address_offset": line["address_offset"],
                        "dump_values": [None] * len(dump_data_list),
                    }
                merged[key]["dump_values"][di] = line["value_int"]

        # 4. 匹配寄存器定义
        parsed_results: list[dict] = []
        # 导出时按 bit 字段重新切值需要寄存器原始值，不能用已提取的字段值
        register_values: dict[str, list[int | None]] = {}
        for (module_name, address_offset), entry in merged.items():
            register_values[f"{module_name}|{address_offset}"] = list(entry["dump_values"])
            module_data = regdef_manager.load_module(storage.regdefs_dir, chip, group, module_name)

            matched_reg = None
            for reg in module_data.get("registers", []):
                if reg.get("address_offset", "").upper() == address_offset.upper():
                    matched_reg = reg
                    break

            if matched_reg:
                parsed_results.extend(
                    _resolve_register(module_name, address_offset, matched_reg, entry["dump_values"])
                )
            else:
                # 未定义寄存器：取第一个有值的 dump 原样展示
                for dv in entry["dump_values"]:
                    if dv is not None:
                        parsed_results.append({
                            "module": module_name,
                            "address_offset": address_offset,
                            "reg_name": "[未定义寄存器]",
                            "start_bit": 0,
                            "end_bit": 31,
                            "field_name": "RAW",
                            "field_values": [dv] * len(dump_data_list),
                            "enum_meaning": "",
                            "changed": False,
                            "expected_value": "",
                            "is_abnormal": False,
                        })
                        break

        remember_parsed(chip, group, {
            "results": parsed_results,
            "dump_names": dump_names,
            "register_values": register_values,
        })
        return jsonify({"dump_names": dump_names, "results": parsed_results})

    # ==================== Excel 导出 API ====================

    def _export(chip: str, group: str, results: list[dict], download_name: str, abnormal: bool = False):
        data = latest_parsed(chip, group)
        grouped = _group_results(results, data.get("register_values", {}), abnormal=abnormal)
        excel_data = export_to_excel(
            list(grouped.values()),
            data["dump_names"],
            abnormal_only=abnormal,
        )
        return send_file(
            BytesIO(excel_data),
            as_attachment=True,
            download_name=download_name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @bp.post("/api/<chip>/<group>/export_excel")
    @login_required
    def api_export_excel(chip: str, group: str):
        """导出完整解析结果为 Excel。"""
        results = latest_parsed(chip, group)["results"]
        if not results:
            return jsonify({"error": "没有可导出的数据，请先解析 dump 文件"}), 400
        return _export(chip, group, results, "register_dump_all.xlsx")

    @bp.post("/api/<chip>/<group>/export_defined_excel")
    @login_required
    def api_export_defined_excel(chip: str, group: str):
        """导出已定义寄存器的解析结果为 Excel。"""
        results = [
            r for r in latest_parsed(chip, group)["results"]
            if r.get("reg_name") != "[未定义寄存器]"
        ]
        if not results:
            return jsonify({"error": "没有已定义的寄存器可导出"}), 400
        return _export(chip, group, results, "defined_registers.xlsx")

    @bp.post("/api/<chip>/<group>/export_abnormal_excel")
    @login_required
    def api_export_abnormal_excel(chip: str, group: str):
        """导出异常寄存器报告为 Excel。"""
        results = [r for r in latest_parsed(chip, group)["results"] if r.get("is_abnormal")]
        if not results:
            return jsonify({"error": "没有发现异常的寄存器"}), 400
        return _export(chip, group, results, "abnormal_registers.xlsx", abnormal=True)

    return bp


def _collect_sections() -> list[dict]:
    """从编辑表单里按 heading_N / content_N 顺序取出分段。"""
    sections = []
    i = 0
    while True:
        heading = request.form.get(f"heading_{i}")
        if heading is None:
            break
        content = request.form.get(f"content_{i}") or ""
        sections.append({"heading": heading.strip(), "content": content.strip()})
        i += 1
    return sections


def _parse_expected_values(expected_value_str: str) -> list[int]:
    """正常值支持逗号、分号、竖线或空格分隔的多个取值。"""
    expected_ints = []
    for part in re.split(r"[,;|，；\s]+", expected_value_str.strip()):
        part = part.strip()
        if not part:
            continue
        try:
            expected_ints.append(int(part, 16) if part.lower().startswith("0x") else int(part, 10))
        except ValueError:
            pass
    return expected_ints


def _resolve_register(
    module_name: str,
    address_offset: str,
    matched_reg: dict,
    dump_values: list[int | None],
) -> list[dict]:
    """把一个已定义寄存器在各 dump 中的取值展开成逐字段的结果行。"""
    bitfields_def = matched_reg.get("bitfields", [])
    resolved_per_dump = [
        resolve_bitfields(dv, bitfields_def) if dv is not None else []
        for dv in dump_values
    ]
    first_bf = resolved_per_dump[0] if resolved_per_dump else []

    rows = []
    for bf_idx, bf_template in enumerate(first_bf):
        field_values = [
            dump_bf[bf_idx]["field_value"] if bf_idx < len(dump_bf) else None
            for dump_bf in resolved_per_dump
        ]

        enum_meaning = ""
        for dump_bf in resolved_per_dump:
            if bf_idx < len(dump_bf) and dump_bf[bf_idx].get("enum_meaning"):
                enum_meaning = dump_bf[bf_idx]["enum_meaning"]
                break

        expected_value_str = ""
        if bf_idx < len(bitfields_def):
            expected_value_str = bitfields_def[bf_idx].get("expected_value", "") or ""
        expected_ints = _parse_expected_values(expected_value_str) if expected_value_str else []
        is_abnormal = any(
            fv is not None and fv not in expected_ints for fv in field_values
        ) if expected_ints else False

        rows.append({
            "module": module_name,
            "address_offset": address_offset,
            "reg_name": matched_reg["name"],
            "start_bit": bf_template["start_bit"],
            "end_bit": bf_template["end_bit"],
            "field_name": bf_template["field_name"],
            "field_values": field_values,
            "enum_meaning": enum_meaning,
            "changed": len(set(field_values)) > 1 if len(field_values) > 1 else False,
            "expected_value": expected_value_str,
            "is_abnormal": is_abnormal,
        })
    return rows


def _group_results(
    results: list[dict],
    register_values: dict[str, list[int | None]],
    abnormal: bool = False,
) -> dict:
    """将扁平化的结果按 (module, address_offset, reg_name) 分组。

    `dump_values` 必须是寄存器原始值：导出时会按每个 bit 字段的范围重新切值，
    传字段值进去会让第一个字段之外的所有字段都算错。
    """
    grouped: dict[tuple[str, str, str], dict] = {}
    for item in results:
        key = (item["module"], item["address_offset"], item["reg_name"])
        if key not in grouped:
            raw = register_values.get(f"{item['module']}|{item['address_offset']}")
            grouped[key] = {
                "module": item["module"],
                "address_offset": item["address_offset"],
                "reg_name": item["reg_name"],
                "bitfields": [],
                "dump_values": raw if raw is not None else item["field_values"],
            }
        bf = {
            "start_bit": item["start_bit"],
            "end_bit": item["end_bit"],
            "field_name": item["field_name"],
            "enum_meaning": item.get("enum_meaning", ""),
        }
        if abnormal:
            bf["expected_value"] = item.get("expected_value", "")
        grouped[key]["bitfields"].append(bf)
    return grouped
