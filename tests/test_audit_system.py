import unittest
import json
import hashlib
import time
from core.db import init_db, get_db_connection
from core.auth import init_users, verify_login
from core.audit_engine import (
    lookup_product,
    batch_checkout,
    get_stock_warnings,
    get_financial_summary,
    add_bill,
    add_receipt,
    calculate_profit_prediction
)
from app import app


class TestAuditSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        init_users()
        cls.client = app.test_client()

    def test_01_database_and_seed(self):
        """Verify database tables and seeded inventory."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM inventory;")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertGreater(count, 0, "Inventory should contain seeded products.")

    def test_02_product_lookup(self):
        """Verify fast product lookup and case-insensitivity."""
        p1 = lookup_product("P001")
        self.assertIsNotNone(p1)
        self.assertEqual(p1["product_name"], "Wireless Mouse")

        p2 = lookup_product("p001")
        self.assertIsNotNone(p2)
        self.assertEqual(p2["product_id"], "P001")

    def test_03_batch_checkout(self):
        """Verify atomic multi-item checkout."""
        prod = lookup_product("P001")
        initial_stock = prod["product_amount_left"]

        items = [{"product_id": "P001", "quantity": 2}]
        receipt = batch_checkout(items, customer_id="C_TEST")
        self.assertTrue(receipt["success"])
        self.assertEqual(receipt["total"], round(prod["product_price"] * 2, 2))

        prod_after = lookup_product("P001")
        self.assertEqual(prod_after["product_amount_left"], initial_stock - 2)

    def test_04_insufficient_stock_rollback(self):
        """Verify that requesting more than available stock raises ValueError and rolls back."""
        with self.assertRaises(ValueError):
            batch_checkout([{"product_id": "P003", "quantity": 999999}])

    def test_05_financial_summary_and_ledger(self):
        """Verify financial summary and bill/receipt updates."""
        summary_before = get_financial_summary()
        curr_bal = summary_before["current_balance"]

        new_bal = add_bill(50.0, "Test Operational Bill")
        self.assertAlmostEqual(new_bal, curr_bal - 50.0, places=2)

        new_bal_2 = add_receipt(100.0, "Test Deposit")
        self.assertAlmostEqual(new_bal_2, new_bal + 100.0, places=2)

    def test_06_profit_prediction_regression(self):
        """Verify linear regression forecasting calculations."""
        pred = calculate_profit_prediction()
        self.assertTrue(pred["has_enough_data"])
        self.assertIn("prediction_1yr", pred)
        self.assertIsInstance(pred["prediction_1yr"], float)
        self.assertIn("equation", pred)

    def test_07_stock_warnings(self):
        """Verify stock alerts for items at or below 50 units."""
        warnings = get_stock_warnings(threshold=50)
        self.assertIsInstance(warnings, list)
        for w in warnings:
            self.assertLessEqual(w["product_amount_left"], 50)

    def test_08_auth_security_and_roles(self):
        """Verify password hashing, role enforcement, and access restrictions."""
        # Test SHA-256 login verification
        admin_hash = hashlib.sha256("admin123".encode()).hexdigest()
        user = verify_login("admin", admin_hash)
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")

        # Wrong password fails
        bad_hash = hashlib.sha256("wrongpass".encode()).hexdigest()
        self.assertIsNone(verify_login("admin", bad_hash))

        # Unauthenticated access to /admin redirects to login
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers.get("Location", ""))

        # Cashier access to /admin redirects to /denied
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": 2, "username": "john", "role": "cashier"}
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/denied", res.headers.get("Location", ""))

        # Admin access to /admin succeeds
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": 1, "username": "admin", "role": "admin"}
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 200)

    def test_09_api_endpoints_and_healthz(self):
        """Test Flask HTTP API endpoints and health probe."""
        # Health probe (public)
        res = self.client.get("/healthz", headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "UP")

        # Ready check (public)
        res = self.client.get("/readyz")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "READY")

        # Authenticated API access
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": 1, "username": "admin", "role": "admin"}

        res = self.client.get("/api/v1/product/P002")
        self.assertEqual(res.status_code, 200)
        prod = json.loads(res.data)
        self.assertEqual(prod["product_id"], "P002")

        res = self.client.post("/api/v1/checkout", json={
            "items": [{"product_id": "P002", "quantity": 1}],
            "customer_id": "API_TEST"
        })
        self.assertEqual(res.status_code, 200)
        rcp = json.loads(res.data)
        self.assertTrue(rcp["success"])


if __name__ == "__main__":
    unittest.main()
