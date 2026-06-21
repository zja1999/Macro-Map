from __future__ import annotations

from math import cos, radians
from typing import Any

import pandas as pd


class SelectionError(ValueError):
    pass


def get_latest_drawn_feature(map_data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the latest drawn GeoJSON feature from streamlit-folium data."""
    if not map_data:
        return None

    last = map_data.get("last_active_drawing")
    if last and isinstance(last, dict) and last.get("geometry"):
        return last

    drawings = map_data.get("all_drawings") or []
    if drawings:
        return drawings[-1]

    return None


def polygon_latlon_from_feature(feature: dict[str, Any]) -> list[tuple[float, float]]:
    """Extract Polygon coordinates as [(lat, lon), ...] from a GeoJSON feature."""
    geometry = feature.get("geometry", feature)
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")

    if geom_type != "Polygon" or not coords:
        raise SelectionError("Draw a rectangle or polygon. Other map shapes are not supported yet.")

    # GeoJSON polygons are [[[lon, lat], ...]]. Use the exterior ring only.
    exterior_ring = coords[0]
    if len(exterior_ring) < 4:
        raise SelectionError("The selected area needs at least 3 corners.")

    latlon = [(float(lat), float(lon)) for lon, lat, *_ in exterior_ring]

    # Most polygon tools repeat the first point at the end. Keep one copy only.
    if latlon[0] == latlon[-1]:
        latlon = latlon[:-1]

    return latlon


def polygon_to_overpass_poly(latlon: list[tuple[float, float]]) -> str:
    """Return Overpass poly string: 'lat lon lat lon ...'. Kept for future use."""
    return " ".join(f"{lat:.7f} {lon:.7f}" for lat, lon in latlon)


def bbox_from_latlon(latlon: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return an Overpass bbox tuple: south, west, north, east."""
    lats = [p[0] for p in latlon]
    lons = [p[1] for p in latlon]
    return min(lats), min(lons), max(lats), max(lons)


def rough_bbox_area_sq_miles(latlon: list[tuple[float, float]]) -> float:
    """Quick rough area using bounding box; good enough for query-size warnings."""
    min_lat, min_lon, max_lat, max_lon = bbox_from_latlon(latlon)
    mid_lat = (min_lat + max_lat) / 2
    miles_per_degree_lat = 69.0
    miles_per_degree_lon = 69.172 * cos(radians(mid_lat))
    return abs(max_lat - min_lat) * miles_per_degree_lat * abs(max_lon - min_lon) * miles_per_degree_lon


def point_in_polygon(lat: float, lon: float, polygon_latlon: list[tuple[float, float]]) -> bool:
    """Return True when a point is inside a polygon using ray casting.

    Coordinates are passed as lat/lon, but the algorithm treats lon as x and lat as y.
    This is accurate enough for the city/neighborhood-sized selections this app targets.
    """
    x = lon
    y = lat
    inside = False
    points = polygon_latlon
    n = len(points)

    if n < 3:
        return False

    j = n - 1
    for i in range(n):
        yi, xi = points[i]
        yj, xj = points[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        if intersects:
            inside = not inside
        j = i

    return inside


def filter_locations_to_polygon(df: pd.DataFrame, polygon_latlon: list[tuple[float, float]]) -> pd.DataFrame:
    """Filter location rows to only points inside the drawn polygon.

    The Overpass query uses the polygon's bounding box for reliability, then this function
    applies the exact drawn polygon shape inside the app.
    """
    if df.empty or "latitude" not in df.columns or "longitude" not in df.columns:
        return df

    valid = df.dropna(subset=["latitude", "longitude"]).copy()
    mask = [point_in_polygon(row.latitude, row.longitude, polygon_latlon) for row in valid.itertuples()]
    return valid.loc[mask].reset_index(drop=True)
