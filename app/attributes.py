from __future__ import annotations
import json
import streamlit as st
from database import get_connection

ALL_DATA_TYPES = ("numeric", "text", "selection", "percentage", "calculated")


@st.cache_data(ttl=300)
def get_all_attributes() -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, data_type, is_calculated, is_default, formula FROM attributes ORDER BY id"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("formula"):
            try:
                d["formula"] = json.loads(d["formula"])
            except Exception:
                d["formula"] = None
        else:
            d["formula"] = None
        result.append(d)
    return result


def add_attribute(name: str, data_type: str, formula: dict | None = None) -> int:
    """Create a new attribute and add empty rows for every existing hospital."""
    name = name.strip()
    if data_type not in ALL_DATA_TYPES:
        raise ValueError(f"data_type must be one of {ALL_DATA_TYPES}")

    formula_json = json.dumps(formula) if formula else None
    is_calculated = 1 if data_type in ("percentage", "calculated") else 0

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO attributes (name, data_type, is_calculated, is_default, formula) "
        "VALUES (%s, %s, %s, 0, %s) RETURNING id",
        (name, data_type, is_calculated, formula_json),
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
