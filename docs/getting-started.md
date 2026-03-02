# Getting Started

## Installation

openbasement is not yet published on PyPI. Install directly from GitHub:

```bash
pip install openbasement @ git+https://github.com/openstage-eu/openbasement.git
```

## Getting RDF data

openbasement operates on in-memory `rdflib.Graph` objects. It does no network I/O. The caller is responsible for loading the graph.

The recommended Cellar format is the **RDF tree notice**, which inlines related entities (events, documents) into a single RDF/XML response:

```bash
curl -L \
  -H 'Accept: application/rdf+xml;notice=tree' \
  'https://publications.europa.eu/resource/procedure/2019_2026'
```

This returns standard RDF/XML that rdflib parses natively.

## First extraction

```python
from rdflib import Graph
from openbasement import extract

# Load an RDF tree notice
g = Graph()
g.parse("2019_2026.rdf", format="xml")

# Extract using the built-in EU procedure template
results = extract(g, template="eu_procedure")

procedure = results[0]
procedure["_uri"]           # "http://publications.europa.eu/resource/..."
procedure["title"]          # {"en": "Regulation on ...", "fr": "..."}
procedure["date"]           # "2019-12-11"
procedure["events"]         # List of nested event dicts
procedure["_raw_triples"]   # Triples not consumed by the template
```

Fields and relations are top-level keys. Metadata keys are prefixed with `_` (`_uri`, `_rdf_types`, `_raw_triples`, `_same_as`).

## Output shape

```python
{
    "_uri": "http://publications.europa.eu/resource/procedure/2019_2026",
    "_rdf_types": ["http://.../cdm#procedure_codecision"],
    "_same_as": [                                    # present when sameAs merge found aliases
        "http://.../resource/cellar/abc123...",
        "http://.../resource/pegase/1042898",
    ],

    # Scalar fields
    "date": "2019-12-11",
    "reference": "2019_2026",

    # Multilingual fields: language-keyed dicts
    "title": {"en": "Regulation on ...", "fr": "Reglement sur ...", "de": "Verordnung ..."},

    # Wildcard fields: predicate-keyed dicts
    "dates": {"date": "2019-12-11", "date_adopted": "2021-06-09"},

    # Relations: lists of nested entity dicts
    "events": [
        {
            "_uri": "http://.../procedure-event/...",
            "date": "2020-01-29",
            "type_code": "...",
            "works": [...],
            "_raw_triples": [...]
        },
    ],

    # Unconsumed triples (from all alias subjects, excludes owl:sameAs triples)
    "_raw_triples": [("subj", "pred", "obj"), ...]
}
```

All RDF information is preserved. Fields the template recognizes become structured data. Everything else lands in `_raw_triples`.

## Extracting specific entities

```python
# Extract only events (not the parent procedure)
events = extract(g, template="eu_procedure", entity="event")

# Extract only documents
docs = extract(g, template="eu_procedure", entity="document")
```

## owl:sameAs merging

By default, openbasement merges entities that share `owl:sameAs` links. CDM data uses multiple URIs for the same entity (pegase IDs, procedure URIs, cellar UUIDs), and merging produces one rich output entity instead of multiple sparse duplicates.

The `_same_as` metadata field lists all alias URIs. The `_uri` field contains the canonical URI (preferring `resource/procedure/` over internal identifiers).

To disable merging (e.g., for debugging or non-CDM data):

```python
# Per call
results = extract(g, template="eu_procedure", merge_same_as=False)

# Or in a custom template (same_as_merge defaults to true)
template = {
    "version": "1",
    "same_as_merge": False,
    "prefixes": {...},
    "entities": {...},
}
```

## Using custom templates

```python
# From a YAML file path
results = extract(g, template="/path/to/custom.yaml")

# From a dict
results = extract(g, template={"version": "1", "prefixes": {...}, "entities": {...}})
```

See [Templates](templates.md) for the full template format.

## Listing built-in templates

```python
from openbasement import list_builtin_templates

list_builtin_templates()  # ["eu_document", "eu_procedure"]
```

## Transforms

Templates can apply named transforms to extracted values. Two built-in transforms are available:

- `year_from_date`: Extracts the year from a date string (`"2019-12-11"` -> `"2019"`)
- `uri_local_name`: Extracts the local name from a URI (`"http://.../concept/COD"` -> `"COD"`)

Custom transforms are callables passed at extraction time:

```python
results = extract(
    g,
    template="eu_procedure",
    transforms={"my_transform": lambda v: v.upper()},
)
```

Templates reference transforms by name in the `transform` field option.

## Auditing templates

The `audit` function checks how well a template covers the predicates actually present in a graph:

```python
from openbasement import audit, load_template

template = load_template("eu_procedure")
report = audit(g, template)

report["summary"]["coverage"]             # 0.85 (85% of triples covered)
report["entities"]["event"]["uncovered"]  # Predicates not in template
report["entities"]["event"]["missing"]    # Template predicates not in graph
```

