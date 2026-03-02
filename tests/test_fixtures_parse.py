"""Test that all RDF/XML fixtures can be parsed by rdflib."""

import pytest
from rdflib import Graph

from tests.conftest import PROCEDURES_DIR


def _rdf_files():
    if not PROCEDURES_DIR.exists():
        return []
    return sorted(PROCEDURES_DIR.glob("*.rdf"))


@pytest.mark.parametrize(
    "rdf_path",
    _rdf_files(),
    ids=lambda p: p.stem,
)
def test_rdflib_parses_fixture(rdf_path):
    """Each RDF/XML fixture should parse without errors."""
    g = Graph()
    g.parse(str(rdf_path), format="xml")
    assert len(g) > 0, f"Graph is empty for {rdf_path.name}"
