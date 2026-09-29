"""认证与权限管理模块。

- PostgreSQL 存储用户信息，离线文件兼容 SQLite
- SHA256 + 随机盐值 哈希密码
- 注册 → 管理员审核 → 登录
- 基于岗位角色的权限控制

密码哈希沿用工具原有的 `salt:sha256(salt+password)` 格式，站点其余部分使用
werkzeug 的 pbkdf2。两者不能互换：改格式会让已有账号全部无法登录。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from flask import flash, redirect, session, url_for

from backend.database import connect_database

# 与站点其它登录态共用同一个 cookie，key 需要带前缀避免互相覆盖
SESSION_KEY = "fpga_debug_user"

# 岗位列表及其说明
ROLES = {
    "product":  "产品人员",
    "software": "软件人员",
    "hardware": "IC设计/验证",
    "admin":    "管理员",
}

# 有编辑权限的岗位
EDIT_ROLES = {"software", "hardware", "admin"}

# 管理员岗位
ADMIN_ROLE = "admin"


# ==================== 密码 ====================

def _hash_password(password: str, salt: str) -> str:
    """SHA256 哈希密码。"""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _generate_password_hash(password: str) -> str:
    """生成 salt:hash 格式的密码串。"""
    salt = secrets.token_hex(16)
    return f"{salt}:{_hash_password(password, salt)}"


def _check_password(password: str, stored: str) -> bool:
    """验证密码。"""
    parts = stored.split(":", 1)
    if len(parts) != 2:
        return False
    salt, pw_hash = parts
    return secrets.compare_digest(_hash_password(password, salt), pw_hash)


class UserStore:
    """工具自带的用户库，独立于站点的管理员体系。"""

    def __init__(self, db_file: Path) -> None:
        self.db_file = Path(db_file)

    # ==================== 数据库 ====================

    def _connect(self) -> sqlite3.Connection:
        conn = connect_database(self.db_file, "fpga")
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self, bootstrap_admin_password: str = "") -> None:
        """建表；仅当库里一个管理员都没有且显式给了初始密码时才创建管理员。"""
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL UNIQUE,
                    role       TEXT NOT NULL,
                    password   TEXT NOT NULL,
                    status     TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    name         TEXT NOT NULL,
                    new_password TEXT NOT NULL,
                    status       TEXT DEFAULT 'pending',
                    created_at   TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            if bootstrap_admin_password:
                admin = conn.execute(
                    "SELECT id FROM users WHERE role = 'admin' LIMIT 1"
                ).fetchone()
                if not admin:
                    conn.execute(
                        "INSERT OR IGNORE INTO users (name, role, password, status)"
                        " VALUES (?, ?, ?, ?)",
                        ("admin", "admin", _generate_password_hash(bootstrap_admin_password), "approved"),
                    )
            conn.commit()
        finally:
            conn.close()

    # ==================== 密码操作 ====================

    def change_password(self, name: str, old_password: str, new_password: str) -> tuple[bool, str]:
        """自助修改密码（需验证旧密码）。"""
        if len(new_password) < 6:
            return False, "新密码至少 6 位"
        conn = self._connect()
        try:
            user = conn.execute(
                "SELECT * FROM users WHERE name = ? AND status = 'approved'", (name.strip(),)
            ).fetchone()
            if not user:
                return False, "用户不存在或未通过审核"
            if not _check_password(old_password, user["password"]):
                return False, "旧密码错误"
            conn.execute(
                "UPDATE users SET password = ? WHERE id = ?",
                (_generate_password_hash(new_password), user["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        return True, "密码修改成功"

    def request_password_reset(self, name: str, new_password: str) -> tuple[bool, str]:
        """申请密码重置（需管理员审批）。"""
        if len(new_password) < 6:
            return False, "新密码至少 6 位"
        conn = self._connect()
        try:
            user = conn.execute(
                "SELECT * FROM users WHERE name = ? AND status = 'approved'", (name.strip(),)
            ).fetchone()
            if not user:
                return False, "该用户不存在或未通过审核，无法重置密码"
            conn.execute(
                "INSERT INTO password_resets (user_id, name, new_password, status)"
                " VALUES (?, ?, ?, 'pending')",
                (user["id"], name.strip(), _generate_password_hash(new_password)),
            )
            conn.commit()
        finally:
            conn.close()
        return True, "密码重置申请已提交，请等待管理员审批"

    def get_reset_requests(self) -> list[dict]:
        """获取待审批的密码重置请求。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM password_resets WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def approve_password_reset(self, reset_id: int) -> bool:
        """管理员审批通过密码重置：使用申请时提交的新密码。"""
        conn = self._connect()
        try:
            req = conn.execute(
                "SELECT * FROM password_resets WHERE id = ?", (reset_id,)
            ).fetchone()
            if not req:
                return False
            conn.execute(
                "UPDATE users SET password = ? WHERE id = ? AND status = 'approved'",
                (req["new_password"], req["user_id"]),
            )
            conn.execute(
                "UPDATE password_resets SET status = 'approved' WHERE id = ?", (reset_id,)
            )
            conn.commit()
        finally:
            conn.close()
        return True

    def reject_password_reset(self, reset_id: int) -> bool:
        """管理员拒绝密码重置请求。"""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE password_resets SET status = 'rejected' WHERE id = ?", (reset_id,)
            )
            conn.commit()
        finally:
            conn.close()
        return True

    # ==================== 用户操作 ====================

    def register_user(self, name: str, role: str, password: str) -> tuple[bool, str]:
        """注册新用户，返回 (成功, 消息)。"""
        if not name or not name.strip():
            return False, "姓名不能为空"
        if role not in ROLES:
            return False, "无效的岗位选择"
        if role == ADMIN_ROLE:
            return False, "无法自助注册管理员账号"
        if len(password) < 6:
            return False, "密码至少 6 位"

        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT id FROM users WHERE name = ?", (name.strip(),)
            ).fetchone()
            if existing:
                return False, f"用户「{name.strip()}」已存在"
            conn.execute(
                "INSERT INTO users (name, role, password, status) VALUES (?, ?, ?, ?)",
                (name.strip(), role, _generate_password_hash(password), "pending"),
            )
            conn.commit()
        finally:
            conn.close()
        return True, "注册成功！已提交审核，请等待管理员通过后登录。"

    def login_user(self, name: str, password: str) -> tuple[bool, str, dict | None]:
        """验证登录，返回 (成功, 消息, 用户信息)。"""
        conn = self._connect()
        try:
            user = conn.execute(
                "SELECT * FROM users WHERE name = ?", (name.strip(),)
            ).fetchone()
        finally:
            conn.close()

        if not user:
            return False, "用户不存在", None
        if user["status"] == "pending":
            return False, "账号尚未通过审核，请等待管理员通过", None
        if user["status"] == "rejected":
            return False, "账号审核未通过", None
        if not _check_password(password, user["password"]):
            return False, "密码错误", None

        return True, "登录成功", {
            "id": user["id"],
            "name": user["name"],
            "role": user["role"],
        }

    def get_pending_users(self) -> list[dict]:
        """获取待审核用户列表（仅管理员调用）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, role, status, created_at FROM users"
                " WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def _set_status(self, user_id: int, status: str) -> bool:
        conn = self._connect()
        try:
            conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
            conn.commit()
        finally:
            conn.close()
        return True

    def approve_user(self, user_id: int) -> bool:
        """通过用户审核。"""
        return self._set_status(user_id, "approved")

    def reject_user(self, user_id: int) -> bool:
        """拒绝用户审核。"""
        return self._set_status(user_id, "rejected")

    def delete_user(self, user_id: int) -> bool:
        """删除用户。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
        return True

    def get_pending_count(self) -> int:
        """获取待审核总数（新用户 + 密码重置申请）。"""
        conn = self._connect()
        try:
            users = conn.execute(
                "SELECT COUNT(*) AS cnt FROM users WHERE status = 'pending'"
            ).fetchone()
            resets = conn.execute(
                "SELECT COUNT(*) AS cnt FROM password_resets WHERE status = 'pending'"
            ).fetchone()
        finally:
            conn.close()
        return (users["cnt"] if users else 0) + (resets["cnt"] if resets else 0)

    def get_all_users(self) -> list[dict]:
        """获取所有用户列表（管理员）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, role, status, created_at FROM users ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]


# ==================== 会话helpers ====================

def current_user() -> dict:
    user = session.get(SESSION_KEY)
    return user if isinstance(user, dict) else {}


def start_session(user: dict) -> None:
    session[SESSION_KEY] = user


def end_session() -> None:
    session.pop(SESSION_KEY, None)


def is_edit_role(role: str) -> bool:
    return role in EDIT_ROLES


# ==================== 装饰器 ====================

def login_required(f):
    """要求登录。未登录重定向到工具自己的登录页。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            flash("请先登录")
            return redirect(url_for(".login_page"))
        return f(*args, **kwargs)
    return decorated


def require_role(*roles: str):
    """要求指定岗位角色。"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = current_user()
            if not user:
                flash("请先登录")
                return redirect(url_for(".login_page"))
            if user.get("role") not in roles:
                return "权限不足", 403
            return f(*args, **kwargs)
        return decorated
    return decorator
