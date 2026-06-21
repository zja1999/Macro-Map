from __future__ import annotations

import argparse
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

try:  # Optional dependency; installed through requirements.txt in this project.
    import pdfplumber
except Exception:  # pragma: no cover - keeps text parsing usable if pdfplumber is missing.
    pdfplumber = None


DEFAULT_COLUMNS_WITHOUT_PROTEIN = [
    "calories",
    "fat_g",
    "sat_fat_g",
    "trans_fat_g",
    "cholesterol_mg",
    "sodium_mg",
    "carbs_g",
    "fiber_g",
    "sugar_g",
]

DEFAULT_COLUMNS_WITH_PROTEIN = DEFAULT_COLUMNS_WITHOUT_PROTEIN + ["protein_g"]

KNOWN_CATEGORIES = [
    "Breakfast",
    "Entrées",
    "Entrees",
    "Salads",
    "Sides",
    "Kid's Meals",
    "Kids Meals",
    "Treats",
    "Drinks",
    "Dipping Sauces",
    "Dressings",
    "Buns",
    "Proteins",
    "Salad Toppings",
    "Sandwich Toppings",
    "Soup Toppings",
    "Trays",
    "Catering Entrées",
    "Catering Entrees",
    "Catering Drinks",
    "Catering",
]

CATEGORY_NORMALIZATION = {
    "Entrees": "Entrées",
    "Catering Entrees": "Catering Entrées",
    "Kids Meals": "Kid's Meals",
}

NOISE_PHRASES = [
    "Nutrition Allergens",
    "Nutrition & Allergens",
    "Ingredient and Nutrition information",
    "2,000 calories a day",
    "Find restaurants",
    "Sign in/Join",
    "Order now",
    "Family time at your fingertips",
    "Who we are",
    "Do business with us",
    "Customer support",
    "All rights reserved",
    "Privacy policy",
    "Terms & conditions",
    "Cookie preference center",
    "Do not sell",
]

HEADER_WORDS = [
    "SERVING SIZE",
    "CALORIES",
    "FAT (G)",
    "SAT. FAT",
    "TRANS FAT",
    "CHOLESTEROL",
    "SODIUM",
    "CARBOHYDRATES",
    "FIBER",
    "SUGAR",
    "PROTEIN",
]

SERVING_RE = re.compile(r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>g|mg|oz|fl\s*oz|ml)\b", re.IGNORECASE)
NUMBER_AT_START_RE = re.compile(r"^\s*(?P<num>-?\d+(?:\.\d+)?)")
ROW_START_RE = re.compile(
    r"(?P<prefix>.{0,60}?)(?P<serving>\d+(?:\.\d+)?\s*(?:g|mg|oz|fl\s*oz|ml)\b)\s+"
    r"(?P<numbers>-?\d+(?:\.\d+)?(?:\s+-?\d+(?:\.\d+)?){5,})",
    re.IGNORECASE,
)


@dataclass
class ParsedRow:
    chain: str
    category: str | None
    item_name: str
    serving_size: str
    serving_g: float | None
    values: dict[str, float | int | None]
    raw_text: str
    needs_review: bool
    review_reason: str


