import psycopg2
import psycopg2.extras
import psycopg2.pool
import streamlit as st


class _PooledConnection:
    """Thin wrapper so that conn.close() returns the connection to the pool."""

    def __init__(self, pool: psycopg2.pool.ThreadedConnectionPool, conn):
        self._pool = pool
        self._conn = conn

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        # Roll back any uncommitted state before returning to pool
        try:
            self._conn.rollback()
        except Exception:
            pass
        self._pool.putconn(self._conn)


@st.cache_resource
def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Create (once) and cache a thread-safe connection pool for the app lifetime."""
    db_url = st.secrets["DATABASE_URL"]
    return psycopg2.pool.ThreadedConnectionPool(
        1, 5, db_url, cursor_factory=psycopg2.extras.RealDictCursor
    )


def get_connection() -> _PooledConnection:
    """Return a pooled connection. Call .close() when done to return it to the pool."""
    pool = _get_pool()
    return _PooledConnection(pool, pool.getconn())


def init_db() -> None:
    """Create tables and seed default attributes if they don't exist."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS hospitals (
            id         SERIAL PRIMARY KEY,
            name       TEXT   NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attributes (
            id            SERIAL  PRIMARY KEY,
            name          TEXT    NOT NULL UNIQUE,
            data_type     TEXT    NOT NULL CHECK(data_type IN ('numeric', 'text')),
            is_calculated INTEGER NOT NULL DEFAULT 0,
            is_default    INTEGER NOT NULL DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS hospital_attributes (
            hospital_id  INTEGER NOT NULL REFERENCES hospitals(id)  ON DELETE CASCADE,
            attribute_id INTEGER NOT NULL REFERENCES attributes(id) ON DELETE CASCADE,
            value        TEXT,
            PRIMARY KEY (hospital_id, attribute_id)
        )
    """)

    # Seed the three default attributes once
    defaults = [
        ("Total Beds",        "numeric", 0, 1),
        ("Beds in Shelter",   "numeric", 0, 1),
        ("% Beds in Shelter", "numeric", 1, 1),
    ]
    for name, dtype, is_calc, is_def in defaults:
        cur.execute(
            """
            INSERT INTO attributes (name, data_type, is_calculated, is_default)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (name, dtype, is_calc, is_def),
        )

    conn.commit()
    cur.close()
    conn.close()
