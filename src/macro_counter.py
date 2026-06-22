from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
import streamlit as st

MACRO_COLS = ["calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g", "sugar_g"]
CORE_TARGET_COLS = ["calories", "protein_g", "carbs_g", "fat_g"]
KCAL_PER_GRAM = {"protein_g": 4, "carbs_g": 4, "fat_g": 9}
DEFAULT_TARGETS = {
    "calories": 2000,
    "protein_g": 150,
    "carbs_g": 200,
    "fat_g": 70,
}
DIET_STYLE_PRESETS = {
    "Balanced": {
        "protein_pct": 0.30,
        "carbs_pct": 0.40,
        "fat_pct": 0.30,
        "description": "General-purpose split with moderate protein, carbs, and fat.",
    },
    "High protein": {
        "protein_pct": 0.35,
        "carbs_pct": 0.35,
        "fat_pct": 0.30,
        "description": "Higher protein while keeping enough carbs for training and daily energy.",
    },
    "Lower carb": {
        "protein_pct": 0.35,
        "carbs_pct": 0.25,
        "fat_pct": 0.40,
        "description": "Higher protein and fat with fewer carbs, but not strict keto.",
    },
    "Keto-style": {
        "protein_pct": 0.25,
        "carbs_pct": 0.05,
        "fat_pct": 0.70,
        "description": "Very low carb, high fat. Restrictive; best treated as a specialty preset.",
    },
    "Endurance": {
        "protein_pct": 0.25,
        "carbs_pct": 0.55,
        "fat_pct": 0.20,
        "description": "Higher-carb split for users who want more training fuel.",
    },
}
PROTEIN_ACTIVITY_FACTORS_G_PER_KG = {
    "Sedentary / general health": 0.8,
    "Light activity": 1.4,
    "Moderate training": 1.6,
    "Heavy training": 1.8,
    "Cutting / lean-mass focus": 2.0,
}


