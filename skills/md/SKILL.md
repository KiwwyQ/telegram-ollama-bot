# Markdown (MD) Skill

Markdown files are plain text with formatting markers. No special library is required for basic reading/writing.

## Reading

```python
with open("file.md", "r", encoding="utf-8") as f:
    text = f.read()
print(text)
```

## Writing

```python
content = "# Title\n\nParagraph with **bold** and *italic*.\n"
with open("file.md", "w", encoding="utf-8") as f:
    f.write(content)
```

## Notes

- Markdown is just text. You can generate it directly.
- If you need to convert Markdown to other formats (HTML, PDF), use Python Eval with appropriate libraries (`markdown`, `weasyprint`, etc.).
- Keep lines under ~120 chars for readability in Telegram.
