# JSON Skill

JSON is the standard structured text format. Use the built-in `json` module.

## Reading

```python
import json

with open("data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# data is now a dict/list/etc.
print(json.dumps(data, indent=2))
```

## Writing

```python
import json

data = {
    "name": "example",
    "values": [1, 2, 3],
    "nested": {"key": "value"}
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
```

## Pretty printing

```python
print(json.dumps(data, indent=2))
```

## Notes

- `json.dumps(..., ensure_ascii=False)` preserves Unicode characters.
- For very large JSON, consider `jsonlines` (one JSON object per line) for streaming.
- The standard library `json` module cannot handle datetime objects by default. Convert them to strings first.
