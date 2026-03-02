# openbasement

A template-based RDF extraction library for EU legislative data published through the [EU Cellar](https://op.europa.eu/en/web/cellar) repository. It turns RDF graphs into structured Python dictionaries using declarative YAML templates that encode domain knowledge about the [CDM ontology](https://op.europa.eu/en/web/eu-vocabularies/cdm).

## What it does

openbasement solves a specific problem: EU Cellar publishes legislative procedure data as RDF using the Common Data Model (CDM) ontology. This RDF is rich but awkward to work with directly because:

- **Predicate aliasing**: CDM encodes the same fact under multiple predicates simultaneously (e.g., `cdm:date`, `cdm:event_date`, and `cdm:event_legal_date` all represent an event's date). The active predicates vary across data vintages.
- **Multilingual literals**: Most text fields exist in up to 24 EU languages.
- **Deep nesting**: A single procedure contains events, documents, institutions, and concept references spread across hundreds of triples.
- **Subclass hierarchies**: Procedure types use `rdfs:subClassOf` rather than a single `rdf:type`.

openbasement handles all of this through YAML templates. The templates declare which predicates to look for (with aliases), how to resolve languages, and how to traverse relations into nested entities. The Python code is generic; all CDM-specific knowledge lives in the templates.

## Expected input

openbasement operates on in-memory `rdflib.Graph` objects. It does no network I/O.

The recommended way to get complete procedure data from Cellar is the **RDF tree notice** format, which inlines related entities (events, documents) into a single RDF/XML response:

```bash
curl -L \
  -H 'Accept: application/rdf+xml;notice=tree' \
  'https://publications.europa.eu/resource/procedure/2019_2026'
```

This returns standard RDF/XML that rdflib parses natively, with full event metadata (dates, types, institutions) included inline.

## Installation

```bash
pip install openbasement @ git+https://github.com/openstage-eu/openbasement.git
```

## Usage

```python
from rdflib import Graph
from openbasement import extract

# Load an RDF tree notice (caller's responsibility)
g = Graph()
g.parse("2019_2026.rdf", format="xml")

# Extract using the built-in EU procedure template
results = extract(g, template="eu_procedure")

procedure = results[0]
procedure["title"]          # {"en": "Regulation on ...", "fr": "..."}
procedure["date"]           # "2019-12-11"
procedure["events"]         # List of nested event dicts
procedure["_raw_triples"]   # Triples not consumed by the template
```

```python
# Extract only events
events = extract(g, template="eu_procedure", entity="event")

# Use a custom template
results = extract(g, template="/path/to/custom.yaml")
results = extract(g, template={"version": "1", "prefixes": {...}, ...})
```

## Output shape

```python
{
    "_uri": "http://publications.europa.eu/resource/procedure/2019_2026",
    "_rdf_types": ["http://.../cdm#procedure_codecision"],
    "_same_as": ["http://.../resource/cellar/...", "http://.../resource/pegase/..."],
    "title": {"en": "Regulation on ...", "fr": "Reglement sur ...", "de": "Verordnung ..."},
    "date": "2019-12-11",
    "reference": "2019_2026",
    "subject_matters": ["http://.../concept/...", ...],
    "dates": {"date": "2019-12-11", "date_adopted": "2021-06-09"},
    "events": [
        {
            "_uri": "http://.../procedure-event/...",
            "date": "2020-01-29",
            "type_code": "...",
            "works": [...],
            "_raw_triples": [...]
        },
        ...
    ],
    "documents": [...],
    "_raw_triples": [("subj", "pred", "obj"), ...]
}
```

Fields and relations are top-level keys. Metadata keys are prefixed with `_` (`_uri`, `_rdf_types`, `_raw_triples`, `_same_as`). Multilingual fields return language-keyed dicts. All RDF information is preserved: fields the template recognizes become structured data, everything else lands in `_raw_triples`.

By default, entities linked by `owl:sameAs` are merged into one output entity. `_same_as` lists the alias URIs. `_uri` is the canonical URI (preferring `resource/procedure/` over internal identifiers). Disable with `merge_same_as=False` or set `same_as_merge: false` in the template.

## Template format

Templates are YAML files declaring extraction rules:

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
        predicate:                    # Predicate aliasing: try each in order
          - "cdm:title"
          - "cdm:dossier_title"
        multilingual: true

      dates:
        predicate: "cdm:date_*"      # Wildcard: collect all matching predicates
        collect: "dict"

    relations:
      events:
        predicate:
          - "cdm:dossier_contains_event_legal"
          - "cdm:dossier_contains_event"
        target_template: "event"
        cardinality: "many"
```

### Field options

| Option | Default | Description |
|--------|---------|-------------|
| `predicate` | required | Prefixed URI, wildcard (`cdm:date_*`), or list of aliases |
| `multilingual` | false | Apply language preference resolution |
| `cardinality` | "one" | "one" or "many" |
| `collect` | null | "dict" to collect wildcard matches as key-value pairs |
| `direction` | "forward" | "forward" or "inverse" |
| `datatype` | null | XSD datatype hint (e.g., "xsd:date") |
| `follow` | null | One-hop traversal for label resolution |
| `exclude` | [] | Predicates to skip in wildcards |
| `required` | false | Log warning if missing |
| `transform` | null | Named transform to apply (e.g., `"year_from_date"`, `"uri_local_name"`) |

### Relation options

| Option | Default | Description |
|--------|---------|-------------|
| `predicate` | required | Prefixed URI or list of aliases |
| `target_template` | null | Entity name for nested extraction |
| `cardinality` | "many" | "one" or "many" |
| `direction` | "forward" | "forward" or "inverse" |
| `inverse_predicate` | [] | Additional predicates for reverse lookup, with owl:sameAs expansion |
| `transform` | null | Named transform to apply to values |

## Built-in templates

- **eu_procedure**: Procedures, events, and documents from Cellar RDF tree notices
- **eu_document**: Documents, expressions, and manifestations

## What it does not do

- **No network I/O.** The caller loads the RDF graph. openbasement only reads it.
- **No data models.** Output is plain dicts. Validation and domain modeling belong downstream.
- **No graph mutation.** Read-only on the input graph.
- **No storage.** Stateless. Graph in, dicts out.
- **No SPARQL.** Operates on in-memory rdflib graphs, not endpoints.

## Dependencies

- **rdflib** (>=7.5.0): RDF graph operations
- **pyyaml** (>=6.0): YAML template loading
