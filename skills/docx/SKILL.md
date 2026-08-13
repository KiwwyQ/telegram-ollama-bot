# DOCX Skill

DOCX files (Microsoft Word). Use `python-docx` for reading and writing.

## Writing

```python
# REQUIRE: python-docx
from docx import Document

doc = Document()
doc.add_heading("Title", level=1)
doc.add_paragraph("Hello, world!")
doc.add_paragraph("Second paragraph").bold = True

doc.save("output.docx")
print("Created output.docx")
```

## Reading

```python
# REQUIRE: python-docx
from docx import Document

doc = Document("input.docx")
for para in doc.paragraphs:
    print(para.text)
```

## Notes

- `python-docx` does not support reading all Word features (e.g., tracked changes, complex tables).
- Tables:
  ```python
  for table in doc.tables:
      for row in table.rows:
          print([cell.text for cell in row.cells])
  ```
- Images embedded in DOCX are in the `word/media/` folder inside the ZIP. Extracting them requires manual ZIP handling.
