from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .chain_normalizer import chain_key, normalize_chain_name

# Anchor the nutrition folder to the project directory, not whatever directory
# Streamlit/Python was launched from. This prevents false "missing" matches when
# running `streamlit run C:\\...\\app.py` from somewhere else.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
NUTRITION_DIR = PROJECT_ROOT / "data" / "nutrition"
IGNORED_FILE_PREFIXES = ("_", ".")
IGNORED_FILE_SUFFIXES = (".example.csv", ".template.csv")

# Stable columns the rest of the app expects. Extra columns in nutrition CSVs are kept too.
STANDARD_NUTRITION_COLUMNS = [
    "chain",
    "category",
    "item_name",
    "serving_size",
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
]


@dataclass(frozen=True)
class NutritionLibrary:
    """Loaded menu/macro CSV files from data/nutrition."""

    items: pd.DataFrame
    files: pd.DataFrame
    covered_chain_keys: set[str]


def nutrition_dir() -> Path:
    """Return the nutrition data directory, creating it if needed."""
    NUTRITION_DIR.mkdir(parents=True, exist_ok=True)
    return NUTRITION_DIR


def _is_real_nutrition_file(path: Path) -> bool:
    """Ignore templates, examples, hidden files, and non-CSV files."""
    if path.suffix.lower() != ".csv":
        return False
    if path.name.startswith(IGNORED_FILE_PREFIXES):
        return False
    return not any(path.name.lower().endswith(suffix) for suffix in IGNORED_FILE_SUFFIXES)


def _nutrition_files(base: Path) -> list[Path]:
    """Return real nutrition CSV files, case-insensitive on the extension."""
    if not base.exists():
        return []
    return sorted(path for path in base.iterdir() if path.is_file() and _is_real_nutrition_file(path))


