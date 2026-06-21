from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests

from .chain_normalizer import choose_chain_name

DEFAULT_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

REQUEST_HEADERS = {
    # Public Overpass instances prefer identifiable clients. This is not authentication;
    # it just makes the prototype look less like anonymous bulk traffic.
    "User-Agent": "MacroMapPrototype/0.2 (local Streamlit app)",
}


@dataclass(frozen=True)
class FastFoodLocation:
    osm_type: str
    osm_id: int
    chain: str
    name: str | None
    brand: str | None
    operator: str | None
    cuisine: str | None
    latitude: float | None
    longitude: float | None
    address: str | None
    website: str | None


def build_fast_food_query(overpass_poly: str) -> str:
    """Build an Overpass QL query for fast-food POIs inside a polygon.

    Kept for future use. The app currently uses the bbox query below because it is
    much less likely to be rejected by public Overpass endpoints.
    """
    return f'''
[out:json][timeout:25];
(
  nwr["amenity"="fast_food"](poly:"{overpass_poly}");
);
out center tags;
'''.strip()


def build_fast_food_bbox_query(south: float, west: float, north: float, east: float) -> str:
    """Build an Overpass QL query for fast-food POIs inside a bbox.

    Bbox queries are more reliable than arbitrary polygon queries on public Overpass
    servers. The app filters the returned points back to the exact drawn shape.
    """
    return f'''
[out:json][timeout:50];
(
  nwr["amenity"="fast_food"]["name"]({south:.7f},{west:.7f},{north:.7f},{east:.7f});
);
out center tags;
'''.strip()


def _short_response_text(response: requests.Response, limit: int = 400) -> str:
    text = response.text.strip().replace("\n", " ")
    if not text:
        return ""
    return text[:limit] + ("..." if len(text) > limit else "")


def query_overpass(query: str, endpoints: list[str] | None = None, timeout: int = 70) -> dict[str, Any]:
    """Run an Overpass query with endpoint failover and useful error details."""
    endpoints = endpoints or DEFAULT_OVERPASS_ENDPOINTS
    errors: list[str] = []

    for endpoint in endpoints:
        try:
            response = requests.post(endpoint, data={"data": query}, headers=REQUEST_HEADERS, timeout=timeout)
            if response.status_code >= 400:
                detail = _short_response_text(response)
                raise requests.HTTPError(
                    f"{response.status_code} {response.reason}" + (f" — {detail}" if detail else ""),
                    response=response,
                )
            return response.json()
        except Exception as exc:  # noqa: BLE001 - preserve endpoint failover details
            errors.append(f"{endpoint}: {exc}")
            time.sleep(1)

    raise RuntimeError("All Overpass endpoints failed. " + " | ".join(errors))


def _element_lat_lon(element: dict[str, Any]) -> tuple[float | None, float | None]:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None, None


def _address_from_tags(tags: dict[str, str]) -> str | None:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:city"),
        tags.get("addr:state"),
    ]
    address = " ".join(part for part in parts if part)
    return address or None


def parse_fast_food_elements(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert Overpass JSON into a location DataFrame with normalized chain names."""
    rows: list[FastFoodLocation] = []

    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        chain = choose_chain_name(tags)
        if not chain:
            continue

        lat, lon = _element_lat_lon(element)
        rows.append(
            FastFoodLocation(
                osm_type=element.get("type", ""),
                osm_id=int(element.get("id")),
                chain=chain,
                name=tags.get("name"),
                brand=tags.get("brand"),
                operator=tags.get("operator"),
                cuisine=tags.get("cuisine"),
                latitude=lat,
                longitude=lon,
                address=_address_from_tags(tags),
                website=tags.get("website") or tags.get("contact:website"),
            )
        )

    columns = [
        "chain",
        "name",
        "brand",
        "operator",
        "cuisine",
        "latitude",
        "longitude",
        "address",
        "website",
        "osm_type",
        "osm_id",
    ]

    df = pd.DataFrame([row.__dict__ for row in rows])
    if df.empty:
        return pd.DataFrame(columns=columns)

    # Drop duplicate OSM elements and normalize sort order.
    df = df.drop_duplicates(subset=["osm_type", "osm_id"]).sort_values(["chain", "name"], na_position="last")
    return df[columns]


def unique_chains(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per chain with a location count."""
    if df.empty:
        return pd.DataFrame(columns=["chain", "locations"])
    return (
        df.groupby("chain", as_index=False)
        .size()
        .rename(columns={"size": "locations"})
        .sort_values(["locations", "chain"], ascending=[False, True])
    )
