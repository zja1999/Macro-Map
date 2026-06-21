from __future__ import annotations

import pandas as pd


def recommend_single_items(menu_items: pd.DataFrame, protein_goal: float, calorie_limit: float, top_n: int = 10) -> pd.DataFrame:
    """Rank individual menu items by protein goal first, then calories.

    The recommender stays intentionally simple: if any items fit under the calorie
    limit, rank only those. If none fit, show the closest available items instead
    so the UI can still provide useful feedback.
    """
    if menu_items.empty or not {"calories", "protein_g"}.issubset(menu_items.columns):
        return pd.DataFrame()

    df = menu_items.copy()
    numeric_cols = ["calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g", "sugar_g"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["calories", "protein_g"])
    if df.empty:
        return df

    calorie_limit = max(float(calorie_limit), 1.0)
    protein_goal = max(float(protein_goal), 0.0)

    under_limit = df[df["calories"] <= calorie_limit].copy()
    if not under_limit.empty:
        df = under_limit

    df["protein_gap_g"] = (protein_goal - df["protein_g"]).clip(lower=0)
    df["calories_remaining"] = calorie_limit - df["calories"]
    df["meets_goal"] = (df["protein_g"] >= protein_goal) & (df["calories"] <= calorie_limit)

    return df.sort_values(
        by=["protein_gap_g", "protein_g", "calories"],
        ascending=[True, False, True],
    ).head(top_n)
