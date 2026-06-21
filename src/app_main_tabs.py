from __future__ import annotations

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from src.geojson_utils import (
    SelectionError,
    bbox_from_latlon,
    filter_locations_to_polygon,
    get_latest_drawn_feature,
    polygon_latlon_from_feature,
    rough_bbox_area_sq_miles,
)
from src.macro_counter import render_macro_counter
from src.nutrition_store import (
    annotate_chains_with_nutrition,
    filter_files_for_chains,
    filter_items_for_chains,
    load_nutrition_library,
)
from src.osm_overpass import build_fast_food_bbox_query, parse_fast_food_elements, query_overpass, unique_chains
from src.recommender import recommend_single_items
from src.ui_helpers import (
    dataframe_height,
    inject_responsive_styles,
    nutrition_request_url,
    render_missing_chain_request_panel,
)

DEFAULT_CENTER = (31.0000, -99.0000)  # Texas-wide starting view.
DEFAULT_ZOOM = 6
MAX_QUERY_AREA_SQ_MI = 250.0
MAP_HEIGHT_PX = 620
MENU_DISPLAY_COLUMNS = [
    "chain",
    "category",
    "item_name",
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "serving_size",
    "sodium_mg",
    "fiber_g",
    "sugar_g",
]


def configure_page() -> None:
    st.set_page_config(page_title="Macro Map", layout="wide")


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_fast_food_locations_in_bbox(south: float, west: float, north: float, east: float) -> pd.DataFrame:
    """Fetch named OSM fast-food locations in a bounding box."""
    query = build_fast_food_bbox_query(south, west, north, east)
    payload = query_overpass(query)
    return parse_fast_food_elements(payload)


