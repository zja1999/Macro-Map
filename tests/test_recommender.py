import pandas as pd

from src.recommender import recommend_single_items


def test_recommend_single_items_prefers_items_that_meet_protein_goal_under_calories():
    menu_items = pd.DataFrame(
        [
            {"chain": "A", "item_name": "Low Protein", "calories": 400, "protein_g": 12},
            {"chain": "A", "item_name": "Best Fit", "calories": 550, "protein_g": 35},
            {"chain": "A", "item_name": "Too Many Calories", "calories": 900, "protein_g": 60},
        ]
    )

    result = recommend_single_items(menu_items, protein_goal=30, calorie_limit=700)

    assert result.iloc[0]["item_name"] == "Best Fit"
    assert bool(result.iloc[0]["meets_goal"]) is True
    assert "Too Many Calories" not in result["item_name"].tolist()


def test_recommend_single_items_falls_back_when_no_items_are_under_calorie_limit():
    menu_items = pd.DataFrame(
        [
            {"chain": "A", "item_name": "Closest", "calories": 800, "protein_g": 35},
            {"chain": "A", "item_name": "Higher Protein", "calories": 950, "protein_g": 45},
        ]
    )

    result = recommend_single_items(menu_items, protein_goal=40, calorie_limit=700)

    assert not result.empty
    assert (result["calories"] > 700).all()
    assert result.iloc[0]["item_name"] == "Higher Protein"
