# XML Skill

XML for structured documents. Use the built-in `xml.etree.ElementTree` for basic tasks, or `lxml` for advanced features.

## Reading

```python
import xml.etree.ElementTree as ET

tree = ET.parse("file.xml")
root = tree.getroot()

for child in root:
    print(child.tag, child.text)
```

## Writing

```python
import xml.etree.ElementTree as ET

root = ET.Element("root")
item = ET.SubElement(root, "item")
item.set("id", "1")
item.text = "value"

tree = ET.ElementTree(root)
tree.write("output.xml", encoding="utf-8", xml_declaration=True)
```

## Pretty printing (requires lxml)

```python
from lxml import etree

xml_str = etree.tostring(root, pretty_print=True, encoding="unicode")
print(xml_str)
```

## Notes

- Standard library `xml.etree.ElementTree` is sufficient for simple XML.
- `lxml` supports XPath, XSD validation, and HTML parsing. Install with `# REQUIRE: lxml`.
- For RSS/Atom feeds, use `feedparser` (`# REQUIRE: feedparser`).