def build_map(center: tuple[float, float] = DEFAULT_CENTER, zoom: int = DEFAULT_ZOOM) -> folium.Map:
    """Build the selection map without store-level markers."""
    m = folium.Map(location=list(center), zoom_start=zoom, control_scale=True, zoom_control=False)

    Draw(
        export=False,
        position="topright",
        draw_options={
            "polyline": False,
            "circle": False,
            "circlemarker": False,
            "marker": False,
            "polygon": {
                "allowIntersection": False,
                "showArea": True,
                "metric": False,
            },
            "rectangle": True,
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(m)

    return m


def style_chain_table(df: pd.DataFrame):
    """Color chain rows by nutrition coverage without background shading."""

    def row_style(row: pd.Series) -> list[str]:
        color = "color: #198754; font-weight: 600" if bool(row.get("nutrition_on_file", False)) else "color: #b00020; font-weight: 600"
        return [color if col in {"chain", "locations", "nutrition_status"} else "" for col in row.index]

    return df.style.apply(row_style, axis=1)


def render_single_item_recommendations(menu_items: pd.DataFrame) -> None:
    """Render a minimal macro-target recommender for individual menu items only."""
    st.caption("Find individual menu items from the selected area that best match your protein goal and calorie limit.")

    input_a, input_b = st.columns(2)
    protein_goal = input_a.number_input("Protein goal (g)", min_value=0, max_value=200, value=30, step=5)
    calorie_limit = input_b.number_input("Calorie limit", min_value=1, max_value=3000, value=700, step=50)

    recommendations = recommend_single_items(menu_items, protein_goal, calorie_limit)

    if recommendations.empty:
        st.info("No recommendable menu items found for the selected chains yet.")
        return

    if not bool((recommendations["calories"] <= calorie_limit).all()):
        st.warning("No items were under the calorie limit, so the closest available items are shown instead.")

    display_cols = [
        "chain",
        "category",
        "item_name",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sodium_mg",
        "meets_goal",
    ]
    present_cols = [col for col in display_cols if col in recommendations.columns]

    st.dataframe(
        recommendations[present_cols],
        use_container_width=True,
        hide_index=True,
        height=dataframe_height(len(recommendations), min_height=220, max_height=420),
        column_config={
            "chain": "Chain",
            "category": "Category",
            "item_name": "Item",
            "calories": "Calories",
            "protein_g": "Protein (g)",
            "carbs_g": "Carbs (g)",
            "fat_g": "Fat (g)",
            "sodium_mg": "Sodium (mg)",
            "meets_goal": "Meets goal",
        },
    )


def render_menu_items_table(menu_items: pd.DataFrame) -> None:
    """Render only the menu macro rows for selected chains."""
    st.caption("Menu macro rows for the chains found in your selected area.")

    filter_col, search_col = st.columns([1, 1.4])
    chains = sorted(menu_items["chain"].dropna().astype(str).unique().tolist()) if "chain" in menu_items.columns else []
    selected_chains = filter_col.multiselect("Filter chains", chains, default=chains, key="menu_items_chain_filter")
    search_text = search_col.text_input("Search menu items", placeholder="chicken, soup, salad...", key="menu_items_search").strip().lower()

    filtered = menu_items.copy()
    if selected_chains and "chain" in filtered.columns:
        filtered = filtered[filtered["chain"].isin(selected_chains)]
    if search_text:
        search_cols = [col for col in ["chain", "category", "item_name"] if col in filtered.columns]
        search_blob = filtered[search_cols].astype(str).agg(" ".join, axis=1).str.lower()
        filtered = filtered[search_blob.str.contains(search_text, na=False)]

    preferred_cols = [col for col in MENU_DISPLAY_COLUMNS if col in filtered.columns]
    extra_cols = [col for col in filtered.columns if col not in preferred_cols and col not in {"chain_key", "source_file"}]

    st.dataframe(
        filtered[present_cols := preferred_cols + extra_cols],
        use_container_width=True,
        hide_index=True,
        height=dataframe_height(len(filtered), min_height=260, max_height=620),
        column_config={
            "chain": "Chain",
            "category": "Category",
            "item_name": "Item",
            "calories": "Calories",
            "protein_g": "Protein (g)",
            "carbs_g": "Carbs (g)",
            "fat_g": "Fat (g)",
            "serving_size": "Serving",
            "sodium_mg": "Sodium (mg)",
            "fiber_g": "Fiber (g)",
            "sugar_g": "Sugar (g)",
        },
    )

    st.download_button(
        "Download visible menu macros CSV",
        data=filtered[present_cols].to_csv(index=False).encode("utf-8"),
        file_name="menu_macros_selected_area.csv",
        mime="text/csv",
    )


def render_selected_chain_tabs(library) -> None:
    """Render selected-chain menu items, suggestions, and macro counter as tabs."""
    selected_files = filter_files_for_chains(library.files, st.session_state.chains)
    menu_items = filter_items_for_chains(library.items, st.session_state.chains)

    selected_chain_count = len(st.session_state.chains)
    covered_selected_count = len(selected_files[selected_files.get("loaded", False) == True]) if not selected_files.empty else 0
    st.caption(
        f"Showing nutrition rows only for chains found in the selected area. "
        f"Nutrition data is available for {covered_selected_count}/{selected_chain_count} selected chains."
    )

    if menu_items.empty:
        st.info("No menu macro rows found for the selected chains yet. Add matching CSVs to `data/nutrition/`.")
        return

    if "is_sample_data" in menu_items.columns:
        sample_mask = menu_items["is_sample_data"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
        if bool(sample_mask.any()):
            st.warning("Some displayed macro rows are sample test data. Replace those CSVs with real nutrition files before using the numbers.")

    menu_tab, suggestions_tab, counter_tab = st.tabs(["Menu items", "Item suggestions", "Macro counter"])
    with menu_tab:
        render_menu_items_table(menu_items)
    with suggestions_tab:
        render_single_item_recommendations(menu_items)
    with counter_tab:
        render_macro_counter(menu_items)


def render_map_chain_finder() -> None:
    st.caption("Draw an area on the Texas map, then list unique fast-food chains inside it.")

    library = load_nutrition_library()

    left, right = st.columns([1.8, 1], gap="large")

    if "locations" not in st.session_state:
        st.session_state.locations = pd.DataFrame()
    if "chains" not in st.session_state:
        st.session_state.chains = pd.DataFrame()
    if "map_reset_token" not in st.session_state:
        st.session_state.map_reset_token = 0

    with left:
        m = build_map()
        map_data = st_folium(
            m,
            height=MAP_HEIGHT_PX,
            use_container_width=True,
            returned_objects=["all_drawings", "last_active_drawing"],
            key=f"macro_map_{st.session_state.map_reset_token}",
        )

    with right:
        st.subheader("Selection")
        button_left, button_right = st.columns(2)
        with button_left:
            find_clicked = st.button("Find fast-food chains", type="primary")
        with button_right:
            clear_clicked = st.button("Clear selection")

        if clear_clicked:
            st.session_state.locations = pd.DataFrame()
            st.session_state.chains = pd.DataFrame()
            st.session_state.map_reset_token += 1
            st.rerun()

        st.caption(f"Draw a rectangle or polygon under about {MAX_QUERY_AREA_SQ_MI:.0f} sq mi, then search.")
        latest_feature = get_latest_drawn_feature(map_data)

        if latest_feature is None:
            if find_clicked:
                st.warning("Draw a rectangle or polygon on the map before searching.")
            else:
                st.info("Draw a rectangle or polygon on the map to select an area.")
        else:
            try:
                latlon = polygon_latlon_from_feature(latest_feature)
                area_sq_miles = rough_bbox_area_sq_miles(latlon)
                south, west, north, east = bbox_from_latlon(latlon)

                st.write(f"Approx. selected bounding-box area: **{area_sq_miles:,.1f} sq mi**")

                if area_sq_miles > MAX_QUERY_AREA_SQ_MI:
                    st.warning(
                        "That area is too large for the public Overpass API prototype. "
                        "Zoom in and draw a smaller box, then search again."
                    )
                elif find_clicked:
                    try:
                        with st.spinner("Searching OpenStreetMap..."):
                            bbox_locations = fetch_fast_food_locations_in_bbox(south, west, north, east)
                            locations = filter_locations_to_polygon(bbox_locations, latlon)

                        chains = unique_chains(locations)
                        st.session_state.locations = locations
                        st.session_state.chains = chains
                        st.rerun()
                    except RuntimeError as exc:
                        st.error("OpenStreetMap's public Overpass API rejected or timed out on this request.")
                        st.caption(
                            "Try a smaller rectangle first. This version catches the error instead of crashing. "
                            "The technical details are below."
                        )
                        with st.expander("Technical details"):
                            st.code(str(exc))
                else:
                    st.info("Selection ready. Click **Find fast-food chains** to search this area.")
            except SelectionError as exc:
                st.error(str(exc))

        st.divider()
        st.subheader("Unique chains")

        chains = st.session_state.chains
        locations = st.session_state.locations

        if chains.empty:
            st.write("No chain results yet.")
        else:
            annotated_chains = annotate_chains_with_nutrition(chains, library)
            covered_count = int(annotated_chains["nutrition_on_file"].sum())

            metric_a, metric_b = st.columns(2)
            metric_a.metric("Chains found", len(annotated_chains))
            metric_b.metric("Locations found", len(locations))
            st.caption(f"Nutrition coverage: **{covered_count}/{len(annotated_chains)} chains** have CSVs on file.")

            display_chains = annotated_chains[["chain", "locations", "nutrition_status", "nutrition_on_file"]].copy()
            display_chains["request_addition"] = display_chains.apply(
                lambda row: nutrition_request_url(row["chain"]) if not bool(row["nutrition_on_file"]) else "",
                axis=1,
            )
            st.dataframe(
                style_chain_table(display_chains),
                use_container_width=True,
                hide_index=True,
                height=dataframe_height(len(display_chains), min_height=160, max_height=330),
                column_config={
                    "chain": "Chain",
                    "locations": "# Locations",
                    "nutrition_status": "Nutrition",
                    "nutrition_on_file": None,
                    "request_addition": st.column_config.LinkColumn("Request addition", display_text="Request"),
                },
            )

            missing_chains = annotated_chains.loc[~annotated_chains["nutrition_on_file"], "chain"].tolist()
            render_missing_chain_request_panel(missing_chains)

            st.download_button(
                "Download chain list CSV",
                data=annotated_chains.drop(columns=["chain_key"], errors="ignore").to_csv(index=False).encode("utf-8"),
                file_name="fast_food_chains.csv",
                mime="text/csv",
            )

    if not st.session_state.chains.empty:
        st.divider()
        st.subheader("Selected-chain menu")
        render_selected_chain_tabs(library)


def main() -> None:
    configure_page()
    inject_responsive_styles()
    st.title("Macro Map")
    render_map_chain_finder()