def clean_text(text: str) -> str:
    """Normalize PDF-extracted text before line parsing."""
    replacements = {
        "\u00a0": " ",
        "\uf0d9": " ",
        "\uf0da": " ",
        "™": "™",
        "®": "®",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Remove common copy/paste artifacts from PDF viewers.
    text = re.sub(r"[\ue000-\uf8ff]", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text_from_pdf(path_or_bytes: str | Path | bytes) -> str:
    """Extract text from a PDF using pdfplumber.

    This works for text-based PDFs. Scanned/image-only PDFs need OCR and will return
    little or no text.
    """
    if pdfplumber is None:
        raise ImportError("pdfplumber is required for PDF extraction. Run: pip install pdfplumber")

    if isinstance(path_or_bytes, bytes):
        pdf_source = io.BytesIO(path_or_bytes)
    else:
        pdf_source = str(path_or_bytes)

    parts: list[str] = []
    with pdfplumber.open(pdf_source) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
    return clean_text("\n".join(parts))


def read_text_or_pdf(path: str | Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return extract_text_from_pdf(path)
    return clean_text(path.read_text(encoding="utf-8", errors="ignore"))


def detect_nutrient_columns(text: str, expected_nutrients: str | int = "auto") -> list[str]:
    """Choose nutrient columns based on headers or a manual count.

    Use 9 for: calories, fat, sat fat, trans fat, cholesterol, sodium, carbs, fiber, sugar.
    Use 10 when the source includes protein after sugar.
    """
    if isinstance(expected_nutrients, int):
        return DEFAULT_COLUMNS_WITH_PROTEIN if expected_nutrients >= 10 else DEFAULT_COLUMNS_WITHOUT_PROTEIN

    text_upper = text.upper()
    # Match a real protein header, not the plural category name "Proteins".
    if re.search(r"\bPROTEIN\b", text_upper):
        return DEFAULT_COLUMNS_WITH_PROTEIN
    return DEFAULT_COLUMNS_WITHOUT_PROTEIN


def canonical_category(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip(" :-|\t")
    value = re.sub(r"\s+", " ", value)
    return CATEGORY_NORMALIZATION.get(value, value)


def detect_category(line: str) -> str | None:
    """Detect category headings that appear with or without nutrition headers."""
    raw = re.sub(r"\s+", " ", line).strip()
    if not raw:
        return None

    # Prefer the text before a nutrition table header.
    if "SERVING SIZE" in raw.upper():
        before = re.split(r"SERVING SIZE", raw, flags=re.IGNORECASE)[0].strip()
        before = re.sub(r"\(nutrition per.*?\)", "", before, flags=re.IGNORECASE).strip()
        for cat in sorted(KNOWN_CATEGORIES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(cat)}\b", before, flags=re.IGNORECASE):
                return canonical_category(cat)

    # A standalone category line.
    for cat in sorted(KNOWN_CATEGORIES, key=len, reverse=True):
        if re.fullmatch(rf"{re.escape(cat)}(?:\s*\(.*\))?", raw, flags=re.IGNORECASE):
            return canonical_category(cat)

    return None


def is_noise_line(line: str) -> bool:
    raw = line.strip()
    if not raw:
        return True
    upper = raw.upper()
    if any(phrase.upper() in upper for phrase in NOISE_PHRASES):
        return True
    # Header-only lines, e.g. "CALORIES" or "SODIUM (MG)".
    if any(word in upper for word in HEADER_WORDS) and not SERVING_RE.search(raw):
        return True
    return False


def looks_like_row_start(line: str) -> bool:
    """Return True when a line likely contains a serving size followed by many numbers."""
    return ROW_START_RE.search(line) is not None


def collapse_item_name(value: str) -> str:
    """Clean item names after PDF extraction.

    Some PDFs split names around numbers: "Be ... 2rry Parfait" becomes
    "Berry Parfait" after this cleanup.
    """
    value = re.sub(r"\s+", " ", value).strip(" -|,;:")
    value = re.sub(r"(?<=\D)\s+(?=\D)", " ", value)

    # Repair common cases where a leading sugar/protein digit was glued to the item name.
    # Example: "1Grilled Chicken" -> "Grilled Chicken" when the digit was already parsed.
    value = re.sub(r"^[0-9]+(?=[A-Z][a-z])", "", value).strip()

    # Repair names split around the serving size, e.g. "G rilled Nuggets" -> "Grilled Nuggets"
    # and "Be Berry Parfait" -> "Berry Parfait".
    value = re.sub(
        r"^([A-Z][a-z]?)\s+([a-z][A-Za-z®™'-]*)",
        lambda m: m.group(1) + m.group(2),
        value,
    )
    parts = value.split(maxsplit=2)
    if len(parts) >= 2 and 1 <= len(parts[0]) <= 2 and parts[1].lower().startswith(parts[0].lower()):
        value = " ".join(parts[1:])
    if value.startswith("Bag o Bag of Ice"):
        value = "Bag of Ice"

    # Repair words split by the table extraction: "Wh ite" -> "White", "Chee se" -> "Cheese".
    # Keep this conservative so normal multi-word names are not destroyed.
    small_fragments = {
        "Wh ite": "White",
        "Chickn Minis": "Chick-n-Minis",
        "Chick nStrips": "Chick-n-Strips",
        "Chick n Strips": "Chick-n-Strips",
        "Chick filA": "Chick-fil-A",
        "Chick fil A": "Chick-fil-A",
        "Chee se": "Cheese",
        "Sa lad": "Salad",
        "Ka le": "Kale",
        "Ma rket": "Market",
        "sh Browns": "Hash Browns",
        "rry Parfait": "Berry Parfait",
        "ruit Cup": "Fruit Cup",
        "reakfast Breads": "Breakfast Breads",
        "rosted Lemonade": "Frosted Lemonade",
        "rosted Coffee": "Frosted Coffee",
        "r Pepper": "Dr Pepper",
        "lash": "Flash",
        "t Coffee": "Hot Coffee",
        "llon Beverages": "Gallon Beverages",
        "imply Orange": "Simply Orange",
        "lynesian Sauce": "Polynesian Sauce",
        "luten Free Bun": "Gluten Free Bun",
        "tter y White Bun": "Buttery White Bun",
        "hite Bun": "White Bun",
        "icy Filet": "Spicy Filet",
        "ick-fil-A": "Chick-fil-A",
        "rispy Bell Peppers": "Crispy Bell Peppers",
        "ili Lime Pepitas": "Chili Lime Pepitas",
        "merican Cheese": "American Cheese",
        "lby Jack Cheese": "Colby Jack Cheese",
        "omato": "Tomato",
        "ettuce": "Lettuce",
        "ltines": "Saltines",
        "ruit Tray": "Fruit Tray",
        "arden Salad": "Garden Salad",
        "ttled": "Bottled",
        "atering Coffee": "Catering Coffee",
        "f Ice": "Bag of Ice",
        "sage": "Sausage",
    }
    for bad, good in small_fragments.items():
        value = re.sub(rf"\b{re.escape(bad)}\b", good, value)

    value = re.sub(r"\s+", " ", value).strip()
    return value


def _parse_leading_numbers(text: str, max_count: int) -> tuple[list[float], str]:
    values: list[float] = []
    rest = text
    while len(values) < max_count:
        m = NUMBER_AT_START_RE.match(rest)
        if not m:
            break
        number_text = m.group("num")
        after = rest[m.end():]

        # Guard against reading an item name count as a nutrient.
        # Example after 8 nutrition values: "5 Ct Nuggets" should remain part of the item name.
        if len(values) >= max_count - 1 and re.match(r"\s+(Ct|Pc|Piece|Pieces)\b", after, flags=re.IGNORECASE):
            break

        # Guard against reading an item name that starts with a percent sign as another value.
        # e.g. after parsing sugar, "1% Milk" can be an item name, not protein.
        if values and after.lstrip().startswith("%"):
            break

        values.append(float(number_text) if "." in number_text else int(number_text))
        rest = after

        # If the next character is a letter and we already have a useful number count,
        # it is probably the start of the item name glued to the last value.
        if re.match(r"^[A-Za-z®™&]", rest.lstrip()) and len(values) >= max_count - 1:
            break
    return values, rest.strip()


def split_possible_rows(line: str) -> list[str]:
    """Split lines that accidentally contain multiple serving-size rows.

    This is intentionally conservative. It helps with PDF text such as:
    "Bacon 10g ... 0 0 Sau 57g ... sage".
    """
    serving_matches = list(SERVING_RE.finditer(line))
    if len(serving_matches) <= 1:
        return [line]

    chunks: list[str] = []
    start = 0
    for i, match in enumerate(serving_matches):
        if i == 0:
            continue
        # Split shortly before the next serving token, keeping a small name prefix with it.
        split_at = match.start()
        prefix_start = line.rfind(" ", 0, split_at - 1)
        # Keep one short word before the serving amount as the next row's prefix.
        if prefix_start != -1 and split_at - prefix_start <= 20:
            split_at = prefix_start + 1
        previous = line[start:split_at].strip()
        if previous:
            chunks.append(previous)
        start = split_at
    last = line[start:].strip()
    if last:
        chunks.append(last)
    return chunks or [line]


def parse_row_text(
    raw_text: str,
    chain: str,
    category: str | None,
    nutrient_columns: list[str],
) -> ParsedRow | None:
    """Parse one candidate row into a ParsedRow."""
    text = re.sub(r"\s+", " ", raw_text).strip()
    serving_match = SERVING_RE.search(text)
    if not serving_match:
        return None

    prefix = text[: serving_match.start()].strip()
    serving_size = serving_match.group(0).replace(" ", "")
    serving_g = None
    if serving_match.group("unit").lower().replace(" ", "") == "g":
        serving_g = float(serving_match.group("amount"))

    after_serving = text[serving_match.end():]
    values, suffix = _parse_leading_numbers(after_serving, max_count=len(nutrient_columns))

    if len(values) < 6:
        return None

    item_name = collapse_item_name(" ".join(part for part in [prefix, suffix] if part))
    if not item_name:
        item_name = "UNKNOWN ITEM"

    values_by_col: dict[str, float | int | None] = {col: None for col in nutrient_columns}
    for col, value in zip(nutrient_columns, values):
        values_by_col[col] = value

    reasons: list[str] = []
    if len(values) != len(nutrient_columns):
        reasons.append(f"expected {len(nutrient_columns)} nutrition values, found {len(values)}")
    if item_name == "UNKNOWN ITEM":
        reasons.append("missing item name")
    if re.search(r"^[a-z]|\b[A-Z][a-z]?\s+[a-z]{1,3}\b", item_name):
        reasons.append("item name may be split by PDF extraction")

    return ParsedRow(
        chain=chain,
        category=category,
        item_name=item_name,
        serving_size=serving_size,
        serving_g=serving_g,
        values=values_by_col,
        raw_text=text,
        needs_review=bool(reasons),
        review_reason="; ".join(reasons),
    )


def iter_candidate_rows(text: str) -> Iterable[tuple[str | None, str]]:
    """Yield (category, raw_row_text) candidate rows from text."""
    text = clean_text(text)
    current_category: str | None = None
    current_row: str | None = None

    def flush_current() -> tuple[str | None, str] | None:
        nonlocal current_row, current_category
        if current_row:
            out = (current_category, current_row.strip())
            current_row = None
            return out
        return None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flushed = flush_current()
            if flushed:
                yield flushed
            continue

        category = detect_category(line)
        if category:
            flushed = flush_current()
            if flushed:
                yield flushed
            current_category = category
            continue

        if is_noise_line(line):
            flushed = flush_current()
            if flushed:
                yield flushed
            continue

        chunks = split_possible_rows(line) if looks_like_row_start(line) else [line]
        for chunk in chunks:
            if looks_like_row_start(chunk):
                flushed = flush_current()
                if flushed:
                    yield flushed
                current_row = chunk
            else:
                # Non-row lines after a row are usually continuation lines for item names.
                if current_row:
                    current_row += " " + chunk
                # Otherwise ignore loose page/footer text.

    flushed = flush_current()
    if flushed:
        yield flushed


def parse_nutrition_text(
    text: str,
    chain: str,
    expected_nutrients: str | int = "auto",
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
) -> pd.DataFrame:
    """Parse nutrition text into a reviewable DataFrame.

    The parser is intentionally tolerant. It returns best-effort rows and flags suspicious
    rows with needs_review=True so you can fix them before joining to the menu app.
    """
    nutrient_columns = detect_nutrient_columns(text, expected_nutrients)
    include_set = {canonical_category(c) for c in include_categories} if include_categories else None
    exclude_set = {canonical_category(c) for c in exclude_categories} if exclude_categories else set()

    rows: list[dict] = []
    seen_raw: set[str] = set()

    for category, candidate in iter_candidate_rows(text):
        if include_set and canonical_category(category) not in include_set:
            continue
        if exclude_set and canonical_category(category) in exclude_set:
            continue

        parsed = parse_row_text(candidate, chain=chain, category=category, nutrient_columns=nutrient_columns)
        if not parsed:
            continue
        if parsed.raw_text in seen_raw:
            continue
        seen_raw.add(parsed.raw_text)

        row = {
            "chain": parsed.chain,
            "category": parsed.category,
            "item_name": parsed.item_name,
            "serving_size": parsed.serving_size,
            "serving_g": parsed.serving_g,
            **parsed.values,
            # Protein often is not present in some chain nutrition PDFs. Keep the column so
            # downstream joins have a stable schema.
            "protein_g": parsed.values.get("protein_g") if "protein_g" in parsed.values else None,
            "needs_review": parsed.needs_review,
            "review_reason": parsed.review_reason,
            "raw_text": parsed.raw_text,
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    stable_cols = [
        "chain",
        "category",
        "item_name",
        "serving_size",
        "serving_g",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sat_fat_g",
        "trans_fat_g",
        "cholesterol_mg",
        "sodium_mg",
        "fiber_g",
        "sugar_g",
        "needs_review",
        "review_reason",
        "raw_text",
    ]
    for col in stable_cols:
        if col not in df.columns:
            df[col] = None
    return df[stable_cols]


def parse_file_to_dataframe(
    path: str | Path,
    chain: str,
    expected_nutrients: str | int = "auto",
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
) -> pd.DataFrame:
    text = read_text_or_pdf(path)
    return parse_nutrition_text(
        text,
        chain=chain,
        expected_nutrients=expected_nutrients,
        include_categories=include_categories,
        exclude_categories=exclude_categories,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a fast-food nutrition PDF/text file into CSV.")
    parser.add_argument("input_file", help="Path to a .pdf or .txt file")
    parser.add_argument("--chain", required=True, help="Chain name, e.g. Chick-fil-A")
    parser.add_argument("--output", "-o", default="parsed_nutrition.csv", help="Output CSV path")
    parser.add_argument(
        "--expected-nutrients",
        default="auto",
        help="auto, 9, or 10. Use 10 if the PDF has a protein column after sugar.",
    )
    parser.add_argument(
        "--include-categories",
        nargs="*",
        default=None,
        help="Optional category whitelist, e.g. Entrées Salads Sides",
    )
    parser.add_argument(
        "--exclude-categories",
        nargs="*",
        default=None,
        help="Optional category blacklist, e.g. Drinks Treats Catering",
    )
    args = parser.parse_args()

    expected: str | int = args.expected_nutrients
    if expected != "auto":
        expected = int(expected)

    df = parse_file_to_dataframe(
        args.input_file,
        chain=args.chain,
        expected_nutrients=expected,
        include_categories=args.include_categories,
        exclude_categories=args.exclude_categories,
    )
    df.to_csv(args.output, index=False)
    print(f"Wrote {len(df)} rows to {args.output}")
    if not df.empty:
        print(f"Rows needing review: {int(df['needs_review'].sum())}")


if __name__ == "__main__":
    main()
