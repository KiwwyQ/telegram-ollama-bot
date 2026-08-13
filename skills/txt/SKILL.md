# TXT Skill

Plain text files. The simplest format. No special libraries needed.

## Reading

```python
with open("file.txt", "r", encoding="utf-8") as f:
    text = f.read()
print(text)
```

## Writing

```python
content = "Hello, world!"
with open("file.txt", "w", encoding="utf-8") as f:
    f.write(content)
```

## Appending

```python
with open("file.txt", "a", encoding="utf-8") as f:
    f.write("\nNew line")
```

## Notes

- Always specify `encoding="utf-8"` to avoid platform-dependent defaults.
- Use `\n` for newlines; Python's text mode handles conversion on Windows.
- For very large files, read in chunks with `f.read(4096)`.
