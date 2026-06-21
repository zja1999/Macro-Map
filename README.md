# Macro Map

A Python/Streamlit prototype for selecting an area on a map, finding unique fast-food chains in that area, and preparing nutrition/menu macro data for later joining.

## What works now

1. **Map Chain Finder**
   - Starts on a Texas-wide map view.
   - Draw a rectangle or polygon on the map.
   - Query OpenStreetMap/Overpass for `amenity=fast_food` locations inside the selected bounding box.
   - Filter the results back to your drawn shape.
   - Show a unique list of chains and location counts.
   - Color-code each chain name with red/green text by whether nutrition data is on file.

2. **Nutrition file library**
   - Put one real CSV per chain in `data/nutrition/`.
   - Example real files:
     - `data/nutrition/chick_fil_a.csv`
     - `data/nutrition/mcdonalds.csv`
     - `data/nutrition/taco_bell.csv`
   - The app loads real CSVs in that folder for matching/coverage, but the macro table stays hidden until you draw/search an area.
   - After a selection, **Menu macros for selected area** shows only rows for chains found in that area.
   - Template/example files are ignored:
     - files starting with `_` or `.`
     - files ending in `.example.csv` or `.template.csv`

3. **Nutrition Parser**
   - Upload a `.pdf` or `.txt` nutrition file.
   - Extract text from text-based PDFs with `pdfplumber`.
   - Parse nutrition rows into a stable CSV schema:
     - `chain`
     - `category`
     - `item_name`
     - `serving_size`
     - `calories`
     - `protein_g`
     - `carbs_g`
     - `fat_g`
     - `sat_fat_g`
     - `trans_fat_g`
     - `cholesterol_mg`
     - `sodium_mg`
     - `fiber_g`
     - `sugar_g`
     - `needs_review`
     - `review_reason`
     - `raw_text`

The parser is intentionally review-first. Fast-food nutrition PDFs do not have one universal format, so suspicious rows are kept but marked with `needs_review=True`.

## Install and run

### Command Prompt

```cmd
cd C:\Users\Zach\Downloads\menu_macro_map
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
streamlit run app.py
```

### PowerShell without activation

```powershell
cd C:\Users\Zach\Downloads\menu_macro_map
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```


## Preloaded test nutrition files

This version includes a few small **sample/test-only** nutrition CSVs so you can verify that the map chain list turns green when data is available and that the selected-area macro table filters correctly. Included test files:

```text
data/nutrition/chick_fil_a.csv
data/nutrition/mcdonalds.csv
data/nutrition/taco_bell.csv
data/nutrition/whataburger.csv
data/nutrition/subway.csv
data/nutrition/chipotle.csv
data/nutrition/wendys.csv
data/nutrition/sonic.csv
data/nutrition/burger_king.csv
```

These rows are placeholders and include `is_sample_data=TRUE`. Replace or delete them when you add real nutrition data.

## Adding nutrition data manually

Place finished nutrition CSV files in:

```text
data/nutrition/
```

Recommended minimum columns:

```text
chain,category,item_name,serving_size,calories,protein_g,carbs_g,fat_g,sodium_mg,fiber_g,sugar_g
```

The app reads the `chain` column inside the CSV when available, and also uses the filename as a match key. That means `data/nutrition/chick_fil_a.csv` matches a map chain displayed as `Chick-fil-A`.

Example:

```text
data/nutrition/chick_fil_a.csv
```

If the map finds `Chick-fil-A`, that row turns green because the nutrition file is on file. Chains without matching nutrition data stay red.

## Nutrition parser command-line usage

Parse a text-based PDF directly into the nutrition library folder:

```cmd
python scripts\parse_nutrition.py nutrition.pdf --chain "Chick-fil-A" --output data\nutrition\chick_fil_a.csv
```

Parse copied PDF text from a `.txt` file:

```cmd
python scripts\parse_nutrition.py nutrition_text.txt --chain "Chick-fil-A" --output data\nutrition\chick_fil_a.csv
```

Force expected nutrient count:

```cmd
python scripts\parse_nutrition.py nutrition.pdf --chain "Example Chain" --expected-nutrients 10 --output data\nutrition\example_chain.csv
```

Use `9` when the row order is:

```text
serving size, calories, fat, saturated fat, trans fat, cholesterol, sodium, carbs, fiber, sugar
```

Use `10` when there is also a protein value after sugar.

## Important parser limits

A single parser cannot perfectly handle every restaurant nutrition PDF because they vary by:

- table layout
- column order
- item names split across lines
- repeated page headers
- missing columns
- scanned/image-only PDFs
- nutrition values glued to item names during text extraction

Best practical workflow:

1. Try official CSV/Excel/JSON data first when available.
2. Use this parser for text-based PDFs.
3. Review rows marked `needs_review=True`.
4. Save approved chain CSVs into `data/nutrition/`.
5. Add chain-specific cleanup rules in `src/nutrition_parser.py` as you encounter new formats.
6. For scanned PDFs, run OCR before parsing.

## Project structure

```text
menu_macro_map/
  app.py
  requirements.txt
  README.md
  scripts/
    parse_nutrition.py
  src/
    chain_normalizer.py
    geojson_utils.py
    nutrition_parser.py
    nutrition_store.py
    osm_overpass.py
  data/
    menu_items.example.csv
    nutrition/
      README.md
      _template.csv
```
