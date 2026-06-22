from __future__ import annotations

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from src.geojson_utils import (
    SelectionError,
    bbox_from_circle,
    circle_area_sq_miles,
    circle_from_feature,
    filter_locations_to_circle,
    get_latest_drawn_feature,
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
    nutrition_request_mailto_url,
    nutrition_request_message,
    nutrition_request_url,
)

DEFAULT_CENTER = (31.0000, -99.0000)  # Texas-wide starting view.
DEFAULT_ZOOM = 6
MAX_QUERY_AREA_SQ_MI = 250.0
MAP_HEIGHT_PX = 650
METERS_PER_MILE = 1609.344
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
    """Build the selection map with circle selection only."""
    m = folium.Map(location=list(center), zoom_start=zoom, control_scale=True, zoom_control=False)
    Draw(
        export=False,
        position="topright",
        draw_options={
            "polyline": False,
            "rectangle": False,
            "polygon": False,
            "circle": {"showRadius": True, "metric": False},
            "circlemarker": False,
            "marker": False,
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(m)
    return m


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

    display_cols = ["chain", "category", "item_name", "calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "meets_goal"]
    present_cols = [col for col in display_cols if col in recommendations.columns]
    st.dataframe(
        recommendations[present_cols],
        width="stretch",
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
    present_cols = preferred_cols + extra_cols
    st.dataframe(
        filtered[present_cols],
        width="stretch",
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
        width="stretch",
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


def selected_chain_from_table_event(table_event, annotated_chains: pd.DataFrame) -> pd.Series | None:
    """Return the selected chain row from a Streamlit dataframe selection event."""
    selection = getattr(table_event, "selection", None)
    selected_rows: list[int] = []

    if selection is not None:
        if hasattr(selection, "rows"):
            selected_rows = list(selection.rows)
        elif isinstance(selection, dict):
            selected_rows = list(selection.get("rows", []))

    if not selected_rows:
        return None

    selected_index = int(selected_rows[0])
    if selected_index < 0 or selected_index >= len(annotated_chains):
        return None

    return annotated_chains.iloc[selected_index]


def style_chain_table(display_table: pd.DataFrame):
    """Color chain table text by nutrition status."""

    def row_style(row: pd.Series) -> list[str]:
        color = "color: #2e9f57; font-weight: 700" if row.get("Nutrition") == "On file" else "color: #ff4b4b; font-weight: 700"
        return [color for _ in row.index]

    return display_table.style.apply(row_style, axis=1)


def render_selectable_chain_table(annotated_chains: pd.DataFrame) -> pd.Series | None:
    """Render chains as a normal selectable table and return the active row."""
    display_table = annotated_chains[["chain", "locations", "nutrition_status"]].copy()
    display_table = display_table.rename(
        columns={
            "chain": "Chain",
            "locations": "# Locations",
            "nutrition_status": "Nutrition",
        }
    )

    table_event = st.dataframe(
        style_chain_table(display_table),
        width="stretch",
        hide_index=True,
        height=dataframe_height(len(display_table), min_height=220, max_height=420),
        on_select="rerun",
        selection_mode="single-row",
        key="unique_chains_selectable_table",
        column_config={
            "Chain": "Chain",
            "# Locations": "# Locations",
            "Nutrition": "Nutrition",
        },
    )

    return selected_chain_from_table_event(table_event, annotated_chains)


def render_chain_request_section(selected_chain: pd.Series | None, missing_chains: list[str]) -> None:
    """Render the request section based on the currently selected table row."""
    with st.expander("Request missing nutrition data", expanded=bool(missing_chains)):
        if not missing_chains:
            st.success("All chains in this selection have nutrition CSVs on file.")
            return

        if selected_chain is None:
            st.info("Select a missing chain row in the table above to populate this request section.")
            st.caption("Rows with nutrition already on file do not need a request.")
            return

        chain = str(selected_chain["chain"])
        nutrition_on_file = bool(selected_chain.get("nutrition_on_file", False))

        st.caption(f"Selected chain: **{chain}**")
        if nutrition_on_file:
            st.success("Nutrition data is already on file for this chain.")
            return

        request_message = nutrition_request_message(chain)
        github_col, email_col = st.columns(2)
        github_col.link_button("Open GitHub request", nutrition_request_url(chain), width="stretch")

        email_url = nutrition_request_mailto_url(chain)
        if email_url:
            email_col.link_button("Open email draft", email_url, width="stretch")
            st.caption("Email drafts still require the user to press Send. Streamlit cannot send mail by itself with `mailto:` links.")
        else:
            email_col.caption("Email drafts can be enabled with `contact_email` in Streamlit secrets.")

        st.text_area(
            "Copyable request message",
            request_message,
            height=130,
            help="Use this if the user does not have GitHub or email drafts are not configured.",
        )


def render_unique_chains_panel(library) -> None:
    """Render unique chains and a request section driven by table selection."""
    st.subheader("Unique chains")
    chains = st.session_state.chains
    locations = st.session_state.locations

    if chains.empty:
        st.write("No chain results yet.")
        return

    annotated_chains = annotate_chains_with_nutrition(chains, library)
    covered_count = int(annotated_chains["nutrition_on_file"].sum())

    metric_a, metric_b = st.columns(2)
    metric_a.metric("Chains found", len(annotated_chains))
    metric_b.metric("Locations found", len(locations))
    st.caption(f"Nutrition coverage: **{covered_count}/{len(annotated_chains)} chains** have CSVs on file.")

    selected_chain = render_selectable_chain_table(annotated_chains)
    missing_chains = annotated_chains.loc[~annotated_chains["nutrition_on_file"], "chain"].tolist()
    render_chain_request_section(selected_chain, missing_chains)

    st.download_button(
        "Download chain list CSV",
        data=annotated_chains.drop(columns=["chain_key"], errors="ignore").to_csv(index=False).encode("utf-8"),
        file_name="fast_food_chains.csv",
        mime="text/csv",
        width="stretch",
    )


def render_selection_panel(map_data) -> None:
    """Render selection controls and update session state when a search runs."""
    st.subheader("Selection")
    button_left, button_right = st.columns(2)
    with button_left:
        find_clicked = st.button("Find fast-food chains", type="primary", width="stretch")
    with button_right:
        clear_clicked = st.button("Clear selection", width="stretch")

    if clear_clicked:
        st.session_state.locations = pd.DataFrame()
        st.session_state.chains = pd.DataFrame()
        st.session_state.map_reset_token += 1
        st.rerun()

    st.caption(f"Draw one circle under about {MAX_QUERY_AREA_SQ_MI:.0f} sq mi, then search.")
    latest_feature = get_latest_drawn_feature(map_data)

    if latest_feature is None:
        if find_clicked:
            st.warning("Draw a circle on the map before searching.")
        else:
            st.info("Click the circle tool, then drag on the map to set your search radius.")
        return

    try:
        center_lat, center_lon, radius_m = circle_from_feature(latest_feature)
        area_sq_miles = circle_area_sq_miles(radius_m)
        radius_miles = radius_m / METERS_PER_MILE
        south, west, north, east = bbox_from_circle(center_lat, center_lon, radius_m)
        st.write(f"Selected circle: **{area_sq_miles:,.1f} sq mi** / **{radius_miles:,.1f} mi radius**")

        if area_sq_miles > MAX_QUERY_AREA_SQ_MI:
            st.warning("That circle is too large for the public Overpass API prototype. Draw a smaller circle, then search again.")
        elif find_clicked:
            try:
                with st.spinner("Searching OpenStreetMap..."):
                    bbox_locations = fetch_fast_food_locations_in_bbox(south, west, north, east)
                    locations = filter_locations_to_circle(bbox_locations, center_lat, center_lon, radius_m)
                st.session_state.locations = locations
                st.session_state.chains = unique_chains(locations)
                st.rerun()
            except RuntimeError as exc:
                st.error("OpenStreetMap's public Overpass API rejected or timed out on this request.")
                st.caption("Try a smaller circle first. This version catches the error instead of crashing. The technical details are below.")
                with st.expander("Technical details"):
                    st.code(str(exc))
        else:
            st.info("Circle ready. Click **Find fast-food chains** to search this area.")
    except SelectionError as exc:
        st.error(str(exc))


def render_map_chain_finder() -> None:
    st.caption("Draw a circle on the map, then list unique fast-food chains inside it.")
    library = load_nutrition_library()

    if "locations" not in st.session_state:
        st.session_state.locations = pd.DataFrame()
    if "chains" not in st.session_state:
        st.session_state.chains = pd.DataFrame()
    if "map_reset_token" not in st.session_state:
        st.session_state.map_reset_token = 0

    map_col, selection_col, chains_col = st.columns([1.8, 0.8, 1.05], gap="large")

    with map_col:
        map_data = st_folium(
            build_map(),
            height=MAP_HEIGHT_PX,
            use_container_width=True,
            returned_objects=["all_drawings", "last_active_drawing"],
            key=f"macro_map_{st.session_state.map_reset_token}",
        )

    with selection_col:
        render_selection_panel(map_data)

    with chains_col:
        render_unique_chains_panel(library)

    if not st.session_state.chains.empty:
        st.divider()
        st.subheader("Selected-chain menu")
        render_selected_chain_tabs(library)


def main() -> None:
    configure_page()
    inject_responsive_styles()
    st.title("Macro Map")
    render_map_chain_finder()
