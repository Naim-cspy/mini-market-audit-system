import time
import uuid
import logging
import statistics
from datetime import date, datetime
from core.db import db_session, execute_with_retry, export_to_csv_backup
from core.cache_cb import product_cache, metrics_cache, db_circuit_breaker

logger = logging.getLogger("audit_system.engine")


def lookup_product(barcode):
    """Fast product lookup with L1 in-memory caching and circuit breaker protection."""
    if not barcode:
        return None

    clean_code = str(barcode).strip().upper()

    # 1. Check L1 cache (<0.1ms)
    cached = product_cache.get(clean_code)
    if cached is not None:
        return cached

    # 2. Database lookup protected by circuit breaker
    def _query(conn):
        cursor = conn.cursor()
        cursor.execute("""
            SELECT product_id, product_name, product_price, product_amount_left, 
                   product_amount_sold, date_sold, date_filled, product_type
            FROM inventory
            WHERE UPPER(product_id) = ? OR UPPER(product_name) = ?
            LIMIT 1;
        """, (clean_code, clean_code))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    try:
        product = db_circuit_breaker.call(execute_with_retry, _query)
        if product:
            product_cache.set(clean_code, product, ttl=180.0)
            # Also cache by ID
            product_cache.set(product["product_id"].upper(), product, ttl=180.0)
        return product
    except Exception as e:
        logger.error(f"Error during product lookup for '{clean_code}': {e}")
        return None


