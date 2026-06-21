import pandas as pd

from src.nutrition_store import (
    filter_files_for_chains,
    filter_items_for_chains,
    load_nutrition_library,
)


def test_load_nutrition_library_ignores_templates_and_matches_filename_key(tmp_path):
    nutrition_dir = tmp_path / "nutrition"
    nutrition_dir.mkdir()

    pd.DataFrame(
        [
            {
                "category": "Entree",
                "item_name": "Grilled Nuggets",
                "calories": 200,
                "protein_g": 38,
            }
        ]
    ).to_csv(nutrition_dir / "chick_fil_a.csv", index=False)

    pd.DataFrame([{"chain": "Template Chain"}]).to_csv(nutrition_dir / "_template.csv", index=False)

    library = load_nutrition_library(nutrition_dir)

    assert "chick fil a" in library.covered_chain_keys
    assert len(library.items) == 1
    assert library.items.iloc[0]["chain"] == "Chick-fil-A"
    assert library.files.iloc[0]["loaded"] is True or bool(library.files.iloc[0]["loaded"]) is True


def test_filter_items_and_files_for_selected_chains(tmp_path):
    nutrition_dir = tmp_path / "nutrition"
    nutrition_dir.mkdir()

    pd.DataFrame(
        [
            {"chain": "Taco Bell", "item_name": "Chicken Bowl", "calories": 500, "protein_g": 28},
        ]
    ).to_csv(nutrition_dir / "taco_bell.csv", index=False)

    pd.DataFrame(
        [
            {"chain": "Wendy's", "item_name": "Chili", "calories": 240, "protein_g": 16},
        ]
    ).to_csv(nutrition_dir / "wendys.csv", index=False)

    library = load_nutrition_library(nutrition_dir)
    chains = pd.DataFrame([{"chain": "Taco Bell", "locations": 2}])

    filtered_items = filter_items_for_chains(library.items, chains)
    filtered_files = filter_files_for_chains(library.files, chains)

    assert filtered_items["chain"].tolist() == ["Taco Bell"]
    assert filtered_files["chain"].tolist() == ["Taco Bell"]
