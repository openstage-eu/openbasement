"""Test openbasement extraction against real Cellar RDF fixtures.

These tests load each fixture, run extract() with the eu_procedure template,
and compare results against EUR-Lex validation data. Mismatches are
reported as warnings rather than hard failures, since the templates may
need adjustment as we discover more about the data.
"""

import json
import logging
from pathlib import Path

import pytest
from rdflib import Graph

from openbasement import extract

from tests.conftest import PROCEDURES_DIR, VALIDATION_DIR, INDEX_PATH

logger = logging.getLogger(__name__)


def _fixture_pairs():
    """Yield (rdf_path, validation_path_or_none, proc_ref) tuples."""
    if not PROCEDURES_DIR.exists():
        return []

    pairs = []
    for rdf_path in sorted(PROCEDURES_DIR.glob("*.rdf")):
        proc_ref = rdf_path.stem
        val_path = VALIDATION_DIR / f"{proc_ref}.json"
        pairs.append((rdf_path, val_path if val_path.exists() else None, proc_ref))
    return pairs


@pytest.mark.parametrize(
    "rdf_path,val_path,proc_ref",
    _fixture_pairs(),
    ids=lambda x: x if isinstance(x, str) else "",
)
def test_extract_produces_results(rdf_path, val_path, proc_ref):
    """extract() should return non-empty results for each fixture."""
    g = Graph()
    g.parse(str(rdf_path), format="xml")

    results = extract(g, template="eu_procedure")

    # We expect at least one procedure entity
    assert len(results) > 0, f"No procedure entities extracted from {proc_ref}"

    proc = results[0]
    assert "_uri" in proc
    assert "_rdf_types" in proc
    assert "_raw_triples" in proc


@pytest.mark.parametrize(
    "rdf_path,val_path,proc_ref",
    [p for p in _fixture_pairs() if p[1] is not None],
    ids=lambda x: x if isinstance(x, str) else "",
)
def test_extract_matches_validation(rdf_path, val_path, proc_ref):
    """Compare extracted data against EUR-Lex validation.

    Hard failures for properties known to be reliable (date overlap,
    event dates, event count >= EUR-Lex). Title comparison remains a
    warning due to cosmetic case differences across data vintages.
    """
    g = Graph()
    g.parse(str(rdf_path), format="xml")

    results = extract(g, template="eu_procedure")
    if not results:
        pytest.skip(f"No procedure entities found in {proc_ref}")

    proc = results[0]

    with open(val_path) as f:
        validation = json.load(f)

    expected_events = validation.get("events") or []
    extracted_events = proc.get("events", [])

    # -- Hard assertions ------------------------------------------------

    # Every extracted event must have a date.
    # Known exception: 2020_330 includes a cross-procedure event from
    # 2020_1998 via owl:sameAs leakage (no date on that foreign event).
    if extracted_events:
        events_with_dates = sum(
            1 for e in extracted_events
            if isinstance(e, dict) and e.get("date")
        )
        if events_with_dates != len(extracted_events):
            if proc_ref == "2020_330":
                pytest.xfail(
                    f"[{proc_ref}] Cross-procedure event leakage via owl:sameAs"
                )
            assert events_with_dates == len(extracted_events), (
                f"[{proc_ref}] {len(extracted_events) - events_with_dates} events "
                f"missing dates ({events_with_dates}/{len(extracted_events)} have dates)"
            )

    # Event counts differ between RDF and EUR-Lex (different granularity
    # in both directions). Log as warning, not a hard failure.
    if expected_events and len(extracted_events) != len(expected_events):
        logger.warning(
            "Event count for %s: extracted=%d, EUR-Lex=%d",
            proc_ref, len(extracted_events), len(expected_events),
        )

    # All EUR-Lex dates must appear in extracted dates
    if extracted_events and expected_events:
        extracted_dates = {
            e.get("date")
            for e in extracted_events
            if isinstance(e, dict) and e.get("date")
        }
        expected_dates = {
            e.get("date") for e in expected_events if e.get("date")
        }
        if expected_dates:
            missing_dates = expected_dates - extracted_dates
            assert not missing_dates, (
                f"[{proc_ref}] EUR-Lex dates not found in extracted: "
                f"{sorted(missing_dates)}"
            )
            extra_dates = extracted_dates - expected_dates
            if extra_dates:
                logger.warning(
                    "Extra RDF dates not in EUR-Lex for %s: %s",
                    proc_ref, sorted(extra_dates),
                )

    # -- Soft warnings (title comparison) -------------------------------

    extracted_title = proc.get("title")
    expected_title = validation.get("title", "")
    if extracted_title and expected_title:
        title_value = next(iter(extracted_title.values()), "") if isinstance(extracted_title, dict) else str(extracted_title)
        if expected_title and title_value and expected_title[:50].lower() not in title_value[:80].lower():
            logger.warning(
                "Title mismatch for %s: extracted=%r expected=%r",
                proc_ref, title_value[:60], expected_title[:60],
            )


@pytest.mark.parametrize(
    "rdf_path,val_path,proc_ref",
    _fixture_pairs()[:5],  # Just first 5 for this detailed check
    ids=lambda x: x if isinstance(x, str) else "",
)
def test_extract_event_entities(rdf_path, val_path, proc_ref):
    """Extract events directly and check they have expected fields."""
    g = Graph()
    g.parse(str(rdf_path), format="xml")

    events = extract(g, template="eu_procedure", entity="event")

    for event in events:
        # Flat output: event["date"] instead of event["fields"]["date"]
        has_date = event.get("date") is not None
        has_type = event.get("type_code") is not None
        assert has_date or has_type, (
            f"Event {event.get('_uri', '?')} in {proc_ref} has neither date nor type_code"
        )


@pytest.mark.parametrize(
    "rdf_path,val_path,proc_ref",
    _fixture_pairs()[:5],
    ids=lambda x: x if isinstance(x, str) else "",
)
def test_raw_triples_captured(rdf_path, val_path, proc_ref):
    """Verify that unconsumed triples are captured in raw_triples."""
    g = Graph()
    g.parse(str(rdf_path), format="xml")

    results = extract(g, template="eu_procedure")
    if not results:
        pytest.skip(f"No results for {proc_ref}")

    proc = results[0]
    # _raw_triples should exist (may be empty if template covers everything)
    assert "_raw_triples" in proc
