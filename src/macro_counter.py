from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
import streamlit as st

MACRO_COLS = ["calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g", "sugar_g"]
DEFAULT_TARGETS = {
    "calories": 2000,
    "protein_g": 150,
    "carbs_g": 200,
    "fat_g": 70,
}


def coerce_macro_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with macro columns coerced to numeric values when present."""
    result = df.copy()
    for col in MACRO_COLS:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col].astype(str).str.replace("<", "", regex=False), errors="coerce").fillna(0)
    return result


def item_label(row: pd.Series) -> str:
    """Build a compact item label for select boxes and logs."""
    calories = int(row.get("calories", 0) or 0)
    protein = float(row.get("protein_g", 0) or 0)
    return f"{row.get('chain', '')} — {row.get('item_name', '')} ({calories} cal, {protein:g}g protein)"


def counter_entry_from_row(row: pd.Series, quantity: float = 1.0) -> dict[str, Any]:
    """Convert a menu row into a macro-counter log entry."""
    quantity = max(float(quantity), 0.0)
    entry: dict[str, Any] = {
        "entry_id": int(time.time() * 1000000),
        "quantity": quantity,
        "chain": row.get("chain", ""),
        "category": row.get("category", ""),
        "item_name": row.get("item_name", ""),
        "serving_size": row.get("serving_size", ""),
    }

    for col in MACRO_COLS:
        value = pd.to_numeric(row.get(col, 0), errors="coerce")
        if pd.isna(value):
            value = 0
        entry[col] = float(value) * quantity

    return entry


def totals_from_entries(entries: list[dict[str, Any]]) -> dict[str, float]:
    """Calculate macro totals from counter entries."""
    totals = {col: 0.0 for col in MACRO_COLS}
    for entry in entries:
        for col in MACRO_COLS:
            totals[col] += float(entry.get(col, 0) or 0)
    return totals


def remaining_from_totals(totals: dict[str, float], targets: dict[str, float]) -> dict[str, float]:
    """Calculate remaining target values for calories and core macros."""
    return {col: float(targets.get(col, 0) or 0) - float(totals.get(col, 0) or 0) for col in DEFAULT_TARGETS}


def _ensure_counter_state() -> None:
    if "macro_counter_items" not in st.session_state:
        st.session_state.macro_counter_items = []
    if "macro_profile_name" not in st.session_state:
        st.session_state.macro_profile_name = "Guest"
    for key, value in DEFAULT_TARGETS.items():
        target_key = f"macro_target_{key}"
        if target_key not in st.session_state:
            st.session_state[target_key] = value


def _current_targets() -> dict[str, float]:
    return {key: float(st.session_state.get(f"macro_target_{key}", default) or 0) for key, default in DEFAULT_TARGETS.items()}


def _download_payload() -> str:
    payload = {
        "profile_name": st.session_state.get("macro_profile_name", "Guest"),
        "targets": _current_targets(),
        "items": st.session_state.get("macro_counter_items", []),
    }
    return json.dumps(payload, indent=2)


def _load_profile_payload(uploaded_file) -> None:
    payload = json.loads(uploaded_file.read().decode("utf-8"))
    st.session_state.macro_profile_name = str(payload.get("profile_name", "Guest"))

    targets = payload.get("targets", {})
    for key, default in DEFAULT_TARGETS.items():
        st.session_state[f"macro_target_{key}"] = float(targets.get(key, default) or 0)

    st.session_state.macro_counter_items = list(payload.get("items", []))


def _render_profile_controls() -> None:
    st.subheader("Profile and goals")
    st.caption(
        "This prototype stores the profile in the current Streamlit session. "
        "Use download/upload to move a profile between visits until a real database is added."
    )

    profile_col, cal_col, protein_col, carb_col, fat_col = st.columns([1.4, 1, 1, 1, 1])
    profile_col.text_input("Profile name", key="macro_profile_name")
    cal_col.number_input("Calories", min_value=0, max_value=10000, step=50, key="macro_target_calories")
    protein_col.number_input("Protein (g)", min_value=0, max_value=500, step=5, key="macro_target_protein_g")
    carb_col.number_input("Carbs (g)", min_value=0, max_value=1000, step=5, key="macro_target_carbs_g")
    fat_col.number_input("Fat (g)", min_value=0, max_value=500, step=5, key="macro_target_fat_g")

    save_col, load_col = st.columns(2)
    save_col.download_button(
        "Download profile/day JSON",
        data=_download_payload().encode("utf-8"),
        file_name="macro_map_profile.json",
        mime="application/json",
        use_container_width=True,
    )
    uploaded_file = load_col.file_uploader("Load profile/day JSON", type="json")
    if uploaded_file is not None and load_col.button("Apply uploaded profile", use_container_width=True):
        try:
            _load_profile_payload(uploaded_file)
            st.success("Profile loaded.")
            st.rerun()
        except Exception as exc:  # noqa: BLE001 - show user-friendly upload error
            st.error(f"Could not load that profile file: {exc}")


def _render_add_items(menu_items: pd.DataFrame) -> None:
    st.subheader("Add items")
    menu_items = coerce_macro_columns(menu_items).reset_index(drop=True)
    menu_items["item_id"] = menu_items.index

    filter_col, search_col = st.columns([1, 1.4])
    chains = sorted(menu_items["chain"].dropna().astype(str).unique().tolist()) if "chain" in menu_items.columns else []
    selected_chains = filter_col.multiselect("Filter chains", chains, default=chains)
    search_text = search_col.text_input("Search menu items", placeholder="chicken, soup, salad...").strip().lower()

    filtered = menu_items.copy()
    if selected_chains and "chain" in filtered.columns:
        filtered = filtered[filtered["chain"].isin(selected_chains)]
    if search_text:
        search_blob = filtered[[col for col in ["chain", "category", "item_name"] if col in filtered.columns]].astype(str).agg(" ".join, axis=1).str.lower()
        filtered = filtered[search_blob.str.contains(search_text, na=False)]

    if filtered.empty:
        st.info("No items match those filters.")
        return

    add_cols = ["add", "quantity", "item_id", "chain", "category", "item_name", "calories", "protein_g", "carbs_g", "fat_g", "sodium_mg"]
    editable = filtered.copy()
    editable.insert(0, "quantity", 1.0)
    editable.insert(0, "add", False)
    present_cols = [col for col in add_cols if col in editable.columns]

    edited = st.data_editor(
        editable[present_cols],
        use_container_width=True,
        hide_index=True,
        height=360,
        disabled=[col for col in present_cols if col not in {"add", "quantity"}],
        column_config={
            "add": st.column_config.CheckboxColumn("Add"),
            "quantity": st.column_config.NumberColumn("Qty", min_value=0.25, max_value=20.0, step=0.25),
            "item_id": None,
            "chain": "Chain",
            "category": "Category",
            "item_name": "Item",
            "calories": "Calories",
            "protein_g": "Protein (g)",
            "carbs_g": "Carbs (g)",
            "fat_g": "Fat (g)",
            "sodium_mg": "Sodium (mg)",
        },
    )

    if st.button("Add checked items to macro counter", type="primary", use_container_width=True):
        selected = edited[edited["add"] == True]  # noqa: E712 - pandas boolean mask
        if selected.empty:
            st.warning("Check at least one item first.")
            return

        entries = st.session_state.macro_counter_items
        for _, selected_row in selected.iterrows():
            item_id = int(selected_row["item_id"])
            source_row = menu_items.loc[item_id]
            entries.append(counter_entry_from_row(source_row, selected_row.get("quantity", 1.0)))
        st.session_state.macro_counter_items = entries
        st.success(f"Added {len(selected)} item(s).")
        st.rerun()


def _render_totals() -> None:
    st.subheader("Today totals")
    entries = st.session_state.macro_counter_items
    totals = totals_from_entries(entries)
    remaining = remaining_from_totals(totals, _current_targets())

    total_cols = st.columns(4)
    total_cols[0].metric("Calories", f"{totals['calories']:.0f}", f"{remaining['calories']:.0f} left")
    total_cols[1].metric("Protein", f"{totals['protein_g']:.0f}g", f"{remaining['protein_g']:.0f}g left")
    total_cols[2].metric("Carbs", f"{totals['carbs_g']:.0f}g", f"{remaining['carbs_g']:.0f}g left")
    total_cols[3].metric("Fat", f"{totals['fat_g']:.0f}g", f"{remaining['fat_g']:.0f}g left")

    if not entries:
        st.info("No items added yet. Check items above and add them to start counting.")
        return

    log = pd.DataFrame(entries)
    display_cols = ["remove", "quantity", "chain", "item_name", "calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "entry_id"]
    log.insert(0, "remove", False)
    edited_log = st.data_editor(
        log[[col for col in display_cols if col in log.columns]],
        use_container_width=True,
        hide_index=True,
        disabled=[col for col in display_cols if col != "remove"],
        column_config={
            "remove": st.column_config.CheckboxColumn("Remove"),
            "entry_id": None,
            "quantity": "Qty",
            "chain": "Chain",
            "item_name": "Item",
            "calories": "Calories",
            "protein_g": "Protein (g)",
            "carbs_g": "Carbs (g)",
            "fat_g": "Fat (g)",
            "sodium_mg": "Sodium (mg)",
        },
    )

    remove_col, clear_col = st.columns(2)
    if remove_col.button("Remove checked items", use_container_width=True):
        remove_ids = set(edited_log.loc[edited_log["remove"] == True, "entry_id"].tolist())  # noqa: E712
        st.session_state.macro_counter_items = [entry for entry in entries if entry.get("entry_id") not in remove_ids]
        st.rerun()
    if clear_col.button("Clear counter", use_container_width=True):
        st.session_state.macro_counter_items = []
        st.rerun()


def render_macro_counter(menu_items: pd.DataFrame) -> None:
    """Render an in-session macro counter for selected-area menu items."""
    _ensure_counter_state()
    st.caption("Prototype: add menu items from the selected-chain results and track calories/macros against simple goals.")
    _render_profile_controls()
    st.divider()
    _render_add_items(menu_items)
    st.divider()
    _render_totals()
