"""Standalone IPT tool; credentials and chip data are provided by the platform."""
import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from backend.database import connect_database
from backend.fpga_register_debug.blueprint import create_blueprint

BASE = "/apps/fpga-register-debug"
app = Flask(__name__, static_folder=None)
app.secret_key = os.environ["APP_SESSION_SECRET"]
app.config.update(SESSION_COOKIE_NAME="app_fpga-register-debug_session",
                  SESSION_COOKIE_PATH=BASE + "/", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=50 * 1024 * 1024)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
app.register_blueprint(create_blueprint(Path("/app-data")))


@app.get(BASE + "/static/images/ivdc/<path:filename>")
def platform_image(filename):
    return send_from_directory("/app-config/ivdc", filename)


@app.get("/health")
def health():
    try:
        with connect_database(None, "fpga") as connection:
            connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        if not Path("/app-data/regdefs").is_dir():
            raise RuntimeError("register definitions unavailable")
        return jsonify({"status": "ok"})
    except Exception:
        return jsonify({"status": "unavailable"}), 503
