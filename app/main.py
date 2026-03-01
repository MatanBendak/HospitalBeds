from __future__ import annotations

import sys
import os

# Allow imports from the app/ directory regardless of working directory
sys.path.insert(0, os.path.dirname(__file__))

import io
import pandas as pd
import streamlit as st

from database import init_db
from hospitals import (
    get_all_hospitals,
    get_all_hospital_values,
    get_hospital_values,
    get_attribute_options,
    add_hospital,
    delete_hospital,
    save_hospital_values,
    update_hospital_attribute,
)
from attributes import get_all_attributes, add_attribute, delete_attribute

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Hospital Resource Tracker", page_icon="🏥", layout="wide")
st.title("🏥 Hospital Resource Tracker")


@st.cache_resource
def _init_db_once():
    """Run DB bootstrap exactly once for the lifetime of the app process."""
    init_db()
    return True


try:
    _init_db_once()
except Exception as _db_err:
    st.error(
        "⚠️ Could not connect to the database.\n\n"
        "Make sure `.streamlit/secrets.toml` contains a valid `DATABASE_URL`.\n\n"
        f"Error: {_db_err}"
    )
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
page = st.sidebar.radio(
    "Navigation",
    ["Manage Hospitals", "Hospital Details", "Manage Attributes", "Summary Table"],
)

# ---------------------------------------------------------------------------
# Helper: build summary DataFrame
# ---------------------------------------------------------------------------

def build_summary_df() -> pd.DataFrame:
    hospitals = get_all_hospitals()
    attributes = get_all_attributes()

    if not hospitals or not attributes:
        return pd.DataFrame()

    # Only numeric, percentage, and calculated columns belong in the summary
    summary_attrs = [
        a for a in attributes
        if a["data_type"] in ("numeric", "percentage", "calculated")
    ]
    if not summary_attrs:
        return pd.DataFrame()

    col_names = [a["name"] for a in summary_attrs]
    all_values = get_all_hospital_values()
    rows = []
    for h in hospitals:
        vals = all_values.get(h["id"], {})
        row = {"Hospital": h["name"]}
        for a in summary_attrs:
            raw = vals.get(a["id"], "")
            try:
                row[a["name"]] = float(raw) if raw not in ("", None) else None
            except ValueError:
                row[a["name"]] = None
        rows.append(row)

    df = pd.DataFrame(rows, columns=["Hospital"] + col_names)

    # --- Totals row ---
    # Step 1: sum numeric columns
    col_sums: dict = {}  # attr_id -> sum
    totals: dict = {"Hospital": "TOTAL"}
    for a in summary_attrs:
        if a["data_type"] == "numeric":
            s = df[a["name"]].sum(skipna=True)
            col_sums[a["id"]] = s
            totals[a["name"]] = s

    # Step 2: formula-based totals (percentage / calculated) derived from numeric sums
    for a in summary_attrs:
        if a["data_type"] in ("percentage", "calculated") and a.get("formula"):
            formula = a["formula"]
            a_sum = col_sums.get(formula.get("a_id"), 0) or 0
            b_sum = col_sums.get(formula.get("b_id"), 0) or 0
            try:
                if a["data_type"] == "percentage":
                    totals[a["name"]] = round(a_sum / b_sum * 100, 2) if b_sum != 0 else 0.0
                else:
                    op = formula.get("op", "+")
                    if op == "+":
                        totals[a["name"]] = a_sum + b_sum
                    elif op == "-":
                        totals[a["name"]] = a_sum - b_sum
                    elif op == "*":
                        totals[a["name"]] = round(a_sum * b_sum, 4)
                    elif op == "/":
                        totals[a["name"]] = round(a_sum / b_sum, 4) if b_sum != 0 else 0.0
                    else:
                        totals[a["name"]] = ""
            except Exception:
                totals[a["name"]] = ""
        elif a["data_type"] not in ("numeric",):
            totals[a["name"]] = ""

    df = pd.concat([df, pd.DataFrame([totals])], ignore_index=True)
    return df


