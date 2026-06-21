# Macro Map

A Python/Streamlit prototype for selecting an area on a map, finding unique fast-food chains in that area, and matching those chains to local nutrition/menu macro CSV files.

The app is currently optimized for Texas-area testing, but most of the code is written so the map/search workflow can grow beyond the prototype.

## What works now

1. **Map Chain Finder**
   - Starts on a Texas-wide map view.
   - Draw a rectangle or polygon on the map.
   - Query OpenStreetMap/Overpass for `amenity=fast_food` locations inside the selected bounding box.
   - Filter the results back to your drawn shape.
   - Show a unique list of chains and location counts.
   - Color-code each chain name with red/green text by whether nutrition data is on file.
   - Store-level location tables and map markers are intentionally hidden to reduce clutter.

2. **Selected-chain menu tabs**
   - After a selection, the selected-chain area uses three tabs:
     - **Menu items**: the main table of menu macros for chains found in the selected area.
     - **Item suggestions**: single-item recommendations by protein goal and calorie limit.
     - **Macro counter**: a prototype daily calorie/macro tracker.

3. **Nutrition file library**
   - Put one real CSV per chain in `data/nutrition/`.
   - The app loads real CSVs in that folder for matching/coverage, but the macro table stays hidden until you draw/search an area.
   - After a selection, **Menu items** shows only rows for chains found in that area.
   - Template/example files are ignored:
     - files starting with `_` or `.`
     - files ending in `.example.csv` or `.template.csv`

4. **Single item recommendations**
   - After selecting an area with matching nutrition data, the app can rank individual menu items by protein goal and calorie limit.
   - The recommender intentionally avoids combo-building; it only recommends single menu items.

5. **Macro counter prototype**
   - Users can check menu items from the selected-chain results and add them to a running macro counter.
   - The counter tracks calories, protein, carbs, fat, sodium, fiber, and sugar.
   - Users can set an in-session profile name and macro targets.
   - Users can download/upload a JSON profile/day file as a temporary persistence workaround.

6. **Missing nutrition requests**
   - Missing chains show a request link in the unique-chain table.
   - The **Request missing nutrition data** panel lets a user pick a missing chain and open a pre-filled GitHub issue.
   - Users without GitHub can copy a prepared request message.
   - Optional email requests can be enabled with Streamlit secrets or an environment variable.

7. **Nutrition Parser**
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

## Optional request email setup

GitHub issues are the default request path. To also show an **Email request** button for users without GitHub, configure one of these:

### Streamlit secrets

```toml
# .streamlit/secrets.toml
contact_email = "you@example.com"
```

### Environment variable

```cmd
set MACRO_MAP_CONTACT_EMAIL=you@example.com
streamlit run app.py
```

On Streamlit Community Cloud, add `contact_email` in the app's Secrets settings.

## User profiles and persistence

The current macro counter stores profile data in Streamlit session state. That means it works during the current browser session, but it is not a true account system.

The temporary workaround is the **Download profile/day JSON** and **Load profile/day JSON** flow in the Macro counter tab.

For real user profiles where someone can come back later and see calories/macros remaining, the app needs a persistent backend. Good future options:

1. **Supabase or hosted Postgres**
   - Best fit for real user accounts and macro logs.
   - Store database credentials in Streamlit secrets, not GitHub.

2. **Firebase**
   - Good if you want user auth plus a document-style database.

3. **Google Sheets or Airtable**
   - Easier to prototype, less ideal for private user health data.

4. **Local SQLite**
   - Fine for local testing, but not reliable for Streamlit Community Cloud persistence.

Do not use GitHub as the user-data backend. GitHub should store code and nutrition CSVs, not private user macro logs.

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

## Quality checks

Install test dependencies:

```cmd
pip install -r requirements.txt -r requirements-dev.txt
```

Run tests:

```cmd
pytest -q
```

Current tests cover:

- chain alias normalization
- nutrition CSV loading/filtering
- single-item recommendation ranking

A GitHub Actions workflow also runs the focused test suite on pushes.

## Project structure

```text
menu_macro_map/
  app.py
  requirements.txt
  requirements-dev.txt
  README.md
  .github/
    ISSUE_TEMPLATE/
      nutrition-request.md
    workflows/
      ci.yml
  scripts/
    parse_nutrition.py
  src/
    app_main.py
    app_main_tabs.py
    chain_normalizer.py
    geojson_utils.py
    macro_counter.py
    nutrition_parser.py
    nutrition_store.py
    osm_overpass.py
    recommender.py
    ui_helpers.py
  tests/
    test_chain_normalizer.py
    test_nutrition_store.py
    test_recommender.py
  data/
    menu_items.example.csv
    nutrition/
      README.md
      _template.csv
```
