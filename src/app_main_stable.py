from __future__ import annotations

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

try:
    from folium.plugins import LocateControl
except ImportError:  # pragma: no cover - depends on installed folium version
    LocateControl = None

from src.app_main_compact import render_selected_chain_tabs, style_chain_table
from src.geojson_utils import (
    SelectionError,
    bbox_from_circle,
    circle_area_sq_miles,
    circle_from_feature,
    filter_locations_to_circle,
    get_latest_drawn_feature,
)
from src.nutrition_store import annotate_chains_with_nutrition, load_nutrition_library
from src.osm_overpass import build_fast_food_bbox_query, parse_fast_food_elements, query_overpass, unique_chains
from src.ui_helpers import (
    dataframe_height,
    inject_responsive_styles,
    nutrition_request_mailto_url,
    nutrition_request_message,
    nutrition_request_url,
)

DEFAULT_CENTER = (31.0000, -99.0000)
DEFAULT_ZOOM = 9
SEARCH_ZOOM = 11
MAX_QUERY_AREA_SQ_MI = 250.0
MAP_HEIGHT_PX = 650
METERS_PER_MILE = 1609.344


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_fast_food_locations_in_bbox(south: float, west: float, north: float, east: float) -> pd.DataFrame:
    query = build_fast_food_bbox_query(south, west, north, east)
    payload = query_overpass(query)
    return parse_fast_food_elements(payload)


def configure_page() -> None:
    st.set_page_config(page_title="Macro Map", layout="wide")


def location_label(row: pd.Series) -> str:
    label = str(row.get("chain", "Unknown chain"))
    name = str(row.get("name", "") or "")
    if name and name != label:
        label = f"{label} — {name}"
    return label


def selected_chain_from_widget(annotated_chains: pd.DataFrame) -> str | None:
    """Read the selected chain from the selectable table's stored widget state."""
    table_state = st.session_state.get("unique_chains_selectable_table")
    selected_rows: list[int] = []

    if isinstance(table_state, dict):
        selection = table_state.get("selection", {})
        if isinstance(selection, dict):
            selected_rows = list(selection.get("rows", []))
    elif table_state is not None and hasattr(table_state, "selection"):
        selection = table_state.selection
        if hasattr(selection, "rows"):
            selected_rows = list(selection.rows)

    if not selected_rows:
        return None

    selected_index = int(selected_rows[0])
    if selected_index < 0 or selected_index >= len(annotated_chains):
        return None

    return str(annotated_chains.iloc[selected_index]["chain"])


def add_location_pins(m: folium.Map, locations: pd.DataFrame, highlighted_chain: str | None) -> None:
    if locations.empty or not {"latitude", "longitude", "chain"}.issubset(locations.columns):
        return

    highlighted_key = str(highlighted_chain or "").strip().lower()
    for _, row in locations.dropna(subset=["latitude", "longitude"]).iterrows():
        chain = str(row.get("chain", ""))
        is_highlighted = bool(highlighted_key and chain.strip().lower() == highlighted_key)
        folium.Marker(
            location=[float(row["latitude"]), float(row["longitude"])],
            tooltip=location_label(row),
            popup=folium.Popup(location_label(row), max_width=300),
            icon=folium.Icon(
                color="red" if is_highlighted else "blue",
                icon="cutlery" if is_highlighted else "info-sign",
                prefix="glyphicon",
            ),
        ).add_to(m)


def build_map(locations: pd.DataFrame, highlighted_chain: str | None, auto_locate: bool) -> folium.Map:
    """Build a stable map.

    User location is used only as a one-time starter center through Leaflet's
    LocateControl. After that, the map uses the stored app center and never
    tracks or repeatedly re-centers on browser location.
    """
    center = st.session_state.get("stable_map_center", DEFAULT_CENTER)
    zoom = st.session_state.get("stable_map_zoom", DEFAULT_ZOOM)
    m = folium.Map(location=list(center), zoom_start=int(zoom), control_scale=True, zoom_control=True)

    if LocateControl is not None:
        LocateControl(
            auto_start=auto_locate,
            keep_current_zoom_level=True,
            locate_options={"maxZoom": DEFAULT_ZOOM, "watch": False},
        ).add_to(m)

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

    add_location_pins(m, locations, highlighted_chain)
    return m


def selected_chain_from_table_event(table_event, annotated_chains: pd.DataFrame) -> pd.Series | None:
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


def render_selectable_chain_table(annotated_chains: pd.DataFrame) -> pd.Series | None:
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

        st.text_area("Copyable request message", request_message, height=130)


