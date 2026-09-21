import sqlite3
import hashlib
import os
import logging
from core.db import get_db_connection, db_session

logger = logging.getLogger("audit_system.auth")

ADMIN_SETUP = [
    # (username, plaintext_password, role)
    # Passwords stored as SHA-256 hex digests – never in plaintext.
    ("admin",  "admin123",      "admin"),
    ("john",   "password1",     "cashier"),
    ("sarah",  "securePass99",  "cashier"),
]


def _sha256(plain: str) -> str:
    """Return the SHA-256 hex digest of a UTF-8 string."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def init_users():
    """
    Create the users table and seed default accounts if the table is empty.
    Run once on application startup.
    """
    with db_session() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT    NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role      TEXT    NOT NULL DEFAULT 'cashier',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor = conn.execute("SELECT COUNT(*) FROM users;")
        if cursor.fetchone()[0] == 0:
            for uname, plain, role in ADMIN_SETUP:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?);",
                    (uname, _sha256(plain), role)
                )
            logger.info("Default users seeded (admin + cashiers).")


def verify_login(username: str, password_hash: str):
    """
    Verify a login attempt given the username and the SHA-256 hash
    of the password (computed in the browser before submission).

    Returns a dict  {id, username, role}  on success, or None.
    """
    with db_session() as conn:
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE username = ? AND password_hash = ?;",
            (username.strip(), password_hash.strip())
        ).fetchone()
        if row:
            return dict(row)
        return None


def get_user(username: str):
    """Return user row dict or None."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE username = ?;",
            (username.strip(),)
        ).fetchone()
        return dict(row) if row else None


def list_users():
    """Return all users (without password hashes)."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id ASC;"
        ).fetchall()
        return [dict(r) for r in rows]


def add_user(username: str, plain_password: str, role: str = "cashier"):
    """Add a new user with a hashed password."""
    with db_session() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?);",
            (username.strip(), _sha256(plain_password), role)
        )
    logger.info(f"User '{username}' ({role}) added.")


def delete_user(username: str):
    """Remove a user account."""
    with db_session() as conn:
        conn.execute("DELETE FROM users WHERE username = ?;", (username.strip(),))
    logger.info(f"User '{username}' deleted.")


def change_password(username: str, new_plain: str):
    """Update a user's password (accepts plaintext, stores hash)."""
    with db_session() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?;",
            (_sha256(new_plain), username.strip())
        )
    logger.info(f"Password updated for '{username}'.")
