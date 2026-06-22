from __future__ import annotations

from math import asin, cos, pi, radians, sin, sqrt
from typing import Any

import pandas as pd


class SelectionError(ValueError):
    pass


EARTH_RADIUS_M = 6_371_008.8
METERS_PER_MILE = 1609.344
METERS_PER_DEGREE_LAT = 111_320.0


def is_circle_feature(feature: dict[str, Any] | None) -> bool:
    """Return True only for Leaflet.draw circle features.

    Browser geolocation and other map artifacts can also appear as Point
    features, so we require a radius property before treating a Point as the
    user's search circle.
    """
    if not isinstance(feature, dict):
        return False

    geometry = feature.get("geometry", feature)
    if geometry.get("type") != "Point":
        return False

    properties = feature.get("properties") or {}
    radius = properties.get("radius") or properties.get("_mRadius") or feature.get("radius")
    return radius is not None


def get_latest_drawn_feature(map_data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the latest user-drawn circle feature from streamlit-folium data."""
    if not map_data:
        return None

    drawings = map_data.get("all_drawings") or []
    for drawing in reversed(drawings):
        if is_circle_feature(drawing):
            return drawing

    last = map_data.get("last_active_drawing")
    if is_circle_feature(last):
        return last

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


def circle_from_feature(feature: dict[str, Any]) -> tuple[float, float, float]:
    """Extract a Leaflet.draw circle as (center_lat, center_lon, radius_meters)."""
    geometry = feature.get("geometry", feature)
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")

    if geom_type != "Point" or not coords or len(coords) < 2:
        raise SelectionError("Draw a circle selection. Other map shapes are not supported in this version.")

    properties = feature.get("properties") or {}
    radius = properties.get("radius") or properties.get("_mRadius") or feature.get("radius")
    if radius is None:
        raise SelectionError("Draw a circle by clicking the circle tool, then dragging to set the radius.")

    lon, lat = coords[:2]
    radius_m = float(radius)
    if radius_m <= 0:
        raise SelectionError("The selected circle needs a radius larger than zero.")

    return float(lat), float(lon), radius_m


def polygon_to_overpass_poly(latlon: list[tuple[float, float]]) -> str:
    """Return Overpass poly string: 'lat lon lat lon ...'. Kept for future use."""
    return " ".join(f"{lat:.7f} {lon:.7f}" for lat, lon in latlon)


def bbox_from_latlon(latlon: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Return an Overpass bbox tuple: south, west, north, east."""
    lats = [p[0] for p in latlon]
    lons = [p[1] for p in latlon]
    return min(lats), min(lons), max(lats), max(lons)


def bbox_from_circle(center_lat: float, center_lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """Return south, west, north, east for a circle's bounding box."""
    delta_lat = radius_m / METERS_PER_DEGREE_LAT
    lon_scale = max(cos(radians(center_lat)), 0.01)
    delta_lon = radius_m / (METERS_PER_DEGREE_LAT * lon_scale)
    return center_lat - delta_lat, center_lon - delta_lon, center_lat + delta_lat, center_lon + delta_lon


def rough_bbox_area_sq_miles(latlon: list[tuple[float, float]]) -> float:
    """Quick rough area using bounding box; good enough for query-size warnings."""
    min_lat, min_lon, max_lat, max_lon = bbox_from_latlon(latlon)
    mid_lat = (min_lat + max_lat) / 2
    miles_per_degree_lat = 69.0
    miles_per_degree_lon = 69.172 * cos(radians(mid_lat))
    return abs(max_lat - min_lat) * miles_per_degree_lat * abs(max_lon - min_lon) * miles_per_degree_lon


def circle_area_sq_miles(radius_m: float) -> float:
    """Return selected circle area in square miles."""
    radius_miles = max(float(radius_m), 0.0) / METERS_PER_MILE
    return pi * radius_miles**2


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two lat/lon points in meters."""
    lat1_rad, lon1_rad = radians(lat1), radians(lon1)
    lat2_rad, lon2_rad = radians(lat2), radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(a)))


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


def point_in_circle(lat: float, lon: float, center_lat: float, center_lon: float, radius_m: float) -> bool:
    """Return True when a point is inside a drawn circle."""
    return haversine_distance_m(lat, lon, center_lat, center_lon) <= radius_m


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


def filter_locations_to_circle(df: pd.DataFrame, center_lat: float, center_lon: float, radius_m: float) -> pd.DataFrame:
    """Filter location rows to only points inside a drawn circle."""
    if df.empty or "latitude" not in df.columns or "longitude" not in df.columns:
        return df

    valid = df.dropna(subset=["latitude", "longitude"]).copy()
    mask = [point_in_circle(row.latitude, row.longitude, center_lat, center_lon, radius_m) for row in valid.itertuples()]
    return valid.loc[mask].reset_index(drop=True)
