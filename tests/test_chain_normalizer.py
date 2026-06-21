from src.chain_normalizer import chain_key, choose_chain_name, normalize_chain_name


def test_known_chain_aliases_normalize_to_display_names():
    assert normalize_chain_name("chickfila") == "Chick-fil-A"
    assert normalize_chain_name("McDonald s") == "McDonald's"
    assert normalize_chain_name("Raising Canes Chicken Fingers") == "Raising Cane's"


def test_chain_key_matches_filename_style_names():
    assert chain_key("Chick-fil-A") == chain_key("chick_fil_a")
    assert chain_key("McDonald's") == chain_key("mcdonalds")


def test_choose_chain_name_prefers_brand_over_location_name():
    tags = {
        "brand": "Taco Bell",
        "name": "Taco Bell - Main Street",
        "operator": "Franchise Group",
    }

    assert choose_chain_name(tags) == "Taco Bell"
