from __future__ import annotations

import re
import unicodedata

# Add/edit this list as you encounter names from OpenStreetMap.
# Keys are simplified lowercase strings produced by _simplify().
KNOWN_CHAIN_ALIASES = {
    "mcdonalds": "McDonald's",
    "mcdonald s": "McDonald's",
    "mc donalds": "McDonald's",
    "taco bell": "Taco Bell",
    "chick fil a": "Chick-fil-A",
    "chickfila": "Chick-fil-A",
    "chik fil a": "Chick-fil-A",
    "burger king": "Burger King",
    "wendys": "Wendy's",
    "wendy s": "Wendy's",
    "wendys hamburgers": "Wendy's",
    "whataburger": "Whataburger",
    "in n out burger": "In-N-Out Burger",
    "in n out": "In-N-Out Burger",
    "jack in the box": "Jack in the Box",
    "popeyes": "Popeyes",
    "popeyes louisiana kitchen": "Popeyes",
    "kfc": "KFC",
    "kentucky fried chicken": "KFC",
    "arbys": "Arby's",
    "arby s": "Arby's",
    "sonic": "Sonic",
    "sonic drive in": "Sonic",
    "subway": "Subway",
    "jimmy johns": "Jimmy John's",
    "jersey mikes": "Jersey Mike's",
    "jersey mike s": "Jersey Mike's",
    "jersey mikes subs": "Jersey Mike's",
    "firehouse subs": "Firehouse Subs",
    "panera bread": "Panera Bread",
    "chipotle": "Chipotle",
    "chipotle mexican grill": "Chipotle",
    "qdoba": "Qdoba",
    "moes southwest grill": "Moe's Southwest Grill",
    "panda express": "Panda Express",
    "raising canes": "Raising Cane's",
    "raising cane s": "Raising Cane's",
    "raising canes chicken fingers": "Raising Cane's",
    "canes": "Raising Cane's",
    "dairy queen": "Dairy Queen",
    "dq": "Dairy Queen",
    "culvers": "Culver's",
    "zaxbys": "Zaxby's",
    "wingstop": "Wingstop",
    "dominos": "Domino's",
    "domino s": "Domino's",
    "dominos pizza": "Domino's",
    "pizza hut": "Pizza Hut",
    "little caesars": "Little Caesars",
    "little caesars pizza": "Little Caesars",
    "papa johns": "Papa Johns",
    "papa john s": "Papa Johns",
    "papa johns pizza": "Papa Johns",
    "starbucks": "Starbucks",
    "dunkin": "Dunkin'",
    "dunkin donuts": "Dunkin'",
    "smoothie king": "Smoothie King",
    "jamba": "Jamba",
    "jamba juice": "Jamba",
    "el pollo loco": "El Pollo Loco",
    "five guys": "Five Guys",
    "five guys burgers and fries": "Five Guys",
}

GENERIC_SUFFIXES = (
    " restaurant",
    " restaurants",
    " drive thru",
    " drive through",
    " drive in",
    " grill",
    " cafe",
)


def _simplify(value: str) -> str:
    """Normalize a free-text brand/name enough for alias matching."""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = value.replace("&", " and ")
    value = re.sub(r"[#№]?\s*\d{2,}\b", " ", value)  # remove store numbers
    value = re.sub(r"\b(store|location|unit)\s*\d+\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    for suffix in GENERIC_SUFFIXES:
        if value.endswith(suffix.strip()):
            value = value[: -len(suffix.strip())].strip()
    return value


def normalize_chain_name(raw_name: str | None) -> str | None:
    """Return a stable chain display name, or None when no name is usable."""
    if not raw_name:
        return None

    raw_name = raw_name.strip()
    if not raw_name:
        return None

    simplified = _simplify(raw_name)
    if not simplified:
        return None

    if simplified in KNOWN_CHAIN_ALIASES:
        return KNOWN_CHAIN_ALIASES[simplified]

    # Handle common OSM naming patterns such as "McDonald's - Main Street".
    first_part = re.split(r"\s[-–—|/]\s", raw_name, maxsplit=1)[0].strip()
    if first_part and first_part != raw_name:
        first_simplified = _simplify(first_part)
        if first_simplified in KNOWN_CHAIN_ALIASES:
            return KNOWN_CHAIN_ALIASES[first_simplified]
        return first_part

    # Title-case unknown chains while preserving short all-caps names reasonably.
    if raw_name.isupper() and len(raw_name) <= 5:
        return raw_name
    return " ".join(word.capitalize() if not word.isupper() else word for word in raw_name.split())


def choose_chain_name(tags: dict) -> str | None:
    """Prefer OSM brand tag, then name, then operator."""
    for key in ("brand", "name", "operator"):
        normalized = normalize_chain_name(tags.get(key))
        if normalized:
            return normalized
    return None



def chain_key(raw_name: str | None) -> str:
    """Return a normalized key for matching map chain names to nutrition files."""
    normalized = normalize_chain_name(raw_name)
    return _simplify(normalized or raw_name or "")
