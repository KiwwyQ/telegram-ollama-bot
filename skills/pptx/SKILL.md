# PPTX Skill

PPTX files (Microsoft PowerPoint). Use `python-pptx` for reading and writing.

## Writing

```python
# REQUIRE: python-pptx
from pptx import Presentation
from pptx.util import Inches

prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[0])
slide.shapes.title.text = "Title"
slide.shapes.placeholders[1].text = "Subtitle"

slide2 = prs.slides.add_slide(prs.slide_layouts[1])
slide2.shapes.title.text = "Slide 2"
slide2.shapes.placeholders[1].text = "Bullet 1\nBullet 2"

prs.save("output.pptx")
print("Created output.pptx")
```

## Reading

```python
# REQUIRE: python-pptx
from pptx import Presentation

prs = Presentation("input.pptx")
for slide in prs.slides:
    for shape in slide.shapes:
        if hasattr(shape, "text"):
            print(shape.text)
```

## Notes

- `python-pptx` supports text, images, charts, and basic shapes.
- Complex animations and transitions are not supported for reading.
- For simple text extraction, the standard library `zipfile` + XML parsing can be used as a fallback.
