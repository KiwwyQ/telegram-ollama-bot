# PDF Skill

PDF files for documents and reports. Use `pypdf` for reading and `fpdf2` or `reportlab` for writing.

## Reading

```python
# REQUIRE: pypdf
from pypdf import PdfReader

reader = PdfReader("input.pdf")
for i, page in enumerate(reader.pages):
    text = page.extract_text()
    if text:
        print(f"--- Page {i+1} ---")
        print(text[:2000])
```

## Writing

```python
# REQUIRE: fpdf2
from fpdf import FPDF

pdf = FPDF()
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.cell(200, 10, txt="Hello, world!", ln=1)
pdf.output("output.pdf")
print("Created output.pdf")
```

## Advanced writing (reportlab)

```python
# REQUIRE: reportlab
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

c = canvas.Canvas("output.pdf", pagesize=letter)
c.drawString(100, 700, "Hello, world!")
c.save()
```

## Notes

- PDF is a complex format. Text extraction is approximate; layout may be lost.
- For scanned PDFs, use OCR libraries like `pytesseract` (`# REQUIRE: pytesseract`), but this requires Tesseract installed on the system.
- `pypdf` cannot edit existing PDFs reliably. Create new ones instead.
- Large PDFs may exceed output limits. Extract text in chunks.