def render_unique_chains_panel(library) -> pd.DataFrame:
    st.subheader("Unique chains")
    chains = st.session_state.chains
    locations = st.session_state.locations

    if chains.empty:
        st.write("No chain results yet.")
        return pd.DataFrame()

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
    return annotated_chains


def circle_signature(center_lat: float, center_lon: float, radius_m: float) -> tuple[float, float, float]:
    return round(center_lat, 5), round(center_lon, 5), round(radius_m, 1)


def render_selection_panel(map_data) -> None:
    st.subheader("Selection")
    if st.button("Clear selection", width="stretch"):
        st.session_state.locations = pd.DataFrame()
        st.session_state.chains = pd.DataFrame()
        st.session_state.last_circle_signature = None
        st.session_state.stable_map_center = DEFAULT_CENTER
        st.session_state.stable_map_zoom = DEFAULT_ZOOM
        st.session_state.map_reset_token += 1
        st.rerun()

    st.caption(f"Draw one circle under about {MAX_QUERY_AREA_SQ_MI:.0f} sq mi. The search runs automatically when the circle is valid.")
    latest_feature = get_latest_drawn_feature(map_data)

    if latest_feature is None:
        st.info("Click the circle tool, then drag on the map to set your search radius.")
        return

    try:
        center_lat, center_lon, radius_m = circle_from_feature(latest_feature)
        area_sq_miles = circle_area_sq_miles(radius_m)
        radius_miles = radius_m / METERS_PER_MILE
        south, west, north, east = bbox_from_circle(center_lat, center_lon, radius_m)
        st.write(f"Selected circle: **{area_sq_miles:,.1f} sq mi** / **{radius_miles:,.1f} mi radius**")

        if area_sq_miles > MAX_QUERY_AREA_SQ_MI:
            st.warning("That circle is too large. Draw a smaller circle to auto-search.")
            return

        signature = circle_signature(center_lat, center_lon, radius_m)
        if st.session_state.get("last_circle_signature") == signature:
            st.success("Search area loaded.")
            return

        with st.spinner("Searching OpenStreetMap..."):
            bbox_locations = fetch_fast_food_locations_in_bbox(south, west, north, east)
            locations = filter_locations_to_circle(bbox_locations, center_lat, center_lon, radius_m)
        st.session_state.locations = locations
        st.session_state.chains = unique_chains(locations)
        st.session_state.last_circle_signature = signature
        st.session_state.stable_map_center = (center_lat, center_lon)
        st.session_state.stable_map_zoom = SEARCH_ZOOM
        st.rerun()
    except RuntimeError as exc:
        st.error("OpenStreetMap's public Overpass API rejected or timed out on this request.")
        with st.expander("Technical details"):
            st.code(str(exc))
    except SelectionError as exc:
        st.error(str(exc))


def render_map_chain_finder() -> None:
    st.caption("Draw a circle on the map, then list unique fast-food chains inside it.")
    library = load_nutrition_library()

    if "locations" not in st.session_state:
        st.session_state.locations = pd.DataFrame()
    if "chains" not in st.session_state:
        st.session_state.chains = pd.DataFrame()
    if "last_circle_signature" not in st.session_state:
        st.session_state.last_circle_signature = None
    if "map_reset_token" not in st.session_state:
        st.session_state.map_reset_token = 0
    if "stable_map_center" not in st.session_state:
        st.session_state.stable_map_center = DEFAULT_CENTER
    if "stable_map_zoom" not in st.session_state:
        st.session_state.stable_map_zoom = DEFAULT_ZOOM
    if "starter_location_used" not in st.session_state:
        st.session_state.starter_location_used = False

    annotated_chains = annotate_chains_with_nutrition(st.session_state.chains, library) if not st.session_state.chains.empty else pd.DataFrame()
    highlighted_chain = selected_chain_from_widget(annotated_chains) if not annotated_chains.empty else None

    map_col, selection_col, chains_col = st.columns([1.8, 0.8, 1.05], gap="large")

    with map_col:
        auto_locate = not bool(st.session_state.starter_location_used)
        map_data = st_folium(
            build_map(st.session_state.locations, highlighted_chain, auto_locate=auto_locate),
            height=MAP_HEIGHT_PX,
            use_container_width=True,
            returned_objects=["all_drawings", "last_active_drawing"],
            key=f"macro_map_{st.session_state.map_reset_token}",
        )
        if auto_locate:
            st.session_state.starter_location_used = True

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
