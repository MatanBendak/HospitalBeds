from __future__ import annotations
from typing import Any
import json
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


@st.cache_data(ttl=300)
def get_attribute_options(attribute_id: int) -> list[str]:
    """Return sorted unique non-empty values stored for a selection-type attribute."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT value FROM hospital_attributes
        WHERE attribute_id = %s AND value IS NOT NULL AND value != ''
        ORDER BY value
        """,
        (attribute_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r["value"] for r in rows]


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
    get_attribute_options.clear()


def save_hospital_values(hospital_id: int, values: dict) -> None:
    """Upsert multiple attribute values at once, then recalculate all formula attributes."""
    conn = get_connection()
    cur = conn.cursor()
    for attribute_id, value in values.items():
        cur.execute(
            """
            INSERT INTO hospital_attributes (hospital_id, attribute_id, value)
            VALUES (%s, %s, %s)
            ON CONFLICT (hospital_id, attribute_id) DO UPDATE SET value = EXCLUDED.value
            """,
            (hospital_id, attribute_id, str(value) if value is not None else ""),
        )
    conn.commit()
    cur.close()
    _recalculate_formulas(conn, hospital_id)
    conn.commit()
    conn.close()
    get_hospital_values.clear()
    get_all_hospital_values.clear()
    get_attribute_options.clear()


def update_hospital_attribute(hospital_id: int, attribute_id: int, value: Any) -> None:
    """Update a single attribute value and recalculate all formula attributes."""
    save_hospital_values(hospital_id, {attribute_id: value})


# ---------------------------------------------------------------------------
# Internal calculation
# ---------------------------------------------------------------------------

def _recalculate_formulas(conn: Any, hospital_id: int) -> None:
    """Recalculate all percentage and calculated attributes for a hospital."""
    cur = conn.cursor()

    # Get all formula-based attributes
    cur.execute("""
        SELECT id, data_type, formula
        FROM attributes
        WHERE data_type IN ('percentage', 'calculated')
          AND formula IS NOT NULL AND formula != ''
    """)
    formula_attrs = cur.fetchall()

    if not formula_attrs:
        cur.close()
        return

    # Current values for this hospital
    cur.execute(
        "SELECT attribute_id, value FROM hospital_attributes WHERE hospital_id = %s",
        (hospital_id,),
    )
    values = {r["attribute_id"]: r["value"] for r in cur.fetchall()}

    for attr in formula_attrs:
        try:
            formula = json.loads(attr["formula"])
            a_val = float(values.get(formula["a_id"]) or 0)
            b_val = float(values.get(formula["b_id"]) or 0)

            if attr["data_type"] == "percentage":
                result = round(a_val / b_val * 100, 2) if b_val != 0 else 0.0
            else:  # calculated
                op = formula.get("op", "+")
                if op == "+":
                    result = a_val + b_val
                elif op == "-":
                    result = a_val - b_val
                elif op == "*":
                    result = round(a_val * b_val, 4)
                elif op == "/":
                    result = round(a_val / b_val, 4) if b_val != 0 else 0.0
                else:
                    result = 0.0
        except Exception:
            result = 0.0

        cur.execute(
            """
            INSERT INTO hospital_attributes (hospital_id, attribute_id, value)
            VALUES (%s, %s, %s)
            ON CONFLICT (hospital_id, attribute_id) DO UPDATE SET value = EXCLUDED.value
            """,
            (hospital_id, attr["id"], str(result)),
        )

    cur.close()
