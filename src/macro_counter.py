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
SEX_OPTIONS = ["Not specified (average)", "Female", "Male"]
ACTIVITY_MULTIPLIERS = {
    "Sedentary: little exercise": 1.2,
    "Light: 1-3 workouts/week": 1.375,
    "Moderate: 3-5 workouts/week": 1.55,
    "Very active: 6-7 workouts/week": 1.725,
    "Athlete / physical job": 1.9,
}
GOAL_PACE_OPTIONS = {
    "Lose weight": {
        "Slow loss (-250 kcal/day)": -250,
        "Moderate loss (-500 kcal/day)": -500,
        "Aggressive loss (-750 kcal/day)": -750,
    },
    "Maintain weight": {
        "Maintain (0 kcal/day)": 0,
    },
    "Gain weight": {
        "Lean gain (+150 kcal/day)": 150,
        "Moderate gain (+300 kcal/day)": 300,
        "Fast gain (+500 kcal/day)": 500,
    },
}
FITNESS_GOAL_PRESETS = {
    "General fitness / balanced": {
        "protein_g_per_kg": 1.6,
        "carb_share": 0.55,
        "fat_share": 0.45,
        "description": "Moderate protein with a balanced carb/fat split.",
    },
    "Fat loss / muscle retention": {
        "protein_g_per_kg": 2.0,
        "carb_share": 0.45,
        "fat_share": 0.55,
        "description": "Higher protein to support lean-mass retention while dieting.",
    },
    "Muscle gain / strength": {
        "protein_g_per_kg": 1.8,
        "carb_share": 0.60,
        "fat_share": 0.40,
        "description": "High protein with more carbs for lifting performance.",
    },
    "Endurance performance": {
        "protein_g_per_kg": 1.6,
        "carb_share": 0.70,
        "fat_share": 0.30,
        "description": "More carbs for longer training sessions.",
    },
    "Lower carb": {
        "protein_g_per_kg": 1.8,
        "carb_share": 0.30,
        "fat_share": 0.70,
        "description": "Higher protein and fat with fewer carbs, but not strict keto.",
    },
    "Keto-style": {
        "protein_g_per_kg": 1.6,
        "carb_pct_override": 0.05,
        "description": "Very low carb and high fat. Restrictive; best treated as a specialty preset.",
    },
}


