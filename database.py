"""SQLite Database Access and Schema Management Module for OmaTasku.

Handles user registrations, Piano ID __tac session cookie storage, and persistent
premium MP3 file size caching in a local, performance-optimized SQLite database.
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional

DB_DIR = os.getenv("DB_DIR", ".")
DB_NAME = os.getenv("DB_NAME", "omatasku.db")

# If DB_PATH is explicitly set, use it; otherwise, combine DB_DIR and DB_NAME
DB_PATH = os.getenv("DB_PATH")
if not DB_PATH:
    DB_PATH = os.path.join(DB_DIR, DB_NAME)


def ensure_db_dir_exists():
    """Defensively ensures that the parent directory of the database file exists on disk."""
    db_dir_parent = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir_parent and not os.path.exists(db_dir_parent):
        try:
            os.makedirs(db_dir_parent, exist_ok=True)
        # pylint: disable=broad-except
        except Exception as e:
            print(f"OmaTasku Warning: Could not create database directory {db_dir_parent}: {e}")


def update_db_path(new_path: str):
    """Updates the global database path dynamically and ensures its parent directory exists."""
    global DB_PATH  # pylint: disable=global-statement
    DB_PATH = new_path
    ensure_db_dir_exists()


# Initialize parent directory
ensure_db_dir_exists()


def get_connection():
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initializes the SQLite database schema if tables do not exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                uuid TEXT PRIMARY KEY,
                comment TEXT,
                tac_cookie TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_sizes (
                public_url TEXT PRIMARY KEY,
                content_length TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def get_file_size(public_url: str) -> Optional[str]:
    """Retrieves a cached file size by the public media URL from the SQLite database."""
    with get_connection() as conn:
        query = "SELECT content_length FROM file_sizes WHERE public_url = ?"
        row = conn.execute(query, (public_url,)).fetchone()
        if row:
            return row[0]
    return None


def save_file_size(public_url: str, content_length: str) -> None:
    """Caches a resolved file size permanently in the database, using public_url as the key."""
    now_str = datetime.now().isoformat()
    with get_connection() as conn:
        query = (
            "INSERT OR REPLACE INTO file_sizes "
            "(public_url, content_length, created_at) VALUES (?, ?, ?)"
        )
        conn.execute(query, (public_url, content_length, now_str))
        conn.commit()


def create_user(uuid: str, tac_cookie: str, comment: str = None) -> dict:
    """Inserts a new user mapping into the database."""
    now_str = datetime.now().isoformat()
    with get_connection() as conn:
        query = (
            "INSERT INTO users (uuid, comment, tac_cookie, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        conn.execute(query, (uuid, comment, tac_cookie, now_str, now_str))
        conn.commit()
    return get_user(uuid)


def get_user(uuid: str) -> dict:
    """Retrieves an existing user by UUID."""
    with get_connection() as conn:
        query = "SELECT * FROM users WHERE uuid = ?"
        row = conn.execute(query, (uuid,)).fetchone()
        if row:
            return dict(row)
    return None


def update_user(uuid: str, tac_cookie: str, comment: str = None) -> dict:
    """Updates the __tac cookie and optional comment for an existing user UUID."""
    now_str = datetime.now().isoformat()
    with get_connection() as conn:
        query = (
            "UPDATE users SET tac_cookie = ?, "
            "comment = COALESCE(?, comment), updated_at = ? WHERE uuid = ?"
        )
        conn.execute(query, (tac_cookie, comment, now_str, uuid))
        conn.commit()
    return get_user(uuid)


def get_session_count() -> int:
    """Returns the total count of user sessions registered in the database."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        if row:
            return row[0]
    return 0


def get_last_registration_timestamp() -> float:
    """Returns the epoch timestamp of the last registered user session, or 0.0 if empty."""
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(created_at) FROM users").fetchone()
        if row and row[0]:
            try:
                dt = datetime.fromisoformat(row[0])
                return dt.timestamp()
            # pylint: disable=broad-except
            except Exception:
                pass
    return 0.0
