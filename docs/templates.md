# Templates

Templates are YAML files that declare how to extract structured data from an RDF graph. All CDM-specific knowledge lives in templates, not in Python code.

## Structure

A template has these top-level sections:

```yaml
version: "1"

prefixes:
  cdm: "http://publications.europa.eu/ontology/cdm#"
  skos: "http://www.w3.org/2004/02/skos/core#"

languages:
  preferred: ["en", "fr", "de"]
  fallback: "any"

same_as_merge: true   # optional, default: true

entities:
  procedure:
    find:
      type: "cdm:procedure_interinstitutional"
      include_subclasses: true

    fields:
      title:
        predicate:
          - "cdm:title"
          - "cdm:dossier_title"
        multilingual: true

    relations:
      events:
        predicate:
          - "cdm:dossier_contains_event_legal"
          - "cdm:dossier_contains_event"
        target_template: "event"
        cardinality: "many"
```

### Prefixes

Maps short prefixes to full namespace URIs. All predicate references in the template use prefixed form (`cdm:title` instead of the full URI).

### Languages

Controls multilingual field resolution.

- `preferred`: Ordered list of preferred language codes. All languages present in the data are returned; the preferred list only affects fallback ordering.
- `fallback`: `"any"` includes untagged literals under the `"_"` key. `"none"` skips them.

### same_as_merge

Controls `owl:sameAs` entity merging (default: `true`). When enabled, instances linked by `owl:sameAs` are grouped into equivalence classes and their triples are merged into a single output entity. The canonical URI is selected by preferring `resource/procedure/` over pegase/cellar URIs. All alias URIs are listed in the `_same_as` metadata field.

Set to `false` if you want each URI extracted as a separate entity (e.g., for debugging or for non-CDM data where `owl:sameAs` has different semantics).

This setting can be overridden per call via `extract(..., merge_same_as=False)`.

### Entities

Each entity has three parts:

- `find`: How to discover instances in the graph (`type` URI, optional `include_subclasses`).
- `fields`: Scalar or multilingual values to extract.
- `relations`: Links to other entities for nested extraction.

The first entity in the template is the root entity, extracted by default.

## Field options

| Option | Default | Description |
|--------|---------|-------------|
| `predicate` | required | Prefixed URI, wildcard (`cdm:date_*`), or list of aliases |
| `multilingual` | `false` | Return a language-keyed dict instead of a scalar |
| `cardinality` | `"one"` | `"one"` (first match wins) or `"many"` (collect all matches) |
| `collect` | `null` | `"dict"` to collect wildcard matches as key-value pairs |
| `direction` | `"forward"` | `"forward"` (subject -> object) or `"inverse"` (object -> subject) |
| `datatype` | `null` | XSD datatype hint (e.g., `"xsd:date"`) |
| `follow` | `null` | One-hop traversal for label resolution (see below) |
| `exclude` | `[]` | Predicates to skip in wildcard matches |
| `required` | `false` | Log a warning if this field is missing |
| `transform` | `null` | Named transform to apply to values (see below) |

## Relation options

| Option | Default | Description |
|--------|---------|-------------|
| `predicate` | required | Prefixed URI or list of aliases |
| `target_template` | `null` | Entity name for recursive nested extraction |
| `cardinality` | `"many"` | `"one"` or `"many"` |
| `direction` | `"forward"` | `"forward"` or `"inverse"` |
| `inverse_predicate` | `[]` | Additional predicates for reverse lookup (object -> subject), with `owl:sameAs` alias expansion |
| `transform` | `null` | Named transform to apply to values |

## Predicate aliasing

CDM encodes the same fact under multiple predicates simultaneously (see [CDM Patterns](cdm-patterns.md)). Templates handle this with predicate lists:

```yaml
date:
  predicate:
    - "cdm:event_legal_date"   # fully qualified
    - "cdm:event_date"         # entity-prefixed
    - "cdm:date"               # short form
  datatype: "xsd:date"
```

For `cardinality: "one"`, the first alias that produces data wins. For `cardinality: "many"`, results from all aliases are merged (deduplicated).

## Wildcard fields

Wildcards match multiple predicates using `fnmatch` patterns:

```yaml
dates:
  predicate: "cdm:date_*"
  collect: "dict"
```

This collects all predicates matching `cdm:date_*` into a dict keyed by local name:

```python
{"date": "2019-12-11", "date_adopted": "2021-06-09"}
```

Use `exclude` to skip specific predicates from a wildcard match:

```yaml
other_properties:
  predicate: "cdm:event_legal_*"
  collect: "dict"
  exclude:
    - "cdm:event_legal_date"
    - "cdm:event_legal_type"
```

## Follow (one-hop traversal)

The `follow` option resolves a value by following one additional predicate. Useful for getting labels from concept URIs:

```yaml
resource_type_label:
  predicate: "cdm:work_has_resource-type"
  follow:
    predicate: "skos:prefLabel"
    multilingual: true
```

This first gets the concept URI via `cdm:work_has_resource-type`, then follows `skos:prefLabel` on that concept to get the human-readable label.

## Inverse predicates on relations

Some entities point back to their parent rather than the parent pointing to them. The `inverse_predicate` option handles this by looking for nodes that point TO the current entity:

```yaml
events:
  predicate:
    - "cdm:dossier_contains_event_legal"
  inverse_predicate:
    - "cdm:event_legal_part_of_dossier"
  target_template: "event"
  cardinality: "many"
```

Inverse predicate lookup automatically expands `owl:sameAs` aliases, handling CDM's multiple-URI-per-entity pattern.

## Transforms

The `transform` option applies a named function to extracted values:

```yaml
procedure_type:
  predicate: "cdm:has_type"
  transform: "uri_local_name"
```

Built-in transforms:

- `year_from_date`: `"2019-12-11"` -> `"2019"`
- `uri_local_name`: `"http://.../concept/COD"` -> `"COD"`

Custom transforms are passed at extraction time:

```python
results = extract(g, template="my_template", transforms={
    "strip_prefix": lambda v: v.removeprefix("http://example.org/"),
})
```

## Writing a custom template

A minimal custom template:

```yaml
version: "1"

prefixes:
  cdm: "http://publications.europa.eu/ontology/cdm#"

languages:
  preferred: ["en"]
  fallback: "any"

entities:
  my_entity:
    find:
      type: "cdm:work"
      include_subclasses: true

    fields:
      title:
        predicate: "cdm:work_title"
        multilingual: true
      date:
        predicate: "cdm:work_date_document"
        datatype: "xsd:date"

    relations: {}
```

Save as `my_template.yaml` and use it:

```python
results = extract(g, template="my_template.yaml")
```

## Built-in templates

| Template | Root entity | Description |
|----------|------------|-------------|
| `eu_procedure` | procedure | Legislative procedures with events and documents |
| `eu_document` | document | Documents, expressions, and manifestations |

Use `list_builtin_templates()` to see the current list.
