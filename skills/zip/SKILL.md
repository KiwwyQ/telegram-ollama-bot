# ZIP Skill

ZIP archives for compressing and extracting files. Use the built-in `zipfile` module.

## Creating a ZIP

```python
import zipfile
import os

with zipfile.ZipFile("output.zip", "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write("file1.txt")
    zf.write("file2.txt", arcname="renamed.txt")
    # Add string content directly
    zf.writestr("notes.txt", "Hello from string")
```

## Extracting a ZIP

```python
import zipfile

with zipfile.ZipFile("input.zip", "r") as zf:
    zf.extractall("extracted/")
    # Or list contents
    for info in zf.infolist():
        print(info.filename, info.file_size)
```

## Reading a single file inside a ZIP

```python
import zipfile

with zipfile.ZipFile("input.zip", "r") as zf:
    content = zf.read("file.txt").decode("utf-8")
    print(content)
```

## Notes

- ZIP files are also used by DOCX, XLSX, PPTX, and EPUB formats. You can inspect them with `zipfile`.
- `arcname` controls the path inside the ZIP.
- Use `ZIP_DEFLATED` for compression; `ZIP_STORED` for no compression.
