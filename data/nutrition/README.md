# Nutrition CSV files

Put one real CSV per chain in this folder.

Examples:

```text
chick_fil_a.csv
mcdonalds.csv
taco_bell.csv
```

Filenames are normalized before matching, so `chick_fil_a.csv` matches a map result displayed as `Chick-fil-A`.

The app also reads the `chain` column inside each CSV when available, but filename matching is included as a fallback so punctuation and underscores do not break coverage.

Template files beginning with `_` are ignored.
