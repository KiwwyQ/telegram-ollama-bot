# XLSX Skill

XLSX files (Microsoft Excel). Use `openpyxl` for reading and writing.

## Writing

```python
# REQUIRE: openpyxl
from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.title = "Sheet1"
ws["A1"] = "Name"
ws["B1"] = "Score"
ws.append(["Alice", 10])
ws.append(["Bob", 20])

wb.save("output.xlsx")
print("Created output.xlsx")
```

## Reading

```python
# REQUIRE: openpyxl
from openpyxl import load_workbook

wb = load_workbook("input.xlsx")
ws = wb.active

for row in ws.iter_rows(values_only=True):
    print(row)
```

## Notes

- `openpyxl` supports formulas, styles, charts, and merged cells.
- For very large Excel files, `pandas` (`# REQUIRE: pandas`) with `read_excel` is more convenient but heavier.
- CSV is often a simpler alternative for tabular data.
