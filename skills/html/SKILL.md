# HTML Skill

HTML files for web pages. Can be generated as plain text or with helper libraries.

## Writing (simple)

```python
html = """<!DOCTYPE html>
<html>
<head><title>Page</title></head>
<body>
<h1>Hello</h1>
<p>Paragraph.</p>
</body>
</html>"""

with open("page.html", "w", encoding="utf-8") as f:
    f.write(html)
```

## Writing (with library)

```python
from html.parser import HTMLParser

# For generating HTML programmatically, consider:
# # REQUIRE: dominate
from dominate.tags import *

with open("page.html", "w", encoding="utf-8") as f:
    with doc(title="Page") as d:
        with div():
            h1("Hello")
            p("Generated with dominate")
    f.write(d.render())
```

## Reading

```python
from html.parser import HTMLParser

class MyParser(HTMLParser):
    def handle_data(self, data):
        print(data)

with open("page.html", "r", encoding="utf-8") as f:
    html = f.read()
    parser = MyParser()
    parser.feed(html)
```

## Notes

- For complex HTML generation, `dominate` or `yattag` are lightweight options.
- For parsing/scraping, `beautifulsoup4` (`# REQUIRE: beautifulsoup4`) is the standard.
- Always use `encoding="utf-8"` when writing HTML files.
