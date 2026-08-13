# PDF Skill

This skill explains how to work with PDF files using the Python tools available in this bot.

## Overview

The bot can read, create, and manipulate PDF files through Python Eval. The execution environment has the user's workspace as the working directory.

## Useful Packages

For PDF manipulation, commonly used packages include:
- `pypdf` or `PyPDF2` - reading and extracting text from PDFs
- `reportlab` - creating new PDFs
- `fpdf2` - simpler PDF creation

## Reading PDFs

To extract text from a PDF:
1. Upload the PDF to your workspace using `[WS_WRITE:document.pdf]` with the binary content, or place it in your workspace manually.
2. Use Python Eval with `[EVAL]` to read it:
   ```python
   # REQUIRE: pypdf
   from pypdf import PdfReader
   reader = PdfReader("document.pdf")
   text = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
   print(text[:2000])
   ```

## Creating PDFs

To generate a PDF:
```python
# REQUIRE: fpdf2
from fpdf import FPDF
pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.cell(200, 10, txt="Hello World", ln=1)
pdf.output("output.pdf")
print("Created output.pdf")
```

## Limitations

- The eval environment may not have all PDF packages pre-installed. Use `# REQUIRE: package` to install them.
- Large PDFs may exceed output limits. Process in chunks if needed.
- Binary PDF data cannot be passed directly through the text protocol. Save to workspace files instead.

## Workflow

1. List workspace files with `[WS_LIST]`
2. Read/write files with `[WS_READ:path]` / `[WS_WRITE:path]`
3. Execute Python with `[EVAL]` to process PDFs
4. Send results back as text or files
