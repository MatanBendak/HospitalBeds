from __future__ import annotations
import streamlit as st
from database import get_connection


@st.cache_data(ttl=300)
def get_all_attributes() -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, data_type, is_calculated, is_default FROM attributes ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def add_attribute(name: str, data_type: str) -> int:
    """Create a new attribute and add empty rows for every existing hospital."""
    name = name.strip()
    if data_type not in ("numeric", "text"):
        raise ValueError("data_type must be 'numeric' or 'text'")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO attributes (name, data_type, is_calculated, is_default) VALUES (%s, %s, 0, 0) RETURNING id",
        (name, data_type),
    )
    attribute_id = cur.fetchone()["id"]

    cur.execute("SELECT id FROM hospitals")
    hospitals = cur.fetchall()
    for h in hospitals:
        cur.execute(
            """
            INSERT INTO hospital_attributes (hospital_id, attribute_id, value)
            VALUES (%s, %s, '')
            ON CONFLICT (hospital_id, attribute_id) DO NOTHING
            """,
            (h["id"], attribute_id),
        )

    conn.commit()
    cur.close()
    conn.close()
    get_all_attributes.clear()
    return attribute_id


def delete_attribute(attribute_id: int) -> None:
    """Delete an attribute and all its associated hospital values."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM attributes WHERE id = %s", (attribute_id,))
    conn.commit()
    cur.close()
    conn.close()
    get_all_attributes.clear()
