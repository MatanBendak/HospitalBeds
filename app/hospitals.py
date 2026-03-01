from __future__ import annotations
from typing import Any
import streamlit as st
from database import get_connection


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def get_all_hospitals() -> list[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, created_at FROM hospitals ORDER BY name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=300)
def get_hospital_values(hospital_id: int) -> dict[int, str]:
    """Return {attribute_id: value} for a given hospital."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT attribute_id, value FROM hospital_attributes WHERE hospital_id = %s",
        (hospital_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["attribute_id"]: r["value"] for r in rows}


@st.cache_data(ttl=300)
def get_all_hospital_values() -> dict[int, dict[int, str]]:
    """Return {hospital_id: {attribute_id: value}} for all hospitals in one query."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT hospital_id, attribute_id, value FROM hospital_attributes")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result: dict[int, dict[int, str]] = {}
    for r in rows:
        result.setdefault(r["hospital_id"], {})[r["attribute_id"]] = r["value"]
    return result


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def add_hospital(name: str) -> int:
    """Insert a hospital and create empty attribute rows for all attributes."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO hospitals (name) VALUES (%s) RETURNING id", (name.strip(),))
    hospital_id = cur.fetchone()["id"]

    cur.execute("SELECT id FROM attributes")
    attrs = cur.fetchall()
    for attr in attrs:
        cur.execute(
            """
            INSERT INTO hospital_attributes (hospital_id, attribute_id, value)
            VALUES (%s, %s, '')
            ON CONFLICT (hospital_id, attribute_id) DO NOTHING
            """,
            (hospital_id, attr["id"]),
        )

    conn.commit()
    cur.close()
    conn.close()
    get_all_hospitals.clear()
    get_all_hospital_values.clear()
    return hospital_id


def delete_hospital(hospital_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM hospitals WHERE id = %s", (hospital_id,))
    conn.commit()
    cur.close()
    conn.close()
    get_all_hospitals.clear()
    get_hospital_values.clear()
    get_all_hospital_values.clear()


def update_hospital_attribute(hospital_id: int, attribute_id: int, value: Any) -> None:
    """Update a single attribute value and auto-recalculate % Beds in Shelter."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO hospital_attributes (hospital_id, attribute_id, value)
        VALUES (%s, %s, %s)
        ON CONFLICT (hospital_id, attribute_id) DO UPDATE SET value = EXCLUDED.value
        """,
        (hospital_id, attribute_id, str(value) if value is not None else ""),
    )
    conn.commit()

    # Determine if the changed attribute is one of the two bed fields
    cur.execute("SELECT name FROM attributes WHERE id = %s", (attribute_id,))
    attr = cur.fetchone()

    if attr and attr["name"] in ("Total Beds", "Beds in Shelter"):
        _recalculate_percentage(conn, hospital_id)
        conn.commit()

    cur.close()
    conn.close()
    get_hospital_values.clear()
    get_all_hospital_values.clear()


# ---------------------------------------------------------------------------
# Internal calculation
# ---------------------------------------------------------------------------

def _recalculate_percentage(conn: Any, hospital_id: int) -> None:
    """Compute (Beds in Shelter / Total Beds * 100) and persist it."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.name, ha.value
        FROM hospital_attributes ha
        JOIN attributes a ON a.id = ha.attribute_id
        WHERE ha.hospital_id = %s
          AND a.name IN ('Total Beds', 'Beds in Shelter', '% Beds in Shelter')
        """,
        (hospital_id,),
    )
    rows = cur.fetchall()

    data = {r["name"]: r["value"] for r in rows}

    try:
        total = float(data.get("Total Beds") or 0)
        shelter = float(data.get("Beds in Shelter") or 0)
        pct = round((shelter / total * 100), 2) if total > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        pct = 0.0

    cur.execute("SELECT id FROM attributes WHERE name = '% Beds in Shelter'")
    pct_attr = cur.fetchone()

    if pct_attr:
        cur.execute(
            """
            INSERT INTO hospital_attributes (hospital_id, attribute_id, value)
            VALUES (%s, %s, %s)
            ON CONFLICT (hospital_id, attribute_id) DO UPDATE SET value = EXCLUDED.value
            """,
            (hospital_id, pct_attr["id"], str(pct)),
        )
    cur.close()
