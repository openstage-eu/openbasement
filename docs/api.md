# API Reference

## Public functions

::: openbasement.extract
    options:
      show_source: false

::: openbasement.load_template
    options:
      show_source: false

::: openbasement.list_builtin_templates
    options:
      show_source: false

::: openbasement.audit
    options:
      show_source: false

## Built-in transforms

| Name | Description |
|:-----|:------------|
| `year_from_date` | Extracts the first 4 characters (year) from a date string, e.g. `"2023-07-05"` -> `"2023"` |
| `uri_local_name` | Extracts the local name from a URI after `#` or `/`, e.g. `"http://example.org/foo#Bar"` -> `"Bar"` |

::: openbasement.transforms.apply_transform
    options:
      show_source: false
