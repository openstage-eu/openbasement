# CDM Patterns

The Common Data Model (CDM) ontology has several patterns that make raw RDF extraction painful. This page documents the quirks that openbasement handles and explains why the template system exists.

## Triple aliasing

The most pervasive CDM pattern. Every fact appears under multiple predicates simultaneously:

```turtle
<event> cdm:date                "2023-07-05" .
<event> cdm:event_date          "2023-07-05" .
<event> cdm:event_legal_date    "2023-07-05" .
```

The three forms follow a naming convention:

1. **Short form**: `cdm:date` (generic, shared across entity types)
2. **Entity-prefixed form**: `cdm:event_date` (scoped to the entity category)
3. **Fully qualified form**: `cdm:event_legal_date` (specific to the entity subtype)

Which predicates are present varies across data vintages. Older procedures (pre-2010) may use different predicate forms than recent ones. Templates handle this with predicate alias lists, trying each in order:

```yaml
date:
  predicate:
    - "cdm:event_legal_date"
    - "cdm:event_date"
    - "cdm:date"
```

For `cardinality: "one"`, the first alias that produces data wins. For `cardinality: "many"`, results from all aliases are merged and deduplicated.

## owl:sameAs identity

CDM uses multiple URIs for the same real-world entity. A single legislative procedure might have:

- A **pegase ID**: `resource/pegase/1044494` (internal identifier)
- A **procedure URI**: `resource/procedure/2017_33` (human-readable reference)
- A **cellar UUID**: `resource/cellar/abc123...` (storage identifier)

These are linked via `owl:sameAs`, sometimes through intermediate nodes:

```turtle
<resource/pegase/1044494>     owl:sameAs  <resource/cellar/abc123> .
<resource/procedure/2017_33>  owl:sameAs  <resource/cellar/abc123> .
```

This matters in two ways:

1. **Duplicate entities**: `find_instances()` may discover the same real-world entity multiple times (once per URI that carries the target `rdf:type`). Without merging, the output contains a rich entity (from the pegase URI with all the data) and a near-empty stub (from the procedure URI with only a few triples).

2. **Broken inverse lookups**: Events may point their `part_of_dossier` predicate to the procedure URI, while the `rdf:type` lives on the pegase URI. Without sameAs expansion, inverse predicate lookups miss these connections.

openbasement handles both problems:

- **sameAs merging** (default, controlled by the template's `same_as_merge` key or `merge_same_as=` function parameter): After discovering instances, the engine groups them into `owl:sameAs` equivalence classes using 2-hop expansion. Each group produces one output entity with triples merged from all alias URIs. A canonical URI is selected (preferring `resource/procedure/` over pegase/cellar URIs). All aliases are listed in the `_same_as` metadata field.

- **Inverse predicate expansion**: The `inverse_predicate` relation option queries all alias URIs when looking for nodes that point back to the entity. When sameAs merging is active, the alias set is already computed; otherwise, it falls back to expanding sameAs for the single instance.

## Multilingual literals

Most text fields in CDM exist in up to 24 EU languages as language-tagged RDF literals:

```turtle
<proc> cdm:title "Regulation on ..."@en .
<proc> cdm:title "Reglement sur ..."@fr .
<proc> cdm:title "Verordnung ..."@de .
```

openbasement collects all language variants into a language-keyed dict:

```python
{"en": "Regulation on ...", "fr": "Reglement sur ...", "de": "Verordnung ..."}
```

The template's `languages.preferred` list controls fallback ordering. Untagged literals (no language tag) appear under the `"_"` key when `fallback: "any"`.

## Subclass hierarchies

CDM uses `rdfs:subClassOf` for entity type specialization. A codecision procedure is not directly typed as `cdm:procedure_interinstitutional`. Instead:

```turtle
cdm:procedure_codecision  rdfs:subClassOf  cdm:procedure_interinstitutional .
<proc>  rdf:type  cdm:procedure_codecision .
```

Similarly, old procedures without an interinstitutional code use `cdm:procedure_without_code_interinstitutional` as a subclass.

Without subclass handling, a query for `procedure_interinstitutional` instances would miss these. The `include_subclasses: true` template option tells the engine to check one level of `rdfs:subClassOf` when finding instances.

Cellar pre-computes type hierarchies, so in practice the one-level subclass check is sufficient for tree notices.

## Predicate variation across vintages

Predicate usage is not uniform across the full historical dataset. A predicate that works on modern procedures (post-2015) may not exist on older ones (pre-2010). Templates should list all known predicate aliases to maximize coverage across the full date range.

The `audit()` function helps identify coverage gaps: it compares template predicates against actual graph content and reports which predicates are missing or uncovered.
