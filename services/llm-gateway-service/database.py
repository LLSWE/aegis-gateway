import sqlite3
import time
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "rate_limit.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT NOT NULL,
            timestamp REAL NOT NULL,
            tokens INTEGER NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_time ON token_usage(api_key, timestamp)")
    conn.commit()
    conn.close()

def check_and_record_rate_limit(api_key: str, estimated_tokens: int, limit: int = 50000, window_seconds: int = 60) -> tuple[bool, int]:
    init_db()
    now = time.time()
    window_start = now - window_seconds

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM token_usage WHERE timestamp < ?", (window_start,))
    cursor.execute("SELECT SUM(tokens) FROM token_usage WHERE api_key = ? AND timestamp >= ?", (api_key, window_start))
    row = cursor.fetchone()
    current_usage = row[0] if row and row[0] is not None else 0

    if current_usage + estimated_tokens > limit:
        conn.close()
        return False, current_usage

    cursor.execute("INSERT INTO token_usage (api_key, timestamp, tokens) VALUES (?, ?, ?)", (api_key, now, estimated_tokens))
    conn.commit()
    conn.close()
    return True, current_usage + estimated_tokens
