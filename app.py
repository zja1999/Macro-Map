from __future__ import annotations

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
from src.nutrition_parser import extract_text_from_pdf, parse_nutrition_text
from src.nutrition_store import (
    annotate_chains_with_nutrition,
    filter_files_for_chains,
    filter_items_for_chains,
    load_nutrition_library,
    nutrition_dir,
)
from src.osm_overpass import build_fast_food_bbox_query, parse_fast_food_elements, query_overpass, unique_chains

st.set_page_config(page_title="Macro Map", layout="wide")

DEFAULT_CENTER = (31.0000, -99.0000)  # Texas-wide starting view.
DEFAULT_ZOOM = 6
MAX_QUERY_AREA_SQ_MI = 250.0


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


def render_map_chain_finder() -> None:
    st.caption("Draw an area on the Texas map, then list unique fast-food chains inside it.")

    library = load_nutrition_library()

    with st.sidebar:
        st.header("Project data")
        st.write("Nutrition CSV files go here:")
        st.code(str(nutrition_dir()), language="text")
        st.caption("Use one real CSV per chain, for example `chick_fil_a.csv`. Filenames and chain names are normalized, so this matches `Chick-fil-A`.")
        st.divider()
        st.write("Tips")
        st.write("Draw a rectangle for the fastest query. Polygons work too.")
        st.write(f"Keep the selected area under about {MAX_QUERY_AREA_SQ_MI:.0f} sq mi for this public-API prototype.")
        if st.button("Clear results"):
            st.session_state.locations = pd.DataFrame()
            st.session_state.chains = pd.DataFrame()
            st.rerun()

    left, right = st.columns([2, 1], gap="large")

    if "locations" not in st.session_state:
        st.session_state.locations = pd.DataFrame()
    if "chains" not in st.session_state:
        st.session_state.chains = pd.DataFrame()

    with left:
        m = build_map(locations=st.session_state.locations)
        map_data = st_folium(
            m,
            height=650,
            use_container_width=True,
            returned_objects=["all_drawings", "last_active_drawing"],
        )

    with right:
        st.subheader("Selection")
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

            display_chains = annotated_chains[["chain", "locations", "nutrition_status", "nutrition_on_file"]]
            st.dataframe(
                style_chain_table(display_chains),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "chain": "Chain",
                    "locations": "# Locations",
                    "nutrition_status": "Nutrition",
                    "nutrition_on_file": None,
                },
            )
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
            st.dataframe(st.session_state.locations[present_cols], use_container_width=True, hide_index=True)
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
                )
                st.download_button(
                    "Download selected menu macros CSV",
                    data=menu_items.drop(columns=["chain_key"], errors="ignore").to_csv(index=False).encode("utf-8"),
                    file_name="menu_macros_selected_area.csv",
                    mime="text/csv",
                )


def render_nutrition_parser() -> None:
    st.caption(
        "Upload a nutrition PDF or copied text file. The parser creates a reviewable CSV with stable macro columns."
    )

    st.warning(
        "This is a best-effort parser, not a guaranteed universal parser. Restaurant PDFs vary a lot, "
        "so rows marked `needs_review` should be checked before you treat them as final data."
    )

    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_a:
        chain = st.text_input("Chain name", value="Chick-fil-A")
    with col_b:
        expected_choice = st.selectbox(
            "Expected nutrient values after serving size",
            options=["auto", "9", "10"],
            help="Use 9 for calories/fat/sat/trans/cholesterol/sodium/carbs/fiber/sugar. Use 10 if protein is included too.",
        )
    with col_c:
        exclude_common = st.checkbox("Exclude drinks/treats/catering", value=False)

    uploaded = st.file_uploader("Upload PDF or text file", type=["pdf", "txt"])

    if not uploaded:
        st.info("Upload a PDF or .txt file to test the parser.")
        st.code(
            "python scripts/parse_nutrition.py nutrition.pdf --chain \"Chick-fil-A\" --output data/chick_fil_a_nutrition.csv",
            language="bash",
        )
        return

    expected_nutrients: str | int = expected_choice
    if expected_choice != "auto":
        expected_nutrients = int(expected_choice)

    try:
        if uploaded.name.lower().endswith(".pdf"):
            text = extract_text_from_pdf(uploaded.getvalue())
        else:
            text = uploaded.getvalue().decode("utf-8", errors="ignore")

        exclude_categories = None
        if exclude_common:
            exclude_categories = [
                "Drinks",
                "Treats",
                "Dipping Sauces",
                "Dressings",
                "Buns",
                "Proteins",
                "Salad Toppings",
                "Sandwich Toppings",
                "Soup Toppings",
                "Trays",
                "Catering",
                "Catering Entrées",
                "Catering Drinks",
            ]

        df = parse_nutrition_text(
            text,
            chain=chain.strip() or "Unknown Chain",
            expected_nutrients=expected_nutrients,
            exclude_categories=exclude_categories,
        )
    except Exception as exc:
        st.error("Could not parse that file.")
        with st.expander("Technical details"):
            st.code(str(exc))
        return

    if df.empty:
        st.error("No nutrition rows were detected. If this is a scanned PDF, it may need OCR first.")
        with st.expander("Extracted text preview"):
            st.text(text[:5000])
        return

    review_count = int(df["needs_review"].sum())
    st.metric("Rows parsed", len(df))
    st.metric("Rows needing review", review_count)

    important_cols = [
        "chain",
        "category",
        "item_name",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "serving_size",
        "needs_review",
        "review_reason",
    ]
    st.dataframe(df[important_cols], use_container_width=True, hide_index=True)

    st.download_button(
        "Download parsed nutrition CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"{chain.lower().replace(' ', '_').replace('-', '_')}_nutrition_parsed.csv",
        mime="text/csv",
    )

    with st.expander("Raw extracted text preview"):
        st.text(text[:8000])


def main() -> None:
    st.title("Macro Map")
    map_tab, parser_tab = st.tabs(["Map Chain Finder", "Nutrition Parser"])
    with map_tab:
        render_map_chain_finder()
    with parser_tab:
        render_nutrition_parser()


if __name__ == "__main__":
    main()