def coerce_macro_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with macro columns coerced to numeric values when present."""
    result = df.copy()
    for col in MACRO_COLS:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col].astype(str).str.replace("<", "", regex=False), errors="coerce").fillna(0)
    return result


def mifflin_st_jeor_bmr(weight_lbs: float, height_inches: float, age_years: int, sex: str) -> float:
    """Estimate basal metabolic rate with the Mifflin-St Jeor equation."""
    weight_kg = max(float(weight_lbs), 0.0) / 2.20462
    height_cm = max(float(height_inches), 0.0) * 2.54
    base = 10 * weight_kg + 6.25 * height_cm - 5 * max(int(age_years), 0)

    if sex == "Male":
        return base + 5
    if sex == "Female":
        return base - 161

    # Neutral default when the user has not selected a sex for the equation.
    return ((base + 5) + (base - 161)) / 2


def current_height_inches() -> float:
    """Return height in inches from the current Streamlit inputs."""
    feet = float(st.session_state.get("macro_height_feet", 5) or 0)
    inches = float(st.session_state.get("macro_height_inches", 10) or 0)
    return feet * 12 + inches


def calorie_delta_for_goal(weight_goal: str, pace: str) -> int:
    """Return daily calorie adjustment for selected weight goal and pace."""
    return int(GOAL_PACE_OPTIONS.get(weight_goal, {}).get(pace, 0))


def calculated_macro_targets(calories: float, weight_lbs: float, fitness_goal: str) -> dict[str, float]:
    """Calculate macro grams from calories, body weight, and fitness goal."""
    calories = max(float(calories), 0.0)
    preset = FITNESS_GOAL_PRESETS[fitness_goal]
    weight_kg = max(float(weight_lbs), 0.0) / 2.20462
    protein_g = round(weight_kg * float(preset["protein_g_per_kg"]))
    protein_calories = protein_g * KCAL_PER_GRAM["protein_g"]

    # Keep recommendations feasible for low-calorie targets.
    if calories > 0 and protein_calories > calories * 0.45:
        protein_g = round(calories * 0.45 / KCAL_PER_GRAM["protein_g"])
        protein_calories = protein_g * KCAL_PER_GRAM["protein_g"]

    if "carb_pct_override" in preset:
        carbs_g = round(calories * float(preset["carb_pct_override"]) / KCAL_PER_GRAM["carbs_g"])
        carb_calories = carbs_g * KCAL_PER_GRAM["carbs_g"]
        fat_g = round(max(calories - protein_calories - carb_calories, 0) / KCAL_PER_GRAM["fat_g"])
    else:
        remaining_calories = max(calories - protein_calories, 0)
        carb_share = float(preset["carb_share"])
        fat_share = float(preset["fat_share"])
        share_total = max(carb_share + fat_share, 0.001)
        carbs_g = round(remaining_calories * (carb_share / share_total) / KCAL_PER_GRAM["carbs_g"])
        fat_g = round(remaining_calories * (fat_share / share_total) / KCAL_PER_GRAM["fat_g"])

    return {
        "calories": round(calories),
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }


def build_macro_recommendation() -> dict[str, float | str]:
    """Build the current BMR/TDEE/calorie/macro recommendation from UI inputs."""
    weight_lbs = float(st.session_state.get("macro_body_weight_lbs", 180.0) or 0)
    age_years = int(st.session_state.get("macro_age_years", 30) or 0)
    height_inches = current_height_inches()
    sex = str(st.session_state.get("macro_sex", SEX_OPTIONS[0]))
    activity_level = str(st.session_state.get("macro_activity_level", next(iter(ACTIVITY_MULTIPLIERS))))
    weight_goal = str(st.session_state.get("macro_weight_goal", "Maintain weight"))
    pace = str(st.session_state.get("macro_weight_pace", "Maintain (0 kcal/day)"))
    fitness_goal = str(st.session_state.get("macro_fitness_goal", "General fitness / balanced"))

    bmr = mifflin_st_jeor_bmr(weight_lbs, height_inches, age_years, sex)
    activity_multiplier = ACTIVITY_MULTIPLIERS[activity_level]
    maintenance_calories = bmr * activity_multiplier
    calorie_delta = calorie_delta_for_goal(weight_goal, pace)
    target_calories = max(maintenance_calories + calorie_delta, 0)
    targets = calculated_macro_targets(target_calories, weight_lbs, fitness_goal)

    return {
        "bmr": round(bmr),
        "maintenance_calories": round(maintenance_calories),
        "calorie_delta": calorie_delta,
        "activity_multiplier": activity_multiplier,
        "fitness_goal": fitness_goal,
        "fitness_note": FITNESS_GOAL_PRESETS[fitness_goal]["description"],
        **targets,
    }


def apply_macro_targets(targets: dict[str, float], preset_name: str | None = None) -> None:
    """Apply calculated macro targets into Streamlit session state."""
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
    if "macro_age_years" not in st.session_state:
        st.session_state.macro_age_years = 30
    if "macro_height_feet" not in st.session_state:
        st.session_state.macro_height_feet = 5
    if "macro_height_inches" not in st.session_state:
        st.session_state.macro_height_inches = 10
    if "macro_sex" not in st.session_state:
        st.session_state.macro_sex = SEX_OPTIONS[0]
    if "macro_activity_level" not in st.session_state:
        st.session_state.macro_activity_level = "Moderate: 3-5 workouts/week"
    if "macro_weight_goal" not in st.session_state:
        st.session_state.macro_weight_goal = "Maintain weight"
    if "macro_weight_pace" not in st.session_state:
        st.session_state.macro_weight_pace = "Maintain (0 kcal/day)"
    if "macro_fitness_goal" not in st.session_state:
        st.session_state.macro_fitness_goal = "General fitness / balanced"
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
        "calculator_inputs": {
            "age_years": st.session_state.get("macro_age_years"),
            "sex": st.session_state.get("macro_sex"),
            "height_feet": st.session_state.get("macro_height_feet"),
            "height_inches": st.session_state.get("macro_height_inches"),
            "weight_lbs": st.session_state.get("macro_body_weight_lbs"),
            "activity_level": st.session_state.get("macro_activity_level"),
            "weight_goal": st.session_state.get("macro_weight_goal"),
            "weight_pace": st.session_state.get("macro_weight_pace"),
            "fitness_goal": st.session_state.get("macro_fitness_goal"),
        },
        "items": st.session_state.get("macro_counter_items", []),
    }
    return json.dumps(payload, indent=2)


def _load_profile_payload(uploaded_file) -> None:
    payload = json.loads(uploaded_file.read().decode("utf-8"))
    st.session_state.macro_profile_name = str(payload.get("profile_name", "Guest"))

    targets = payload.get("targets", {})
    for key, default in DEFAULT_TARGETS.items():
        st.session_state[f"macro_target_{key}"] = float(targets.get(key, default) or 0)

    calculator_inputs = payload.get("calculator_inputs", {})
    input_key_map = {
        "age_years": "macro_age_years",
        "sex": "macro_sex",
        "height_feet": "macro_height_feet",
        "height_inches": "macro_height_inches",
        "weight_lbs": "macro_body_weight_lbs",
        "activity_level": "macro_activity_level",
        "weight_goal": "macro_weight_goal",
        "weight_pace": "macro_weight_pace",
        "fitness_goal": "macro_fitness_goal",
    }
    for payload_key, session_key in input_key_map.items():
        if payload_key in calculator_inputs:
            st.session_state[session_key] = calculator_inputs[payload_key]

    st.session_state.macro_counter_items = list(payload.get("items", []))
    st.session_state.macro_profile_loaded = True


def _render_calculator_inputs() -> dict[str, float | str]:
    st.subheader("Macro calculator")
    st.caption(
        "Enter basic health and goal details first. The app estimates maintenance calories, then suggests a target for losing, maintaining, or gaining weight."
    )

    top_cols = st.columns([1.2, 1, 0.8, 0.8, 0.8, 1])
    top_cols[0].text_input("Profile name", key="macro_profile_name")
    top_cols[1].selectbox("Sex for BMR equation", SEX_OPTIONS, key="macro_sex")
    top_cols[2].number_input("Age", min_value=13, max_value=100, step=1, key="macro_age_years")
    top_cols[3].number_input("Height ft", min_value=3, max_value=8, step=1, key="macro_height_feet")
    top_cols[4].number_input("Height in", min_value=0, max_value=11, step=1, key="macro_height_inches")
    top_cols[5].number_input("Weight (lb)", min_value=50.0, max_value=600.0, step=1.0, key="macro_body_weight_lbs")

    goal_cols = st.columns([1.25, 1, 1.2, 1.4])
    goal_cols[0].selectbox("Activity level", list(ACTIVITY_MULTIPLIERS.keys()), key="macro_activity_level")
    goal_cols[1].selectbox("Weight goal", list(GOAL_PACE_OPTIONS.keys()), key="macro_weight_goal")

    pace_options = list(GOAL_PACE_OPTIONS[st.session_state.macro_weight_goal].keys())
    if st.session_state.get("macro_weight_pace") not in pace_options:
        st.session_state.macro_weight_pace = pace_options[0]
    goal_cols[2].selectbox("Goal pace", pace_options, key="macro_weight_pace")
    goal_cols[3].selectbox("Fitness goal", list(FITNESS_GOAL_PRESETS.keys()), key="macro_fitness_goal")

    return build_macro_recommendation()


def _render_recommendation(recommendation: dict[str, float | str]) -> None:
    st.subheader("Recommended targets")
    metric_cols = st.columns(6)
    metric_cols[0].metric("BMR", f"{float(recommendation['bmr']):.0f}")
    metric_cols[1].metric("Maintenance", f"{float(recommendation['maintenance_calories']):.0f}")
    metric_cols[2].metric("Suggested calories", f"{float(recommendation['calories']):.0f}", f"{float(recommendation['calorie_delta']):+.0f}/day")
    metric_cols[3].metric("Protein", f"{float(recommendation['protein_g']):.0f}g")
    metric_cols[4].metric("Carbs", f"{float(recommendation['carbs_g']):.0f}g")
    metric_cols[5].metric("Fat", f"{float(recommendation['fat_g']):.0f}g")

    st.caption(f"Fitness split: **{recommendation['fitness_goal']}** — {recommendation['fitness_note']}")

    if float(recommendation["calories"]) < 1200:
        st.warning("This suggested calorie target is very low. Consider a slower pace or professional guidance.")
    elif float(recommendation["calories"]) < float(recommendation["bmr"]):
        st.warning("This target is below estimated BMR. It may be too aggressive for many users.")

    target_payload = {key: float(recommendation[key]) for key in CORE_TARGET_COLS}
    st.button(
        "Use recommended targets",
        type="primary",
        on_click=apply_macro_targets,
        args=(target_payload, "Calculated recommendation"),
        width="stretch",
    )


def _render_active_targets() -> None:
    st.subheader("Active daily targets")
    st.caption("These editable numbers drive the remaining-calories/macros totals below.")
    target_cols = st.columns(4)
    target_cols[0].number_input("Calories", min_value=0, max_value=10000, step=50, key="macro_target_calories")
    target_cols[1].number_input("Protein (g)", min_value=0, max_value=500, step=5, key="macro_target_protein_g")
    target_cols[2].number_input("Carbs (g)", min_value=0, max_value=1000, step=5, key="macro_target_carbs_g")
    target_cols[3].number_input("Fat (g)", min_value=0, max_value=500, step=5, key="macro_target_fat_g")


def _render_profile_tools() -> None:
    with st.expander("Save or load this profile/day", expanded=False):
        st.caption(
            "This prototype stores data in the current Streamlit session. Download/upload JSON to move a profile between visits until a database is added."
        )
        if st.session_state.pop("macro_profile_loaded", False):
            st.success("Profile loaded.")
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


def _render_profile_controls() -> None:
    recommendation = _render_calculator_inputs()
    st.divider()
    _render_recommendation(recommendation)
    st.divider()
    _render_active_targets()
    _render_profile_tools()


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
    st.caption("Estimate calorie/macros targets, manually adjust if needed, then add selected-chain menu items to your daily totals.")
    _render_profile_controls()
    st.divider()
    _render_add_items(menu_items)
    st.divider()
    _render_totals()
