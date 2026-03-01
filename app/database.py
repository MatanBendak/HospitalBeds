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
    """Create tables and run all schema migrations."""
    import json as _json
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

    # Migration: add formula column (idempotent)
    cur.execute("ALTER TABLE attributes ADD COLUMN IF NOT EXISTS formula TEXT DEFAULT NULL")

    # Migration: update data_type constraint to include all types
    cur.execute("""
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conname = 'attributes_data_type_check'
          AND conrelid = 'attributes'::regclass
    """)
    row = cur.fetchone()
    constraint_def = row[0] if row else ""
    if "percentage" not in constraint_def or "calculated" not in constraint_def:
        cur.execute("ALTER TABLE attributes DROP CONSTRAINT IF EXISTS attributes_data_type_check")
        cur.execute(
            "ALTER TABLE attributes ADD CONSTRAINT attributes_data_type_check "
            "CHECK(data_type IN ('numeric', 'text', 'selection', 'percentage', 'calculated'))"
        )

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

    # Migration: convert '% Beds in Shelter' to new percentage type with formula
    cur.execute("""
        SELECT id FROM attributes
        WHERE name = '%% Beds in Shelter' AND (formula IS NULL OR formula = '')
    """)
    pct_row = cur.fetchone()
    if pct_row:
        cur.execute("SELECT id FROM attributes WHERE name = 'Total Beds'")
        total_row = cur.fetchone()
        cur.execute("SELECT id FROM attributes WHERE name = 'Beds in Shelter'")
        shelter_row = cur.fetchone()
        if total_row and shelter_row:
            formula_json = _json.dumps({"a_id": shelter_row["id"], "b_id": total_row["id"]})
            cur.execute(
                "UPDATE attributes SET data_type = 'percentage', formula = %s, is_calculated = 1 WHERE id = %s",
                (formula_json, pct_row["id"]),
            )

    conn.commit()
    cur.close()
    conn.close()
