# openbasement

A Python library for template-based RDF extraction from EU legislative data published through [EU Cellar](https://op.europa.eu/en/web/cellar). It turns `rdflib.Graph` objects into structured Python dictionaries using declarative YAML templates that encode domain knowledge about the [CDM ontology](https://op.europa.eu/en/web/eu-vocabularies/cdm).

## Why it exists

EU Cellar publishes legislative procedure data as RDF using the Common Data Model (CDM) ontology. This RDF is rich but awkward to work with directly:

- **Predicate aliasing**: The same fact appears under 2-3 predicates simultaneously.
- **Multilingual literals**: Text fields exist in up to 24 EU languages.
- **Deep nesting**: A procedure contains events, documents, and concept references spread across hundreds of triples.
- **Subclass hierarchies**: Procedure types use `rdfs:subClassOf` rather than a single `rdf:type`.

openbasement handles all of this through YAML templates. The templates declare which predicates to look for (with aliases), how to resolve languages, and how to traverse relations. The Python code is generic. All CDM-specific knowledge lives in the templates.

## Core contract

Graph in, dicts out. No network I/O, no storage, no graph mutation, no data models.

## Next steps

- [Getting started](getting-started.md): Install from GitHub, run your first extraction, inspect output.
- [Templates](templates.md): Write and customize YAML templates.
- [API reference](api.md): Public functions and transforms.
- [CDM patterns](cdm-patterns.md): The ontology quirks that openbasement handles for you.
