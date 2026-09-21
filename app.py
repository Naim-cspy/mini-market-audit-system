import os
import sys
import time
import logging
from datetime import date
from functools import wraps
from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, session
)
import pandas as pd

from core.db import init_db, get_db_connection, export_to_csv_backup
from core.auth import init_users, verify_login, list_users, add_user, delete_user, change_password
from core.audit_engine import (
    lookup_product,
    batch_checkout,
    get_stock_warnings,
    get_financial_summary,
    add_bill,
    add_receipt,
    calculate_profit_prediction,
    add_product,
    update_price,
    remove_product
)
from core.cache_cb import product_cache

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("audit_system")

# ── App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "audit-pos-secret-key-2026-CHANGE-ME")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["JSON_SORT_KEYS"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = 3600  # 1 hour

# ── Init ────────────────────────────────────────────────────────────────
init_db()
init_users()
START_TIME = time.time()


# ══════════════════════════════════════════════════════════════════════
#  AUTH DECORATORS
# ══════════════════════════════════════════════════════════════════════

def login_required(f):
    """Redirect unauthenticated users to the login page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Allow only admin-role users; everyone else hits the denied page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = session.get("user")
        if not user:
            return redirect(url_for("login_page", next=request.path))
        if user.get("role") != "admin":
            return redirect(url_for("access_denied"))
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET"])
def login_page():
    if session.get("user"):
        return redirect(url_for("home"))
    next_url = request.args.get("next", "/")
    return render_template("login.html", next_url=next_url)


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """
    JSON login endpoint.
    Expects:  { "username": "...", "password_hash": "<sha256-hex>" }
    The browser hashes the password with SHA-256 (SubtleCrypto) BEFORE sending.
    """
    data = request.get_json(silent=True) or {}
    username     = data.get("username", "").strip()
    password_hash = data.get("password_hash", "").strip().lower()

    if not username or not password_hash:
        return jsonify({"success": False, "error": "Missing credentials."}), 400

    user = verify_login(username, password_hash)
    if not user:
        logger.warning(f"Failed login attempt for username='{username}' from {request.remote_addr}")
        return jsonify({"success": False, "error": "Incorrect username or password."}), 401

    session.permanent = True
    session["user"] = {"id": user["id"], "username": user["username"], "role": user["role"]}
    next_url = data.get("next", "/")
    logger.info(f"User '{username}' ({user['role']}) logged in from {request.remote_addr}")
    return jsonify({"success": True, "role": user["role"], "redirect": next_url})


@app.route("/logout")
def logout():
    username = session.get("user", {}).get("username", "Unknown")
    session.clear()
    logger.info(f"User '{username}' logged out.")
    return redirect(url_for("login_page"))


@app.route("/denied")
@login_required
def access_denied():
    user = session.get("user", {})
    return render_template("denied.html", user=user)


# ══════════════════════════════════════════════════════════════════════
#  HEALTH & READINESS PROBES
# ══════════════════════════════════════════════════════════════════════

@app.route("/healthz")
def healthz():
    """Liveness probe – renders interactive dashboard for browsers, JSON for clients."""
    import psutil
    proc     = psutil.Process()
    mem      = proc.memory_info()
    cpu      = psutil.cpu_percent(interval=None)
    disk     = psutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
    vm       = psutil.virtual_memory()

    payload = {
        "status":          "UP",
        "service":         "supermarket-audit-pos",
        "uptime_seconds":  round(time.time() - START_TIME, 2),
        "memory_rss_mb":   round(mem.rss / (1024 * 1024), 2),
        "memory_total_mb": round(vm.total / (1024 * 1024), 2),
        "memory_used_pct": round(vm.percent, 1),
        "cpu_percent":     round(cpu, 1),
        "disk_used_pct":   round(disk.percent, 1),
        "disk_free_gb":    round(disk.free / (1024 ** 3), 2),
        "pid":             proc.pid,
    }

    # Return JSON for API clients, HTML dashboard for browsers
    accept = request.headers.get("Accept", "")
    if "text/html" in accept and "json" not in accept:
        return render_template("health.html", data=payload)
    return jsonify(payload)


@app.route("/api/healthz")
def api_healthz_json():
    """Always-JSON endpoint for programmatic polling."""
    import psutil
    proc  = psutil.Process()
    mem   = proc.memory_info()
    cpu   = psutil.cpu_percent(interval=None)
    disk  = psutil.disk_usage(os.path.dirname(os.path.abspath(__file__)))
    vm    = psutil.virtual_memory()
    return jsonify({
        "status":          "UP",
        "service":         "supermarket-audit-pos",
        "uptime_seconds":  round(time.time() - START_TIME, 2),
        "memory_rss_mb":   round(mem.rss / (1024 * 1024), 2),
        "memory_total_mb": round(vm.total / (1024 * 1024), 2),
        "memory_used_pct": round(vm.percent, 1),
        "cpu_percent":     round(cpu, 1),
        "disk_used_pct":   round(disk.percent, 1),
        "disk_free_gb":    round(disk.free / (1024 ** 3), 2),
        "pid":             proc.pid,
    })


@app.route("/readyz")
def readyz():
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1;").fetchone()
        conn.close()
        return jsonify({"status": "READY", "database": "CONNECTED", "journal_mode": "WAL", "cache": "ACTIVE"})
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return jsonify({"status": "UNREADY", "error": str(e)}), 503


# ══════════════════════════════════════════════════════════════════════
#  FRONTEND ROUTES  (require login)
# ══════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET", "POST"])
@login_required
def home():
    if request.method == "POST":
        barcode = request.form.get("barcode", "").strip()
        if barcode:
            prod = lookup_product(barcode)
            if prod:
                try:
                    batch_checkout([{"product_id": prod["product_id"], "quantity": 1}])
                except Exception as e:
                    logger.warning(f"Direct checkout failed for {barcode}: {e}")
        return redirect(url_for("home"))

    conn = get_db_connection()
    sales = conn.execute("""
        SELECT sale_id, product_id, product_name, quantity, price,
               (quantity * price) as total, sale_date, customer_id
        FROM sales ORDER BY created_at DESC LIMIT 10;
    """).fetchall()
    conn.close()

    user = session.get("user", {})
    return render_template("index.html", recent_sales=[dict(s) for s in sales], user=user)


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add_bill":
                add_bill(float(request.form.get("amount", 0)), request.form.get("reason", "Expense"))
            elif action == "add_receipt":
                add_receipt(float(request.form.get("amount", 0)), request.form.get("reason", "Receipt"))
            elif action == "add_product":
                add_product(
                    request.form.get("product_id"),
                    request.form.get("product_name"),
                    float(request.form.get("product_price", 0)),
                    int(request.form.get("product_amount_left", 0)),
                    0, str(date.today()),
                    request.form.get("product_type", "General")
                )
            elif action == "update_price":
                update_price(request.form.get("product_id"), float(request.form.get("new_price", 0)))
            elif action == "remove_product":
                remove_product(request.form.get("product_id"))
            # ── User management (admin only) ──
            elif action == "add_user":
                add_user(
                    request.form.get("new_username"),
                    request.form.get("new_password"),
                    request.form.get("new_role", "cashier")
                )
            elif action == "delete_user":
                target = request.form.get("target_username")
                if target == "admin":
                    pass  # protect the root admin
                else:
                    delete_user(target)
            elif action == "change_password":
                change_password(
                    request.form.get("pw_username"),
                    request.form.get("new_pw")
                )
        except Exception as e:
            logger.error(f"Admin action '{action}' failed: {e}")
        return redirect(url_for("admin_panel"))

    conn = get_db_connection()
    inventory      = [dict(r) for r in conn.execute("SELECT * FROM inventory ORDER BY product_name ASC;").fetchall()]
    balance_history = [dict(r) for r in conn.execute("SELECT * FROM balance_history ORDER BY id DESC LIMIT 50;").fetchall()]
    conn.close()

    summary      = get_financial_summary()
    stock_warnings = get_stock_warnings(threshold=50)
    prediction   = calculate_profit_prediction()
    users        = list_users()
    current_user = session.get("user", {})

    return render_template(
        "admin.html",
        summary=summary,
        inventory=inventory,
        balance_history=balance_history,
        stock_warnings=stock_warnings,
        prediction=prediction,
        users=users,
        current_user=current_user,
    )


# ══════════════════════════════════════════════════════════════════════
#  REST API  (product / checkout / finance)
# ══════════════════════════════════════════════════════════════════════

@app.route("/api/v1/product/<barcode>", methods=["GET"])
@login_required
def api_product_lookup(barcode):
    prod = lookup_product(barcode)
    if not prod:
        return jsonify({"error": "ProductNotFound", "message": f"No product for barcode '{barcode}'"}), 404
    return jsonify(prod)


@app.route("/api/v1/checkout", methods=["POST"])
@login_required
def api_checkout():
    data = request.get_json(silent=True)
    if not data or "items" not in data:
        return jsonify({"error": "InvalidPayload", "message": "Requires 'items' array"}), 400
    try:
        receipt = batch_checkout(data["items"], customer_id=data.get("customer_id", "C101"))
        return jsonify(receipt)
    except ValueError as ve:
        return jsonify({"error": "CheckoutError", "message": str(ve)}), 400
    except Exception as e:
        logger.error(f"Checkout exception: {e}", exc_info=True)
        return jsonify({"error": "ServerError", "message": "Internal checkout failure"}), 500


@app.route("/api/v1/analytics/summary", methods=["GET"])
@login_required
def api_analytics_summary():
    return jsonify(get_financial_summary())


@app.route("/api/v1/analytics/prediction", methods=["GET"])
@admin_required
def api_profit_prediction():
    return jsonify(calculate_profit_prediction())


@app.route("/api/v1/stock-warnings", methods=["GET"])
@login_required
def api_stock_warnings():
    return jsonify(get_stock_warnings(threshold=request.args.get("threshold", 50, type=int)))


@app.route("/api/v1/bills", methods=["POST"])
@admin_required
def api_add_bill():
    data = request.get_json(silent=True) or request.form
    try:
        new_bal = add_bill(float(data.get("amount", 0)), data.get("reason", "Expense"))
        return jsonify({"success": True, "new_balance": new_bal})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/v1/receipts", methods=["POST"])
@admin_required
def api_add_receipt():
    data = request.get_json(silent=True) or request.form
    try:
        new_bal = add_receipt(float(data.get("amount", 0)), data.get("reason", "Receipt"))
        return jsonify({"success": True, "new_balance": new_bal})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/v1/export", methods=["GET"])
@admin_required
def api_export_csv():
    try:
        export_to_csv_backup()
        return jsonify({"success": True, "message": "Synchronized DB → CSV."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ══════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found_handler(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "NotFound"}), 404
    return redirect(url_for("home"))


@app.errorhandler(500)
def server_error_handler(e):
    logger.error(f"Global Error Boundary 500: {e}")
    if request.path.startswith("/api/"):
        return jsonify({"error": "InternalServerError", "status": "RECOVERED"}), 500
    return redirect(url_for("home"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)