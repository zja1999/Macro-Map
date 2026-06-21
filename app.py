from __future__ import annotations

from urllib.parse import urlencode

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw, MarkerCluster
from streamlit_folium import st_folium

from src.geojson_utils import (
    SelectionError,
    bbox_from_latlon,
    filter_locations_to_polygon,
    get_latest_drawn_feature,
    polygon_latlon_from_feature,
    rough_bbox_area_sq_miles,
)
from src.nutrition_store import (
    annotate_chains_with_nutrition,
    filter_files_for_chains,
    filter_items_for_chains,
    load_nutrition_library,
)
from src.osm_overpass import build_fast_food_bbox_query, parse_fast_food_elements, query_overpass, unique_chains

st.set_page_config(page_title="Macro Map", layout="wide")

DEFAULT_CENTER = (31.0000, -99.0000)  # Texas-wide starting view.
DEFAULT_ZOOM = 6
MAX_QUERY_AREA_SQ_MI = 250.0
MAP_HEIGHT_PX = 620
GITHUB_REPO_URL = "https://github.com/zja1999/Macro-Map"
GITHUB_ISSUE_TEMPLATE = "nutrition-request.md"


def inject_responsive_styles() -> None:
    """Tighten Streamlit/Folium layout on phones without changing desktop behavior much."""
    st.markdown(
        """
        <style>
            .block-container {
                max-width: 1500px;
                padding-top: 1.1rem;
                padding-bottom: 2rem;
            }

            h1 {
                margin-bottom: 0.15rem;
            }

            div[data-testid="stMetric"] {
                background: transparent;
                border: 1px solid rgba(49, 51, 63, 0.18);
                border-radius: 0.65rem;
                padding: 0.55rem 0.7rem;
            }

            div[data-testid="stDataFrame"] {
                width: 100%;
            }

            div[data-testid="stButton"] > button,
            div[data-testid="stDownloadButton"] > button {
                width: 100%;
            }

            /* Streamlit's default mobile stacking can still leave awkward widths. */
            @media (max-width: 768px) {
                .block-container {
                    padding-left: 0.65rem;
                    padding-right: 0.65rem;
                    padding-top: 0.6rem;
                }

                h1 {
                    font-size: 1.75rem !important;
                    line-height: 1.15 !important;
                }

                h2, h3 {
                    font-size: 1.1rem !important;
                    line-height: 1.25 !important;
                }

                p, .stCaption, div[data-testid="stMarkdownContainer"] {
                    font-size: 0.92rem;
                }

                div[data-testid="stHorizontalBlock"] {
                    flex-direction: column !important;
                    gap: 0.85rem !important;
                }

                div[data-testid="column"] {
                    width: 100% !important;
                    min-width: 100% !important;
                    flex: 1 1 100% !important;
                }

                iframe[title="streamlit_folium.st_folium"] {
                    height: 430px !important;
                    min-height: 430px !important;
                    border-radius: 0.6rem;
                }

                .leaflet-control-container .leaflet-top.leaflet-right {
                    top: 0.35rem;
                    right: 0.35rem;
                }

                .leaflet-draw-toolbar a {
                    width: 34px !important;
                    height: 34px !important;
                    line-height: 34px !important;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def dataframe_height(row_count: int, *, min_height: int = 180, max_height: int = 420) -> int:
    """Return a reasonable dataframe height so tables do not dominate mobile screens."""
    if row_count <= 0:
        return min_height
    return max(min_height, min(max_height, 38 + row_count * 35))


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_fast_food_locations_in_bbox(south: float, west: float, north: float, east: float) -> pd.DataFrame:
    """Fetch named OSM fast-food locations in a bounding box."""
    query = build_fast_food_bbox_query(south, west, north, east)
    payload = query_overpass(query)
    return parse_fast_food_elements(payload)


def build_map(center: tuple[float, float] = DEFAULT_CENTER, zoom: int = DEFAULT_ZOOM, locations: pd.DataFrame | None = None) -> folium.Map:
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

    if locations is not None and not locations.empty:
        cluster = MarkerCluster(name="Fast food locations").add_to(m)
        for _, row in locations.dropna(subset=["latitude", "longitude"]).iterrows():
            label = row["chain"]
            if row.get("name") and row["name"] != row["chain"]:
                label = f"{row['chain']} — {row['name']}"
            folium.Marker(
                location=[row["latitude"], row["longitude"]],
                tooltip=label,
                popup=folium.Popup(label, max_width=300),
            ).add_to(cluster)

    return m


def style_chain_table(df: pd.DataFrame):
    """Color chain rows by nutrition coverage without background shading."""

    def row_style(row: pd.Series) -> list[str]:
        color = "color: #198754; font-weight: 600" if bool(row.get("nutrition_on_file", False)) else "color: #b00020; font-weight: 600"
        return [color if col in {"chain", "locations", "nutrition_status"} else "" for col in row.index]

    return df.style.apply(row_style, axis=1)


def nutrition_request_url(chain: str) -> str:
    """Return a pre-filled GitHub issue URL for requesting nutrition data."""
    params = urlencode(
        {
            "template": GITHUB_ISSUE_TEMPLATE,
            "title": f"Nutrition data request: {chain}",
            "body": (
                f"Chain requested from Macro Map: {chain}\n\n"
                "Please add a matching CSV in `data/nutrition/` using the standard nutrition format."
            ),
        }
    )
    return f"{GITHUB_REPO_URL}/issues/new?{params}"


def recommend_single_items(menu_items: pd.DataFrame, protein_goal: float, calorie_limit: float, top_n: int = 10) -> pd.DataFrame:
    """Rank individual menu items by protein goal first, then calories."""
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


def render_single_item_recommendations(menu_items: pd.DataFrame) -> None:
    """Render a minimal macro-target recommender for individual menu items only."""
    with st.expander("Single item recommendations", expanded=True):
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


def render_map_chain_finder() -> None:
    st.caption("Draw an area on the Texas map, then list unique fast-food chains inside it.")

    library = load_nutrition_library()

    left, right = st.columns([1.8, 1], gap="large")

    if "locations" not in st.session_state:
        st.session_state.locations = pd.DataFrame()
    if "chains" not in st.session_state:
        st.session_state.chains = pd.DataFrame()

    with left:
        m = build_map(locations=st.session_state.locations)
        map_data = st_folium(
            m,
            height=MAP_HEIGHT_PX,
            use_container_width=True,
            returned_objects=["all_drawings", "last_active_drawing"],
        )

    with right:
        st.subheader("Selection")
        if st.button("Clear results"):
            st.session_state.locations = pd.DataFrame()
            st.session_state.chains = pd.DataFrame()
            st.rerun()
        st.caption(f"Draw a rectangle or polygon under about {MAX_QUERY_AREA_SQ_MI:.0f} sq mi, then search.")
        latest_feature = get_latest_drawn_feature(map_data)

        if latest_feature is None:
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
                elif st.button("Find fast-food chains", type="primary"):
                    try:
                        with st.spinner("Searching OpenStreetMap..."):
                            # Use bbox for reliable Overpass querying, then filter back to the exact drawn polygon.
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
            st.caption("For missing chains, use the request link to open a pre-filled GitHub issue.")
            st.download_button(
                "Download chain list CSV",
                data=annotated_chains.drop(columns=["chain_key"], errors="ignore").to_csv(index=False).encode("utf-8"),
                file_name="fast_food_chains.csv",
                mime="text/csv",
            )

    if not st.session_state.locations.empty:
        with st.expander("Location-level results", expanded=False):
            display_cols = [
                "chain",
                "name",
                "brand",
                "operator",
                "cuisine",
                "address",
                "latitude",
                "longitude",
                "osm_type",
                "osm_id",
            ]
            present_cols = [col for col in display_cols if col in st.session_state.locations.columns]
            st.dataframe(
                st.session_state.locations[present_cols],
                use_container_width=True,
                hide_index=True,
                height=dataframe_height(len(st.session_state.locations), min_height=220, max_height=520),
            )
            st.download_button(
                "Download location details CSV",
                data=st.session_state.locations.to_csv(index=False).encode("utf-8"),
                file_name="fast_food_locations.csv",
                mime="text/csv",
            )

    # Keep the nutrition section hidden until the user has searched a drawn area.
    # This prevents the full library from appearing before any map selection exists.
    if not st.session_state.chains.empty:
        with st.expander("Menu macros for selected area", expanded=True):
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
            else:
                if "is_sample_data" in menu_items.columns:
                    sample_mask = menu_items["is_sample_data"].astype(str).str.lower().isin(["true", "1", "yes", "y"])
                    if bool(sample_mask.any()):
                        st.warning("Some displayed macro rows are sample test data. Replace those CSVs with real nutrition files before using the numbers.")

                render_single_item_recommendations(menu_items)

                with st.expander("Menu macros for selected area", expanded=False):
                    preferred_cols = [
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
                    present_cols = [col for col in preferred_cols if col in menu_items.columns]
                    extra_cols = [
                        col
                        for col in menu_items.columns
                        if col not in present_cols and col not in {"chain_key", "source_file"}
                    ]
                    st.dataframe(
                        menu_items[present_cols + extra_cols],
                        use_container_width=True,
                        hide_index=True,
                        height=dataframe_height(len(menu_items), min_height=240, max_height=560),
                    )
                    st.download_button(
                        "Download selected menu macros CSV",
                        data=menu_items.drop(columns=["chain_key"], errors="ignore").to_csv(index=False).encode("utf-8"),
                        file_name="menu_macros_selected_area.csv",
                        mime="text/csv",
                    )


def main() -> None:
    inject_responsive_styles()
    st.title("Macro Map")
    render_map_chain_finder()


if __name__ == "__main__":
    main()