# ===========================================================================
# Page: Manage Hospitals
# ===========================================================================
if page == "Manage Hospitals":
    st.header("Manage Hospitals")

    # Add hospital form
    with st.form("add_hospital_form", clear_on_submit=True):
        new_name = st.text_input("New hospital name")
        submitted = st.form_submit_button("Add Hospital")
        if submitted:
            if not new_name.strip():
                st.error("Hospital name cannot be empty.")
            else:
                try:
                    add_hospital(new_name)
                    st.success(f"Hospital '{new_name.strip()}' added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()

    hospitals = get_all_hospitals()
    if not hospitals:
        st.info("No hospitals yet. Add one above.")
    else:
        st.subheader("Existing Hospitals")
        for h in hospitals:
            col1, col2 = st.columns([4, 1])
            col1.write(f"**{h['name']}**")
            with col2:
                if st.button("🗑 Delete", key=f"del_h_{h['id']}"):
                    st.session_state[f"confirm_del_h_{h['id']}"] = True

            if st.session_state.get(f"confirm_del_h_{h['id']}"):
                st.warning(
                    f"⚠️ Are you sure you want to delete **{h['name']}**? "
                    "All data for this hospital will be permanently removed."
                )
                c1, c2 = st.columns(2)
                if c1.button("Yes, delete", key=f"yes_del_h_{h['id']}"):
                    delete_hospital(h["id"])
                    st.session_state.pop(f"confirm_del_h_{h['id']}", None)
                    st.success(f"'{h['name']}' deleted.")
                    st.rerun()
                if c2.button("Cancel", key=f"cancel_del_h_{h['id']}"):
                    st.session_state.pop(f"confirm_del_h_{h['id']}", None)
                    st.rerun()


# ===========================================================================
# Page: Hospital Details
# ===========================================================================
elif page == "Hospital Details":
    st.header("Hospital Details")

    hospitals = get_all_hospitals()
    if not hospitals:
        st.info("No hospitals yet. Go to 'Manage Hospitals' to add one.")
    else:
        hospital_names = [h["name"] for h in hospitals]
        selected_name = st.selectbox("Select hospital", hospital_names)
        hospital = next(h for h in hospitals if h["name"] == selected_name)

        attributes = get_all_attributes()
        current_values = get_hospital_values(hospital["id"])

        st.subheader(f"Attributes — {hospital['name']}")

        with st.form(f"hospital_form_{hospital['id']}"):
            new_vals: dict[int, object] = {}

            for attr in attributes:
                raw = current_values.get(attr["id"], "")
                label = attr["name"]
                is_auto = attr["is_calculated"] or attr["data_type"] in ("percentage", "calculated")

                if is_auto:
                    # Read-only display for auto-calculated fields
                    display_val = raw if raw not in ("", None) else "—"
                    if attr["data_type"] == "percentage":
                        display_val = f"{display_val}%" if display_val != "—" else "—"
                    st.text_input(
                        f"{label} (auto-calculated)",
                        value=display_val,
                        disabled=True,
                        key=f"readonly_{attr['id']}",
                    )
                    new_vals[attr["id"]] = None  # skip saving
                elif attr["data_type"] == "numeric":
                    # Use None when no value has been set yet (issue #6)
                    try:
                        numeric_val = float(raw) if raw not in ("", None) else None
                    except ValueError:
                        numeric_val = None
                    new_vals[attr["id"]] = st.number_input(
                        label,
                        value=numeric_val,
                        step=1.0,
                        format="%g",
                        key=f"attr_{attr['id']}",
                    )
                elif attr["data_type"] == "selection":
                    existing_opts = get_attribute_options(attr["id"])
                    current_val = raw or ""
                    if current_val in existing_opts:
                        sel_idx = existing_opts.index(current_val) + 1
                        custom_default = ""
                    else:
                        sel_idx = 0
                        custom_default = current_val
                    selected = st.selectbox(
                        label,
                        options=[""] + existing_opts,
                        index=sel_idx,
                        key=f"sel_{attr['id']}",
                    )
                    custom_val = st.text_input(
                        f"↳ New value for \"{label}\" (type here to add a new option)",
                        value=custom_default,
                        placeholder="Leave blank to use the selection above",
                        key=f"sel_new_{attr['id']}",
                    )
                    new_vals[attr["id"]] = custom_val.strip() if custom_val.strip() else selected
                else:  # text
                    new_vals[attr["id"]] = st.text_input(
                        label,
                        value=raw or "",
                        key=f"attr_{attr['id']}",
                    )

            save = st.form_submit_button("💾 Save")
            if save:
                vals_to_save = {
                    attr["id"]: new_vals.get(attr["id"])
                    for attr in attributes
                    if not (attr["is_calculated"] or attr["data_type"] in ("percentage", "calculated"))
                }
                save_hospital_values(hospital["id"], vals_to_save)
                st.success("Saved successfully.")
                st.rerun()


# ===========================================================================
# Page: Manage Attributes
# ===========================================================================
elif page == "Manage Attributes":
    st.header("Manage Attributes")

    # ---- Add attribute ----
    st.subheader("Add Attribute")

    all_attrs_now = get_all_attributes()
    numeric_attrs = [a for a in all_attrs_now if a["data_type"] == "numeric"]
    numeric_names = [a["name"] for a in numeric_attrs]

    new_type = st.selectbox(
        "Type",
        ["numeric", "text", "selection", "calculated", "percentage"],
        key="add_attr_type",
        help="calculated = A ± B  |  percentage = (A ÷ B) × 100%",
    )

    formula: dict | None = None
    if new_type in ("calculated", "percentage"):
        if not numeric_attrs:
            st.warning("⚠️ You need at least one numeric attribute before creating a formula attribute.")
        else:
            if new_type == "calculated":
                c1, c2, c3 = st.columns(3)
                a_name = c1.selectbox("Attribute A", numeric_names, key="calc_a")
                op_display = c2.selectbox("Operator", ["+", "−", "×", "÷"], key="calc_op")
                b_name = c3.selectbox("Attribute B", numeric_names, key="calc_b")
                op_map = {"+": "+", "−": "-", "×": "*", "÷": "/"}
                a_id = next(a["id"] for a in numeric_attrs if a["name"] == a_name)
                b_id = next(a["id"] for a in numeric_attrs if a["name"] == b_name)
                formula = {"a_id": a_id, "op": op_map[op_display], "b_id": b_id}
                st.caption(f"Result = {a_name} {op_display} {b_name}")
            else:  # percentage
                c1, c2 = st.columns(2)
                num_name = c1.selectbox("Numerator", numeric_names, key="pct_num")
                den_name = c2.selectbox("Denominator", numeric_names, key="pct_den")
                a_id = next(a["id"] for a in numeric_attrs if a["name"] == num_name)
                b_id = next(a["id"] for a in numeric_attrs if a["name"] == den_name)
                formula = {"a_id": a_id, "b_id": b_id}
                st.caption(f"Result = ({num_name} ÷ {den_name}) × 100%")

    with st.form("add_attr_form", clear_on_submit=True):
        attr_name = st.text_input("Attribute name")
        add_submitted = st.form_submit_button("➕ Add Attribute")
        if add_submitted:
            if not attr_name.strip():
                st.error("Attribute name cannot be empty.")
            elif new_type in ("calculated", "percentage") and not formula:
                st.error("Please select source attributes for the formula above.")
            else:
                try:
                    add_attribute(attr_name.strip(), new_type, formula=formula)
                    st.success(f"Attribute '{attr_name.strip()}' added.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()

    attributes = get_all_attributes()
    st.subheader("All Attributes")
    attr_name_by_id = {a["id"]: a["name"] for a in attributes}

    for attr in attributes:
        col1, col2, col3 = st.columns([3, 1, 1])
        badge_map = {"numeric": "🔢", "text": "📝", "selection": "📋", "percentage": "💯", "calculated": "🧮"}
        badge = badge_map.get(attr["data_type"], "📝")
        calc_tag = " *(auto-calculated)*" if attr["is_calculated"] else ""
        formula_str = ""
        if attr.get("formula"):
            f = attr["formula"]
            a_lbl = attr_name_by_id.get(f.get("a_id"), "?")
            b_lbl = attr_name_by_id.get(f.get("b_id"), "?")
            if attr["data_type"] == "percentage":
                formula_str = f" *({a_lbl} ÷ {b_lbl} × 100%)*"
            else:
                op_disp = {"+ ": "+", "-": "−", "*": "×", "/": "÷"}.get(f.get("op", "+"), f.get("op", "+"))
                formula_str = f" *({a_lbl} {op_disp} {b_lbl})*"
        col1.markdown(f"{badge} **{attr['name']}**{calc_tag}{formula_str}")
        col2.write(attr["data_type"])

        with col3:
            if st.button("🗑 Delete", key=f"del_a_{attr['id']}"):
                st.session_state[f"confirm_del_a_{attr['id']}"] = True

        if st.session_state.get(f"confirm_del_a_{attr['id']}"):
            st.warning(
                f"⚠️ Deleting **{attr['name']}** will remove this data for **all hospitals** permanently."
            )
            confirmed = st.checkbox(
                "I understand this will delete data for all hospitals",
                key=f"chk_del_a_{attr['id']}",
            )
            c1, c2 = st.columns(2)
            if c1.button("Yes, delete", key=f"yes_del_a_{attr['id']}", disabled=not confirmed):
                delete_attribute(attr["id"])
                st.session_state.pop(f"confirm_del_a_{attr['id']}", None)
                st.session_state.pop(f"chk_del_a_{attr['id']}", None)
                st.success(f"Attribute '{attr['name']}' deleted.")
                st.rerun()
            if c2.button("Cancel", key=f"cancel_del_a_{attr['id']}"):
                st.session_state.pop(f"confirm_del_a_{attr['id']}", None)
                st.session_state.pop(f"chk_del_a_{attr['id']}", None)
                st.rerun()


# ===========================================================================
# Page: Summary Table
# ===========================================================================
elif page == "Summary Table":
    st.header("Summary Table")

    df = build_summary_df()

    if df.empty:
        st.info("No data yet. Add hospitals and fill in their attributes first.")
    else:
        # Highlight totals row
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Excel export
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Hospital Summary")
        buffer.seek(0)

        st.download_button(
            label="📥 Download Excel",
            data=buffer,
            file_name="hospital_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
