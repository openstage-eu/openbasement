"""Dedicated tests for wildcard field extraction.

Tests the wildcard predicate matching mechanism (cdm:date_*, cdm:event_legal_*)
against real Cellar fixtures and synthetic graphs. Covers:
- Wildcard fields produce dict output with correct keys/values
- Exclude lists filter specified predicates
- Consumed predicates don't leak into _raw_triples
- Edge cases (empty results, no matching predicates)
- matching.py functions directly
"""

from pathlib import Path

import pytest
from rdflib import Graph, Literal, Namespace, URIRef, RDF

from openbasement import extract, load_template
from openbasement.matching import is_wildcard, matches_predicate, extract_local_name

from tests.conftest import PROCEDURES_DIR

CDM = Namespace("http://publications.europa.eu/ontology/cdm#")

# Pick a small set of fixtures known to have date_* predicates on procedures
# and event_legal_* predicates on events.
_FIXTURE_REFS = ["2025_54", "2025_61", "2024_123"]


def _available_fixtures() -> list[tuple[Path, str]]:
    """Return (path, ref) pairs for available fixtures from our target set."""
    if not PROCEDURES_DIR.exists():
        return []
    pairs = []
    for ref in _FIXTURE_REFS:
        path = PROCEDURES_DIR / f"{ref}.rdf"
        if path.exists():
            pairs.append((path, ref))
    return pairs


def _load_graph(path: Path) -> Graph:
    g = Graph()
    g.parse(str(path), format="xml")
    return g


# ---------------------------------------------------------------------------
# Helpers to extract with specific entity types
# ---------------------------------------------------------------------------

def _extract_procedure(g: Graph) -> dict:
    results = extract(g, template="eu_procedure", entity="procedure")
    assert results, "No procedure entities extracted"
    return results[0]


def _extract_events(g: Graph) -> list[dict]:
    results = extract(g, template="eu_procedure", entity="event")
    return results


# ---------------------------------------------------------------------------
# 1. Wildcard dates on procedures
# ---------------------------------------------------------------------------

_fixtures = _available_fixtures()
_skip_no_fixtures = pytest.mark.skipif(
    not _fixtures, reason="No test fixtures available"
)


@_skip_no_fixtures
@pytest.mark.parametrize("rdf_path,proc_ref", _fixtures, ids=[f[1] for f in _fixtures])
def test_wildcard_dates_returns_dict(rdf_path, proc_ref):
    """The 'dates' wildcard field (cdm:date_*) should produce a dict."""
    g = _load_graph(rdf_path)
    proc = _extract_procedure(g)

    dates = proc.get("dates")
    assert isinstance(dates, dict), f"Expected dict, got {type(dates)}"

    # Keys should be local names (no full URIs)
    for key in dates:
        assert isinstance(key, str)
        assert not key.startswith("http"), f"Key should be local name, got {key}"
        assert key.startswith("date_"), f"Key should match date_* pattern, got {key}"

    # Values should be strings (date literals)
    for val in dates.values():
        assert isinstance(val, str), f"Expected string value, got {type(val)}: {val}"


# ---------------------------------------------------------------------------
# 2. Wildcard event other_properties
# ---------------------------------------------------------------------------

@_skip_no_fixtures
@pytest.mark.parametrize("rdf_path,proc_ref", _fixtures, ids=[f[1] for f in _fixtures])
def test_wildcard_event_other_properties_returns_dict(rdf_path, proc_ref):
    """The 'other_properties' wildcard field (cdm:event_legal_*) should produce a dict."""
    g = _load_graph(rdf_path)
    events = _extract_events(g)

    if not events:
        pytest.skip(f"No events in {proc_ref}")

    # At least one event should have other_properties as a dict
    for event in events:
        other = event.get("other_properties")
        assert isinstance(other, dict), (
            f"Expected dict for other_properties, got {type(other)}"
        )

        for key in other:
            assert isinstance(key, str)
            assert not key.startswith("http"), f"Key should be local name, got {key}"


# ---------------------------------------------------------------------------
# 3. Exclude list actually excludes
# ---------------------------------------------------------------------------

@_skip_no_fixtures
@pytest.mark.parametrize("rdf_path,proc_ref", _fixtures, ids=[f[1] for f in _fixtures])
def test_wildcard_exclude_actually_excludes(rdf_path, proc_ref):
    """Excluded predicates should NOT appear in other_properties."""
    g = _load_graph(rdf_path)
    events = _extract_events(g)

    if not events:
        pytest.skip(f"No events in {proc_ref}")

    # These are the excluded local names from eu_procedure.yaml event.other_properties
    excluded_local_names = {
        "event_legal_date",
        "event_legal_type",
        "event_legal_has_type_concept_type_event_legal",
        "event_legal_initiated_by_institution",
        "event_legal_formally-addresses_institution",
        "event_legal_document_reference",
        "event_legal_occurs_in_procedure-phase",
        "event_legal_initiating",
        "event_legal_contains_work",
        "event_legal_part_of_dossier",
    }

    for event in events:
        other = event.get("other_properties", {})
        found_excluded = set(other.keys()) & excluded_local_names
        assert not found_excluded, (
            f"Excluded predicates found in other_properties: {found_excluded}"
        )

        # Cross-check: excluded predicates that have their own named fields
        # should appear there instead (date, type_code are the main ones)
        if event.get("date") is not None:
            assert "event_legal_date" not in other
        if event.get("type_code") is not None:
            assert "event_legal_type" not in other


