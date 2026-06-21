# Contributing nutrition data

Macro Map stores nutrition data as one CSV file per chain in:

```text
data/nutrition/
```

## Preferred workflow

1. Create a new branch from `main`.
2. Add or update one chain CSV in `data/nutrition/`.
3. Keep the filename as a lowercase slug, for example:
   - `chick_fil_a.csv`
   - `mcdonalds.csv`
   - `taco_bell.csv`
4. Make sure the CSV has the required columns below.
5. Open a pull request and describe the data source used.

## Required minimum columns

```text
chain,category,item_name,serving_size,calories,protein_g,carbs_g,fat_g,sodium_mg,fiber_g,sugar_g
```

## Recommended full columns

```text
chain,category,item_name,calories,protein_g,carbs_g,fat_g,serving_size,serving_g,sodium_mg,fiber_g,sugar_g,sat_fat_g,trans_fat_g,cholesterol_mg,is_sample_data,source_note
```

## Data rules

- Use one row per menu item.
- Put the same chain name in every row of a chain file.
- Use numbers only in macro columns. Do not include units inside numeric cells.
- Use `serving_size` for the display value, such as `153g` or `1 sandwich`.
- Use `serving_g` only when the serving size is available in grams.
- Set `is_sample_data` to `FALSE` for real nutrition data.
- Include a short `source_note`, such as `Official Chick-fil-A nutrition PDF copied June 2026`.
- Do not include catering trays, drinks, sauces, or desserts unless the project owner asks for those categories.

## Review checklist

Before submitting a pull request, verify:

- The file is in `data/nutrition/`.
- The filename matches the chain slug.
- There are no duplicate `item_name` rows unless the serving size or category is different.
- Calories, protein, carbs, and fat are filled where the source provides them.
- The source is official or clearly noted.
