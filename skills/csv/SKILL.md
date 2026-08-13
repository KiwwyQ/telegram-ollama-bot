# CSV Skill

CSV files for tabular data. Use the built-in `csv` module.

## Reading

```python
import csv

with open("data.csv", "r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

for row in rows:
    print(row["column_name"], row["another_column"])
```

## Writing

```python
import csv

rows = [
    {"name": "Alice", "score": "10"},
    {"name": "Bob", "score": "20"},
]

with open("output.csv", "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "score"])
    writer.writeheader()
    writer.writerows(rows)
```

## Notes

- Always open CSV with `newline=""` to avoid blank lines on Windows.
- Use `csv.QUOTE_MINIMAL` (default) to quote fields only when needed.
- For complex tabular data (dates, numbers), consider pandas if available.