# ---------------------------------------------------------------------------
# 4. Consumed predicates not in _raw_triples
# ---------------------------------------------------------------------------

@_skip_no_fixtures
@pytest.mark.parametrize("rdf_path,proc_ref", _fixtures, ids=[f[1] for f in _fixtures])
def test_wildcard_consumed_not_in_raw_triples(rdf_path, proc_ref):
    """Predicates consumed by wildcard fields must not appear in _raw_triples."""
    g = _load_graph(rdf_path)
    proc = _extract_procedure(g)

    dates = proc.get("dates", {})
    if not dates:
        pytest.skip(f"No wildcard dates in {proc_ref}")

    # Build the set of consumed predicate URIs from the wildcard keys
    cdm_ns = str(CDM)
    consumed_uris = {cdm_ns + key for key in dates}

    # Check none of them appear in _raw_triples
    raw_pred_uris = {triple[1] for triple in proc.get("_raw_triples", [])}
    leaked = consumed_uris & raw_pred_uris
    assert not leaked, (
        f"Wildcard-consumed predicates leaked into _raw_triples: {leaked}"
    )


# ---------------------------------------------------------------------------
# 5. Wildcard with no matching predicates -> empty dict
# ---------------------------------------------------------------------------

def test_wildcard_empty_result():
    """A wildcard field with no matching predicates should return {}."""
    g = Graph()
    ns = Namespace("http://example.org/ontology#")
    entity = URIRef("http://example.org/entity/1")

    # Add a type triple so find_instances discovers the entity
    g.add((entity, RDF.type, ns["SomeType"]))
    # Add a predicate that does NOT match "date_*"
    g.add((entity, ns["title"], Literal("A title")))

    template = load_template({
        "version": "1",
        "prefixes": {
            "ex": str(ns),
        },
        "languages": {
            "preferred": ["en"],
            "fallback": "any",
        },
        "entities": {
            "thing": {
                "find": {
                    "type": "ex:SomeType",
                    "include_subclasses": False,
                },
                "fields": {
                    "dates": {
                        "predicate": "ex:date_*",
                        "collect": "dict",
                    },
                },
                "relations": {},
            }
        },
    })

    results = extract(g, template=template, entity="thing")
    assert len(results) == 1
    assert results[0]["dates"] == {}


# ---------------------------------------------------------------------------
# 6. matching.py functions directly
# ---------------------------------------------------------------------------

class TestIsWildcard:
    def test_star(self):
        assert is_wildcard("cdm:date_*") is True

    def test_question_mark(self):
        assert is_wildcard("cdm:date_?") is True

    def test_no_wildcard(self):
        assert is_wildcard("cdm:date_adopted") is False

    def test_star_in_middle(self):
        assert is_wildcard("cdm:event_*_date") is True

    def test_empty(self):
        assert is_wildcard("") is False


class TestMatchesPredicate:
    NS = "http://publications.europa.eu/ontology/cdm#"

    def test_star_matches(self):
        pred = URIRef(self.NS + "date_adopted")
        pattern = self.NS + "date_*"
        assert matches_predicate(pred, pattern, self.NS) is True

    def test_star_no_match(self):
        pred = URIRef(self.NS + "title")
        pattern = self.NS + "date_*"
        assert matches_predicate(pred, pattern, self.NS) is False

    def test_question_mark_matches(self):
        pred = URIRef(self.NS + "date_x")
        pattern = self.NS + "date_?"
        assert matches_predicate(pred, pattern, self.NS) is True

    def test_question_mark_no_match(self):
        pred = URIRef(self.NS + "date_ab")
        pattern = self.NS + "date_?"
        assert matches_predicate(pred, pattern, self.NS) is False

    def test_different_namespace(self):
        other_ns = "http://example.org/ns#"
        pred = URIRef(other_ns + "date_adopted")
        pattern = self.NS + "date_*"
        assert matches_predicate(pred, pattern, self.NS) is False


class TestExtractLocalName:
    NS = "http://publications.europa.eu/ontology/cdm#"

    def test_with_matching_namespace(self):
        uri = URIRef(self.NS + "date_adopted")
        assert extract_local_name(uri, self.NS) == "date_adopted"

    def test_with_fragment(self):
        uri = URIRef("http://example.org/ontology#myProp")
        assert extract_local_name(uri, "http://other.org/") == "myProp"

    def test_with_path_segment(self):
        uri = URIRef("http://example.org/ontology/myProp")
        assert extract_local_name(uri, "http://other.org/") == "myProp"

    def test_string_input(self):
        assert extract_local_name(self.NS + "title", self.NS) == "title"