def _chain_from_filename(path: Path) -> str:
    """Best-effort fallback chain name from a nutrition filename."""
    stem = path.stem
    for suffix in ("_nutrition_parsed", "_nutrition", "_macros", "_menu", "_items"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
    candidate = stem.replace("_", " ").replace("-", " ")
    return normalize_chain_name(candidate) or candidate.title()


def _coerce_expected_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure standard columns exist while preserving any chain-specific extras."""
    df = df.copy()
    for col in STANDARD_NUTRITION_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    # Put the core macro columns first, then preserve additional details after them.
    ordered = STANDARD_NUTRITION_COLUMNS + [col for col in df.columns if col not in STANDARD_NUTRITION_COLUMNS]
    return df[ordered]


def _first_usable_chain(df: pd.DataFrame, fallback: str) -> str:
    """Read the chain name from the CSV when possible, otherwise use filename fallback."""
    if "chain" in df.columns:
        chains = df["chain"].dropna().astype(str).str.strip()
        chains = chains[chains != ""]
        if not chains.empty:
            normalized = normalize_chain_name(chains.iloc[0])
            return normalized or chains.iloc[0]
    return fallback


def load_nutrition_library(directory: Path | str | None = None) -> NutritionLibrary:
    """Load and concatenate one-CSV-per-chain nutrition files.

    Put real nutrition files in data/nutrition/. Recommended filename style:
    chick_fil_a.csv, taco_bell.csv, mcdonalds.csv, etc.

    Matching is intentionally forgiving. A file named `chick_fil_a.csv` matches
    the map's `Chick-fil-A` result even if the CSV's internal `chain` column is
    missing or imperfect. The CSV `chain` column is still used for display when
    it is clean.
    """
    base = Path(directory) if directory is not None else nutrition_dir()
    base.mkdir(parents=True, exist_ok=True)

    item_frames: list[pd.DataFrame] = []
    file_rows: list[dict[str, object]] = []
    covered: set[str] = set()

    for path in _nutrition_files(base):
        fallback_chain = _chain_from_filename(path)
        filename_key = chain_key(fallback_chain)

        try:
            df = pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001 - display the file-specific error in-app
            file_rows.append(
                {
                    "chain": fallback_chain,
                    "chain_key": filename_key,
                    "filename_chain_key": filename_key,
                    "match_keys": filename_key,
                    "file": str(path),
                    "rows": 0,
                    "loaded": False,
                    "error": str(exc),
                }
            )
            continue

        chain = _first_usable_chain(df, fallback=fallback_chain)
        normalized_chain = normalize_chain_name(chain) or chain
        csv_key = chain_key(normalized_chain)

        # Mark both the filename slug and the CSV chain column as covered. This
        # prevents `chick_fil_a.csv` from missing `Chick-fil-A` just because the
        # visible punctuation differs or the CSV has a weird chain value.
        match_keys = {key for key in (csv_key, filename_key) if key}
        covered.update(match_keys)

        df = _coerce_expected_columns(df)
        df["chain"] = normalized_chain
        df["chain_key"] = csv_key or filename_key
        df["source_file"] = str(path)

        item_frames.append(df)
        file_rows.append(
            {
                "chain": normalized_chain,
                "chain_key": csv_key,
                "filename_chain_key": filename_key,
                "match_keys": ", ".join(sorted(match_keys)),
                "file": str(path),
                "rows": int(len(df)),
                "loaded": True,
                "error": "",
            }
        )

    items = (
        pd.concat(item_frames, ignore_index=True)
        if item_frames
        else pd.DataFrame(columns=STANDARD_NUTRITION_COLUMNS + ["chain_key", "source_file"])
    )
    files = pd.DataFrame(
        file_rows,
        columns=["chain", "chain_key", "filename_chain_key", "match_keys", "file", "rows", "loaded", "error"],
    )
    return NutritionLibrary(items=items, files=files, covered_chain_keys=covered)


def annotate_chains_with_nutrition(chains: pd.DataFrame, library: NutritionLibrary) -> pd.DataFrame:
    """Add nutrition coverage columns to the unique-chain results."""
    if chains.empty:
        return pd.DataFrame(columns=["chain", "locations", "nutrition_status", "nutrition_on_file"])

    result = chains.copy()
    result["chain_key"] = result["chain"].map(chain_key)
    result["nutrition_on_file"] = result["chain_key"].isin(library.covered_chain_keys)
    result["nutrition_status"] = result["nutrition_on_file"].map({True: "On file", False: "Missing"})

    display_cols = ["chain", "locations", "nutrition_status", "nutrition_on_file", "chain_key"]
    return result[[col for col in display_cols if col in result.columns]]


def _selected_chain_keys(chains: pd.DataFrame) -> set[str]:
    """Return normalized chain keys from the current map selection."""
    if chains.empty or "chain" not in chains.columns:
        return set()
    return {key for key in chains["chain"].map(chain_key).dropna() if key}


def filter_items_for_chains(items: pd.DataFrame, chains: pd.DataFrame) -> pd.DataFrame:
    """Return nutrition rows only for chains in the current map selection.

    Important: no map selection means no nutrition rows. The app should not show
    the whole nutrition library before the user draws/searches an area.
    """
    if items.empty:
        return items

    selected_keys = _selected_chain_keys(chains)
    if not selected_keys or "chain_key" not in items.columns:
        return items.iloc[0:0].copy()

    return items[items["chain_key"].isin(selected_keys)].reset_index(drop=True)


def filter_files_for_chains(files: pd.DataFrame, chains: pd.DataFrame) -> pd.DataFrame:
    """Return nutrition-file metadata only for chains in the current map selection."""
    if files.empty:
        return files

    selected_keys = _selected_chain_keys(chains)
    if not selected_keys:
        return files.iloc[0:0].copy()

    key_cols = [col for col in ("chain_key", "filename_chain_key") if col in files.columns]
    if not key_cols:
        return files.iloc[0:0].copy()

    mask = pd.Series(False, index=files.index)
    for col in key_cols:
        mask = mask | files[col].isin(selected_keys)

    return files[mask].reset_index(drop=True)