def batch_checkout(cart_items, customer_id="C101"):
    """
    Atomically process checkout for multiple items.
    cart_items: list of dicts [{'product_id': 'P001', 'quantity': 2}, ...]
    Guarantees all-or-nothing stock decrement and sales logging.
    """
    if not cart_items:
        raise ValueError("Cart is empty.")

    def _execute_checkout(conn):
        cursor = conn.cursor()
        today_str = str(date.today())
        now_ts = int(time.time() * 1000)
        processed_items = []
        total_amount = 0.0

        # Pass 1: Validate stock for all items
        for idx, item in enumerate(cart_items):
            pid = str(item.get("product_id", "")).strip().upper()
            qty = int(item.get("quantity", 1))
            if qty <= 0:
                raise ValueError(f"Invalid quantity {qty} for item {pid}")

            cursor.execute("SELECT product_id, product_name, product_price, product_amount_left FROM inventory WHERE UPPER(product_id) = ?", (pid,))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Product '{pid}' not found in inventory.")

            amount_left = row["product_amount_left"]
            if amount_left < qty:
                raise ValueError(f"Insufficient stock for '{row['product_name']}' ({pid}). Available: {amount_left}, Requested: {qty}")

            item_total = round(row["product_price"] * qty, 2)
            total_amount += item_total
            processed_items.append({
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "price": row["product_price"],
                "quantity": qty,
                "subtotal": item_total,
                "remaining_stock": amount_left - qty
            })

        # Pass 2: Apply decrements and insert sales records
        for idx, item in enumerate(processed_items):
            sale_id = f"S{now_ts}_{uuid.uuid4().hex[:6]}"
            cursor.execute("""
                UPDATE inventory 
                SET product_amount_left = product_amount_left - ?,
                    product_amount_sold = product_amount_sold + ?,
                    date_sold = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE UPPER(product_id) = ?
            """, (item["quantity"], item["quantity"], today_str, item["product_id"].upper()))

            cursor.execute("""
                INSERT INTO sales (sale_id, product_id, product_name, quantity, price, sale_date, customer_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (sale_id, item["product_id"], item["product_name"], item["quantity"], item["price"], today_str, customer_id))

            # Invalidate cache
            product_cache.delete(item["product_id"].upper())

        # Update balance history with income from this sale transaction
        cursor.execute("SELECT budget_starting, money_in, money_out FROM balance_history ORDER BY id DESC LIMIT 1;")
        last_bal = cursor.fetchone()
        if last_bal:
            last_start = last_bal["budget_starting"]
            last_in = last_bal["money_in"] or 0.0
            last_out = last_bal["money_out"] or 0.0
            current_bal = last_start + last_in - last_out
        else:
            current_bal = 5000.0

        cursor.execute("""
            INSERT INTO balance_history (date, budget_starting, money_in, money_out, reason)
            VALUES (?, ?, ?, 0.0, ?)
        """, (today_str, current_bal, total_amount, f"POS Checkout ({len(processed_items)} items)"))

        # Log audit entry
        cursor.execute("""
            INSERT INTO audit_logs (action, details)
            VALUES (?, ?)
        """, ("CHECKOUT", f"Customer {customer_id} purchased {len(processed_items)} items for ${total_amount:.2f}"))

        metrics_cache.clear()

        return {
            "success": True,
            "receipt_id": f"RCP-{now_ts}-{uuid.uuid4().hex[:4].upper()}",
            "date": today_str,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "customer_id": customer_id,
            "items": processed_items,
            "total": round(total_amount, 2),
            "item_count": sum(i["quantity"] for i in processed_items)
        }

    receipt = execute_with_retry(_execute_checkout)
    # Async background CSV export
    try:
        export_to_csv_backup()
    except Exception as e:
        logger.warning(f"Background CSV sync delayed: {e}")
    return receipt


def get_stock_warnings(threshold=50):
    """Retrieve products at or below warning threshold."""
    def _query(conn):
        cursor = conn.cursor()
        cursor.execute("""
            SELECT product_id, product_name, product_price, product_amount_left, product_amount_sold, product_type
            FROM inventory
            WHERE product_amount_left <= ?
            ORDER BY product_amount_left ASC;
        """, (threshold,))
        warnings = []
        for row in cursor.fetchall():
            item = dict(row)
            item["severity"] = "CRITICAL" if item["product_amount_left"] <= 10 else "WARNING"
            warnings.append(item)
        return warnings

    return execute_with_retry(_query)


def get_financial_summary():
    """Calculates live financial metrics and budget health."""
    cached = metrics_cache.get("summary")
    if cached:
        return cached

    def _calc(conn):
        cursor = conn.cursor()
        
        # 1. Balance History
        cursor.execute("SELECT budget_starting, money_in, money_out FROM balance_history ORDER BY id ASC;")
        rows = cursor.fetchall()
        
        total_in = sum(r["money_in"] or 0.0 for r in rows)
        total_out = sum(r["money_out"] or 0.0 for r in rows)
        
        if rows:
            last_row = rows[-1]
            current_balance = last_row["budget_starting"] + (last_row["money_in"] or 0.0) - (last_row["money_out"] or 0.0)
            initial_budget = rows[0]["budget_starting"]
        else:
            current_balance = 5000.0
            initial_budget = 5000.0

        net_profit = current_balance - initial_budget

        # 2. Sales Totals
        cursor.execute("SELECT COUNT(*) as tx_count, SUM(quantity * price) as sales_revenue, SUM(quantity) as units_sold FROM sales;")
        sales_stat = cursor.fetchone()
        sales_revenue = sales_stat["sales_revenue"] or 0.0
        total_tx = sales_stat["tx_count"] or 0
        units_sold = sales_stat["units_sold"] or 0

        # 3. Inventory Totals
        cursor.execute("SELECT COUNT(*) as total_prods, SUM(product_price * product_amount_left) as inventory_value, SUM(product_amount_left) as total_stock FROM inventory;")
        inv_stat = cursor.fetchone()
        total_products = inv_stat["total_prods"] or 0
        inventory_value = inv_stat["inventory_value"] or 0.0
        total_stock = inv_stat["total_stock"] or 0

        # 4. Stock alerts count
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE product_amount_left <= 50;")
        alert_count = cursor.fetchone()[0]

        summary = {
            "current_balance": round(current_balance, 2),
            "initial_budget": round(initial_budget, 2),
            "total_money_in": round(total_in, 2),
            "total_money_out": round(total_out, 2),
            "net_profit": round(net_profit, 2),
            "sales_revenue": round(sales_revenue, 2),
            "total_transactions": total_tx,
            "units_sold": units_sold,
            "total_products": total_products,
            "inventory_valuation": round(inventory_value, 2),
            "total_stock_count": total_stock,
            "alert_count": alert_count,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        metrics_cache.set("summary", summary, ttl=10.0)
        return summary

    return execute_with_retry(_calc)


def add_bill(amount, reason):
    """Records an expense bill and updates current balance."""
    if amount <= 0:
        raise ValueError("Bill amount must be positive.")

    def _execute(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT budget_starting, money_in, money_out FROM balance_history ORDER BY id DESC LIMIT 1;")
        last = cursor.fetchone()
        if last:
            curr = last["budget_starting"] + (last["money_in"] or 0.0) - (last["money_out"] or 0.0)
        else:
            curr = 5000.0

        today_str = str(date.today())
        cursor.execute("""
            INSERT INTO balance_history (date, budget_starting, money_in, money_out, reason)
            VALUES (?, ?, 0.0, ?, ?)
        """, (today_str, curr, amount, reason.strip()))

        cursor.execute("INSERT INTO audit_logs (action, details) VALUES (?, ?)", ("ADD_BILL", f"Expense ${amount:.2f} for '{reason}'"))
        metrics_cache.clear()
        return curr - amount

    res = execute_with_retry(_execute)
    export_to_csv_backup()
    return res


def add_receipt(amount, reason):
    """Records an income receipt and updates current balance."""
    if amount <= 0:
        raise ValueError("Receipt amount must be positive.")

    def _execute(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT budget_starting, money_in, money_out FROM balance_history ORDER BY id DESC LIMIT 1;")
        last = cursor.fetchone()
        if last:
            curr = last["budget_starting"] + (last["money_in"] or 0.0) - (last["money_out"] or 0.0)
        else:
            curr = 5000.0

        today_str = str(date.today())
        cursor.execute("""
            INSERT INTO balance_history (date, budget_starting, money_in, money_out, reason)
            VALUES (?, ?, ?, 0.0, ?)
        """, (today_str, curr, amount, reason.strip()))

        cursor.execute("INSERT INTO audit_logs (action, details) VALUES (?, ?)", ("ADD_RECEIPT", f"Income ${amount:.2f} from '{reason}'"))
        metrics_cache.clear()
        return curr + amount

    res = execute_with_retry(_execute)
    export_to_csv_backup()
    return res


def calculate_profit_prediction():
    """
    Statistical Linear Regression profit prediction engine.
    Calculates profit trajectory and 1-year statistical approximation.
    """
    def _query(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT date, budget_starting, money_in, money_out FROM balance_history ORDER BY id ASC;")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    rows = execute_with_retry(_query)
    if not rows or len(rows) < 2:
        return {
            "has_enough_data": False,
            "message": "At least 2 financial history entries required for statistical prediction.",
            "dates": [],
            "actual_profits": [],
            "trend_line": [],
            "prediction_1yr": 0.0,
            "daily_slope": 0.0
        }

    initial_budget = rows[0]["budget_starting"]
    dates = []
    profits = []

    for r in rows:
        dates.append(r["date"])
        # Profit relative to day 0
        current_val = r["budget_starting"] + (r["money_in"] or 0.0) - (r["money_out"] or 0.0)
        profits.append(round(current_val - initial_budget, 2))

    x = list(range(len(rows)))
    y = profits

    try:
        slope, intercept = statistics.linear_regression(x, y)
    except Exception:
        # Fallback if constant values
        slope = 0.0
        intercept = y[-1] if y else 0.0

    trend_line = [round((slope * xi) + intercept, 2) for xi in x]

    # Predict 30d, 90d, 365d
    future_day_1yr = len(rows) + 365
    future_day_90d = len(rows) + 90
    future_day_30d = len(rows) + 30

    pred_1yr = round((slope * future_day_1yr) + intercept, 2)
    pred_90d = round((slope * future_day_90d) + intercept, 2)
    pred_30d = round((slope * future_day_30d) + intercept, 2)

    return {
        "has_enough_data": True,
        "dates": dates,
        "actual_profits": profits,
        "trend_line": trend_line,
        "equation": f"Profit = {slope:.2f} * Day + {intercept:.2f}",
        "daily_slope": round(slope, 2),
        "intercept": round(intercept, 2),
        "prediction_30d": pred_30d,
        "prediction_90d": pred_90d,
        "prediction_1yr": pred_1yr
    }


def add_product(prod_id, name, price, amount_left, amount_sold=0, date_filled=None, prod_type="General"):
    """Adds a new product to inventory."""
    pid = str(prod_id).strip().upper()
    date_filled = date_filled or str(date.today())

    def _execute(conn):
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO inventory (product_id, product_name, product_price, product_amount_left, product_amount_sold, date_filled, product_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (pid, name.strip(), float(price), int(amount_left), int(amount_sold), date_filled, prod_type.strip()))
        cursor.execute("INSERT INTO audit_logs (action, details) VALUES (?, ?)", ("ADD_PRODUCT", f"Added {name} ({pid})"))

    execute_with_retry(_execute)
    product_cache.delete(pid)
    metrics_cache.clear()
    export_to_csv_backup()


def update_price(prod_id, new_price):
    """Updates product price with audit tracking."""
    pid = str(prod_id).strip().upper()
    new_price = float(new_price)

    def _execute(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT product_price FROM inventory WHERE UPPER(product_id) = ?", (pid,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Product {pid} not found.")
        old_price = row["product_price"]
        cursor.execute("UPDATE inventory SET product_price = ?, updated_at = CURRENT_TIMESTAMP WHERE UPPER(product_id) = ?", (new_price, pid))
        cursor.execute("INSERT INTO audit_logs (action, details) VALUES (?, ?)", ("PRICE_CHANGE", f"Product {pid} price changed from ${old_price:.2f} to ${new_price:.2f}"))

    execute_with_retry(_execute)
    product_cache.delete(pid)
    metrics_cache.clear()
    export_to_csv_backup()


def remove_product(prod_id):
    """Deletes a product from inventory."""
    pid = str(prod_id).strip().upper()

    def _execute(conn):
        cursor = conn.cursor()
        cursor.execute("DELETE FROM inventory WHERE UPPER(product_id) = ?", (pid,))
        cursor.execute("INSERT INTO audit_logs (action, details) VALUES (?, ?)", ("REMOVE_PRODUCT", f"Deleted product {pid}"))

    execute_with_retry(_execute)
    product_cache.delete(pid)
    metrics_cache.clear()
    export_to_csv_backup()
