"""Dedicated tests for the follow spec (one-hop label resolution).

Tests the follow mechanism in _extract_field: instead of returning a raw
concept URI, follow a predicate (e.g. skos:prefLabel) to get a human-readable
label. All tests use synthetic graphs since the 100 Cellar fixtures contain
zero skos:prefLabel triples.

Covers:
- Basic label resolution via follow
- Fallback when the followed predicate has no value
- Multilingual labels via follow + multilingual: true
- Consumed predicate tracking (base predicate, not followed one)
- Cardinality "many" with follow
- traversal.follow_predicate() directly
"""

from rdflib import Graph, Literal, Namespace, URIRef, RDF
from rdflib.namespace import SKOS

from openbasement import extract, load_template
from openbasement.traversal import follow_predicate

EX = Namespace("http://example.org/ontology#")
CONCEPT = Namespace("http://example.org/concept/")


def _make_template(fields: dict, prefixes: dict | None = None) -> dict:
    """Build a minimal normalized template with one entity."""
    pf = {
        "ex": str(EX),
        "concept": str(CONCEPT),
        "skos": str(SKOS),
    }
    if prefixes:
        pf.update(prefixes)
    return load_template({
        "version": "1",
        "prefixes": pf,
        "languages": {
            "preferred": ["en", "fr", "de"],
            "fallback": "any",
        },
        "entities": {
            "thing": {
                "find": {
                    "type": "ex:Thing",
                    "include_subclasses": False,
                },
                "fields": fields,
                "relations": {},
            }
        },
    })


def _make_base_graph() -> tuple[Graph, URIRef, URIRef]:
    """Create a graph with an entity linked to a concept URI."""
    g = Graph()
    entity = URIRef("http://example.org/entity/1")
    concept = CONCEPT["directive"]

    g.add((entity, RDF.type, EX["Thing"]))
    g.add((entity, EX["has_type"], concept))
    return g, entity, concept


# ---------------------------------------------------------------------------
# 1. Basic follow resolves label
# ---------------------------------------------------------------------------

def test_follow_resolves_label():
    """Follow should replace the concept URI with its skos:prefLabel value."""
    g, entity, concept = _make_base_graph()
    g.add((concept, SKOS.prefLabel, Literal("Directive")))

    template = _make_template({
        "type_label": {
            "predicate": "ex:has_type",
            "follow": {
                "predicate": "skos:prefLabel",
            },
        },
    })

    results = extract(g, template=template, entity="thing")
    assert len(results) == 1
    assert results[0]["type_label"] == "Directive"


# ---------------------------------------------------------------------------
# 2. Follow fallback returns original URI when no label exists
# ---------------------------------------------------------------------------

def test_follow_fallback_returns_original():
    """When the followed predicate has no value, return the original object."""
    g, entity, concept = _make_base_graph()
    # No skos:prefLabel added to concept

    template = _make_template({
        "type_label": {
            "predicate": "ex:has_type",
            "follow": {
                "predicate": "skos:prefLabel",
            },
        },
    })

    results = extract(g, template=template, entity="thing")
    assert len(results) == 1
    # Should fall back to the concept URI string
    assert results[0]["type_label"] == str(concept)


# ---------------------------------------------------------------------------
# 3. Follow with multilingual labels
# ---------------------------------------------------------------------------

def test_follow_multilingual():
    """Follow + multilingual: true should produce a language-keyed dict."""
    g, entity, concept = _make_base_graph()
    g.add((concept, SKOS.prefLabel, Literal("Directive", lang="en")))
    g.add((concept, SKOS.prefLabel, Literal("Directive (fr)", lang="fr")))
    g.add((concept, SKOS.prefLabel, Literal("Richtlinie", lang="de")))

    template = _make_template({
        "type_label": {
            "predicate": "ex:has_type",
            "follow": {
                "predicate": "skos:prefLabel",
                "multilingual": True,
            },
        },
    })

    results = extract(g, template=template, entity="thing")
    assert len(results) == 1
    label = results[0]["type_label"]

    assert isinstance(label, dict), f"Expected language-keyed dict, got {type(label)}: {label}"
    assert label["en"] == "Directive"
    assert label["fr"] == "Directive (fr)"
    assert label["de"] == "Richtlinie"


# ---------------------------------------------------------------------------
# 4. Consumed predicates: base predicate tracked, not the followed one
# ---------------------------------------------------------------------------

def test_follow_consumed_predicates():
    """The base predicate (ex:has_type) should be consumed, not leaking into _raw_triples."""
    g, entity, concept = _make_base_graph()
    g.add((concept, SKOS.prefLabel, Literal("Directive")))

    template = _make_template({
        "type_label": {
            "predicate": "ex:has_type",
            "follow": {
                "predicate": "skos:prefLabel",
            },
        },
    })

    results = extract(g, template=template, entity="thing")
    assert len(results) == 1

    raw_preds = {triple[1] for triple in results[0].get("_raw_triples", [])}
    assert str(EX["has_type"]) not in raw_preds, (
        "Base predicate ex:has_type should be consumed, not in _raw_triples"
    )


# ---------------------------------------------------------------------------
# 5. Follow with cardinality "many"
# ---------------------------------------------------------------------------

def test_follow_with_cardinality_many():
    """Cardinality many + follow should produce a list of label strings."""
    g = Graph()
    entity = URIRef("http://example.org/entity/1")
    concept_a = CONCEPT["directive"]
    concept_b = CONCEPT["regulation"]

    g.add((entity, RDF.type, EX["Thing"]))
    g.add((entity, EX["has_type"], concept_a))
    g.add((entity, EX["has_type"], concept_b))
    g.add((concept_a, SKOS.prefLabel, Literal("Directive")))
    g.add((concept_b, SKOS.prefLabel, Literal("Regulation")))

    template = _make_template({
        "type_labels": {
            "predicate": "ex:has_type",
            "cardinality": "many",
            "follow": {
                "predicate": "skos:prefLabel",
            },
        },
    })

    results = extract(g, template=template, entity="thing")
    assert len(results) == 1
    labels = results[0]["type_labels"]

    assert isinstance(labels, list)
    assert len(labels) == 2
    assert set(labels) == {"Directive", "Regulation"}


# ---------------------------------------------------------------------------
# 6. traversal.follow_predicate() directly
# ---------------------------------------------------------------------------

class TestFollowPredicate:
    """Unit tests for traversal.follow_predicate()."""

    def test_forward(self):
        g = Graph()
        s = URIRef("http://example.org/s")
        o = URIRef("http://example.org/o")
        p = EX["link"]
        g.add((s, p, o))

        result = follow_predicate(g, s, p, direction="forward")
        assert result == [o]

    def test_inverse(self):
        g = Graph()
        s = URIRef("http://example.org/s")
        o = URIRef("http://example.org/o")
        p = EX["link"]
        g.add((s, p, o))

        result = follow_predicate(g, o, p, direction="inverse")
        assert result == [s]

    def test_no_match(self):
        g = Graph()
        s = URIRef("http://example.org/s")
        p = EX["link"]

        result = follow_predicate(g, s, p)
        assert result == []

    def test_multiple_targets(self):
        g = Graph()
        s = URIRef("http://example.org/s")
        o1 = URIRef("http://example.org/o1")
        o2 = URIRef("http://example.org/o2")
        p = EX["link"]
        g.add((s, p, o1))
        g.add((s, p, o2))

        result = follow_predicate(g, s, p)
        assert len(result) == 2
        assert set(result) == {o1, o2}