def coerce_macro_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with macro columns coerced to numeric values when present."""
    result = df.copy()
    for col in MACRO_COLS:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col].astype(str).str.replace("<", "", regex=False), errors="coerce").fillna(0)
    return result


def targets_from_percentages(calories: float, preset: dict[str, float | str]) -> dict[str, float]:
    """Convert calorie percentages into grams of protein, carbs, and fat."""
    calories = max(float(calories), 0.0)
    return {
        "calories": round(calories),
        "protein_g": round(calories * float(preset["protein_pct"]) / KCAL_PER_GRAM["protein_g"]),
        "carbs_g": round(calories * float(preset["carbs_pct"]) / KCAL_PER_GRAM["carbs_g"]),
        "fat_g": round(calories * float(preset["fat_pct"]) / KCAL_PER_GRAM["fat_g"]),
    }


def body_weight_protein_target(weight_lbs: float, activity_level: str) -> float:
    """Return protein grams from body weight and selected activity level."""
    weight_kg = max(float(weight_lbs), 0.0) / 2.20462
    protein_factor = PROTEIN_ACTIVITY_FACTORS_G_PER_KG[activity_level]
    return round(weight_kg * protein_factor)


def targets_from_body_weight(calories: float, weight_lbs: float, activity_level: str, diet_style: str) -> dict[str, float]:
    """Set protein from body weight and split remaining calories across carbs/fat by style."""
    calories = max(float(calories), 0.0)
    protein_g = body_weight_protein_target(weight_lbs, activity_level)
    protein_calories = protein_g * KCAL_PER_GRAM["protein_g"]

    # Keep the target possible if the user's calorie target is very low.
    if protein_calories > calories and calories > 0:
        protein_g = round(calories * 0.45 / KCAL_PER_GRAM["protein_g"])
        protein_calories = protein_g * KCAL_PER_GRAM["protein_g"]

    remaining_calories = max(calories - protein_calories, 0.0)
    preset = DIET_STYLE_PRESETS[diet_style]
    carb_pct = float(preset["carbs_pct"])
    fat_pct = float(preset["fat_pct"])
    carb_fat_total = max(carb_pct + fat_pct, 0.001)
    carbs_g = round(remaining_calories * (carb_pct / carb_fat_total) / KCAL_PER_GRAM["carbs_g"])
    fat_g = round(remaining_calories * (fat_pct / carb_fat_total) / KCAL_PER_GRAM["fat_g"])

    return {
        "calories": round(calories),
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }


def apply_macro_targets(targets: dict[str, float], preset_name: str | None = None) -> None:
    """Apply calculated macro targets into Streamlit session state.

    This is intended to run as a Streamlit widget callback, before the target
    number_input widgets are instantiated on the rerun. Updating these keys
    inline after the widgets exist causes StreamlitAPIException.
    """
    for key in CORE_TARGET_COLS:
        st.session_state[f"macro_target_{key}"] = float(targets.get(key, 0) or 0)
    if preset_name:
        st.session_state.macro_last_preset = preset_name


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
    if "macro_body_weight_lbs" not in st.session_state:
        st.session_state.macro_body_weight_lbs = 180.0
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
    st.session_state.macro_profile_loaded = True


def _render_preset_buttons() -> None:
    st.subheader("Macro target presets")
    st.caption(
        "These buttons use your current calorie target and split those calories into protein, carbs, and fat. "
        "They are starting points, not medical advice."
    )

    calories = float(st.session_state.get("macro_target_calories", DEFAULT_TARGETS["calories"]) or 0)
    preset_cols = st.columns(len(DIET_STYLE_PRESETS))
    for idx, (name, preset) in enumerate(DIET_STYLE_PRESETS.items()):
        targets = targets_from_percentages(calories, preset)
        button_label = f"{name}\n{targets['protein_g']:.0f}P / {targets['carbs_g']:.0f}C / {targets['fat_g']:.0f}F"
        preset_cols[idx].button(
            button_label,
            key=f"macro_preset_{name}",
            on_click=apply_macro_targets,
            args=(targets, name),
            width="stretch",
        )

    with st.expander("Preset details", expanded=False):
        rows = []
        for name, preset in DIET_STYLE_PRESETS.items():
            targets = targets_from_percentages(calories, preset)
            rows.append(
                {
                    "Style": name,
                    "Calories": targets["calories"],
                    "Protein %": f"{float(preset['protein_pct']) * 100:.0f}%",
                    "Carbs %": f"{float(preset['carbs_pct']) * 100:.0f}%",
                    "Fat %": f"{float(preset['fat_pct']) * 100:.0f}%",
                    "Protein (g)": targets["protein_g"],
                    "Carbs (g)": targets["carbs_g"],
                    "Fat (g)": targets["fat_g"],
                    "Notes": preset["description"],
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_body_weight_helper() -> None:
    with st.expander("Body-weight protein helper", expanded=False):
        st.caption(
            "This keeps your current calorie target, sets protein from body weight/activity, "
            "then splits the remaining calories between carbs and fat using the selected diet style."
        )

        input_cols = st.columns([1, 1.35, 1])
        weight_lbs = input_cols[0].number_input(
            "Body weight (lb)",
            min_value=50.0,
            max_value=600.0,
            step=1.0,
            key="macro_body_weight_lbs",
        )
        activity_level = input_cols[1].selectbox(
            "Activity / protein need",
            list(PROTEIN_ACTIVITY_FACTORS_G_PER_KG.keys()),
            index=2,
            key="macro_activity_level",
        )
        diet_style = input_cols[2].selectbox(
            "Carb/fat style",
            list(DIET_STYLE_PRESETS.keys()),
            index=0,
            key="macro_body_diet_style",
        )

        calories = float(st.session_state.get("macro_target_calories", DEFAULT_TARGETS["calories"]) or 0)
        targets = targets_from_body_weight(calories, weight_lbs, activity_level, diet_style)
        factor = PROTEIN_ACTIVITY_FACTORS_G_PER_KG[activity_level]

        preview_cols = st.columns(4)
        preview_cols[0].metric("Calories", f"{targets['calories']:.0f}")
        preview_cols[1].metric("Protein", f"{targets['protein_g']:.0f}g", f"{factor:g}g/kg")
        preview_cols[2].metric("Carbs", f"{targets['carbs_g']:.0f}g")
        preview_cols[3].metric("Fat", f"{targets['fat_g']:.0f}g")

        st.button(
            "Apply body-aware targets",
            type="primary",
            on_click=apply_macro_targets,
            args=(targets, f"Body-aware {diet_style}"),
            width="stretch",
        )


def _render_profile_controls() -> None:
    st.subheader("Profile and goals")
    st.caption(
        "This prototype stores the profile in the current Streamlit session. "
        "Use download/upload to move a profile between visits until a real database is added."
    )
    if st.session_state.pop("macro_profile_loaded", False):
        st.success("Profile loaded.")

    profile_col, cal_col, protein_col, carb_col, fat_col = st.columns([1.4, 1, 1, 1, 1])
    profile_col.text_input("Profile name", key="macro_profile_name")
    cal_col.number_input("Calories", min_value=0, max_value=10000, step=50, key="macro_target_calories")
    protein_col.number_input("Protein (g)", min_value=0, max_value=500, step=5, key="macro_target_protein_g")
    carb_col.number_input("Carbs (g)", min_value=0, max_value=1000, step=5, key="macro_target_carbs_g")
    fat_col.number_input("Fat (g)", min_value=0, max_value=500, step=5, key="macro_target_fat_g")

    st.divider()
    _render_preset_buttons()
    _render_body_weight_helper()

    save_col, load_col = st.columns(2)
    save_col.download_button(
        "Download profile/day JSON",
        data=_download_payload().encode("utf-8"),
        file_name="macro_map_profile.json",
        mime="application/json",
        width="stretch",
    )
    uploaded_file = load_col.file_uploader("Load profile/day JSON", type="json")
    if uploaded_file is not None:
        load_col.button(
            "Apply uploaded profile",
            on_click=_load_profile_payload,
            args=(uploaded_file,),
            width="stretch",
        )


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
        width="stretch",
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

    if st.button("Add checked items to macro counter", type="primary", width="stretch"):
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
        width="stretch",
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
    if remove_col.button("Remove checked items", width="stretch"):
        remove_ids = set(edited_log.loc[edited_log["remove"] == True, "entry_id"].tolist())  # noqa: E712
        st.session_state.macro_counter_items = [entry for entry in entries if entry.get("entry_id") not in remove_ids]
        st.rerun()
    if clear_col.button("Clear counter", width="stretch"):
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
