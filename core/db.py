import sqlite3
import os
import time
import logging
import pandas as pd
from contextlib import contextmanager

logger = logging.getLogger("audit_system.db")

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit_system.db")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_db_connection():
    """Create a thread-safe connection to SQLite with WAL mode enabled."""
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_session():
    """Context manager for managing transactional database operations with auto-commit/rollback."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Transaction rolled back due to error: {e}", exc_info=True)
        raise
    finally:
        conn.close()


def execute_with_retry(query_fn, max_retries=5, initial_delay=0.05):
    """Executes a database function with exponential backoff on busy/locked errors."""
    delay = initial_delay
    last_err = None
    for attempt in range(max_retries):
        try:
            with db_session() as conn:
                return query_fn(conn)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                last_err = e
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except Exception:
            raise
    raise last_err or RuntimeError("Failed to execute database query after retries.")


def init_db():
    """Initialize database tables and automatically migrate existing CSV data."""
    with db_session() as conn:
        cursor = conn.cursor()
        
        # 1. Inventory Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                product_id TEXT PRIMARY KEY,
                product_name TEXT NOT NULL,
                product_price REAL NOT NULL,
                product_amount_left INTEGER NOT NULL,
                product_amount_sold INTEGER NOT NULL DEFAULT 0,
                date_sold TEXT,
                date_filled TEXT,
                product_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory(product_name);")
        
        # 2. Sales Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                sale_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                product_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                sale_date TEXT NOT NULL,
                customer_id TEXT DEFAULT 'C101',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES inventory(product_id) ON DELETE SET NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sale_date);")
        
        # 3. Balance History (Ledger) Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                budget_starting REAL NOT NULL,
                money_in REAL DEFAULT 0.0,
                money_out REAL DEFAULT 0.0,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_balance_date ON balance_history(date);")
        
        # 4. Audit Log Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

    logger.info("Database schemas verified.")
    _seed_from_csvs_if_needed()


def _seed_from_csvs_if_needed():
    """Imports initial data from existing CSV files if database tables are currently empty."""
    with db_session() as conn:
        cursor = conn.cursor()
        
        # Check inventory
        cursor.execute("SELECT COUNT(*) FROM inventory;")
        inv_count = cursor.fetchone()[0]
        inv_csv = os.path.join(BASE_DIR, "inventory.csv")
        if inv_count == 0 and os.path.exists(inv_csv):
            try:
                df = pd.read_csv(inv_csv)
                for _, row in df.iterrows():
                    cursor.execute("""
                        INSERT OR IGNORE INTO inventory 
                        (product_id, product_name, product_price, product_amount_left, product_amount_sold, date_sold, date_filled, product_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(row.get("product_id", "")).strip(),
                        str(row.get("product_name", "")).strip(),
                        float(row.get("product_price", 0.0)),
                        int(row.get("product_amount_left", 0)),
                        int(row.get("product_amount_sold", 0)),
                        str(row.get("date_sold", "")) if pd.notna(row.get("date_sold")) else "",
                        str(row.get("date_filled", "")) if pd.notna(row.get("date_filled")) else "",
                        str(row.get("product_type", "")) if pd.notna(row.get("product_type")) else ""
                    ))
                logger.info(f"Seeded {len(df)} inventory records from {inv_csv}")
            except Exception as e:
                logger.warning(f"Could not seed inventory from CSV: {e}")

        # Check balance history
        cursor.execute("SELECT COUNT(*) FROM balance_history;")
        bal_count = cursor.fetchone()[0]
        bal_csv = os.path.join(BASE_DIR, "balance_history.csv")
        if bal_count == 0 and os.path.exists(bal_csv):
            try:
                df = pd.read_csv(bal_csv)
                for _, row in df.iterrows():
                    m_in = float(row.get("money_in")) if pd.notna(row.get("money_in")) and str(row.get("money_in")).strip() != "" else 0.0
                    m_out = float(row.get("money_out")) if pd.notna(row.get("money_out")) and str(row.get("money_out")).strip() != "" else 0.0
                    cursor.execute("""
                        INSERT INTO balance_history 
                        (date, budget_starting, money_in, money_out, reason)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        str(row.get("date", "")).strip(),
                        float(row.get("budget_starting", 0.0)),
                        m_in,
                        m_out,
                        str(row.get("reason", "")).strip() if pd.notna(row.get("reason")) else ""
                    ))
                logger.info(f"Seeded {len(df)} balance records from {bal_csv}")
            except Exception as e:
                logger.warning(f"Could not seed balance history from CSV: {e}")

        # Check sales
        cursor.execute("SELECT COUNT(*) FROM sales;")
        sales_count = cursor.fetchone()[0]
        sales_csv = os.path.join(BASE_DIR, "sales.csv")
        if sales_count == 0 and os.path.exists(sales_csv):
            try:
                df = pd.read_csv(sales_csv)
                for _, row in df.iterrows():
                    cursor.execute("""
                        INSERT OR IGNORE INTO sales 
                        (sale_id, product_id, product_name, quantity, price, sale_date, customer_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(row.get("sale_id", "")).strip(),
                        str(row.get("product_id", "")).strip(),
                        str(row.get("product_name", "")).strip(),
                        int(row.get("quantity", 1)),
                        float(row.get("price", 0.0)),
                        str(row.get("sale_date", "")).strip(),
                        str(row.get("customer_id", "C101")).strip()
                    ))
                logger.info(f"Seeded {len(df)} sales records from {sales_csv}")
            except Exception as e:
                logger.warning(f"Could not seed sales from CSV: {e}")


def export_to_csv_backup():
    """Exports active database tables back to CSV for dual backup and external auditing."""
    with db_session() as conn:
        inv_df = pd.read_sql_query("SELECT product_id, product_name, product_price, product_amount_left, product_amount_sold, date_sold, date_filled, product_type FROM inventory", conn)
        inv_df.to_csv(os.path.join(BASE_DIR, "inventory.csv"), index=False)

        sales_df = pd.read_sql_query("SELECT sale_id, product_id, product_name, quantity, price, sale_date, customer_id FROM sales", conn)
        sales_df.to_csv(os.path.join(BASE_DIR, "sales.csv"), index=False)

        bal_df = pd.read_sql_query("SELECT date, budget_starting, money_in, money_out, reason FROM balance_history", conn)
        bal_df.to_csv(os.path.join(BASE_DIR, "balance_history.csv"), index=False)
