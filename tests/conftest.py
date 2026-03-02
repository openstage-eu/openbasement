"""Shared fixtures for openbasement tests."""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROCEDURES_DIR = FIXTURES_DIR / "procedures"
VALIDATION_DIR = FIXTURES_DIR / "validation"
INDEX_PATH = FIXTURES_DIR / "index.json"


def _load_index() -> dict:
    """Load the fixture index, or return empty dict if not yet built."""
    if not INDEX_PATH.exists():
        return {}
    with open(INDEX_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def fixture_index() -> dict:
    """The full fixture index mapping proc_ref -> metadata."""
    return _load_index()


@pytest.fixture(scope="session")
def rdf_fixture_paths() -> list[Path]:
    """All downloaded RDF/XML fixture file paths."""
    if not PROCEDURES_DIR.exists():
        return []
    return sorted(PROCEDURES_DIR.glob("*.rdf"))


@pytest.fixture(scope="session")
def validation_fixture_paths() -> list[Path]:
    """All downloaded law-tracker validation JSON file paths."""
    if not VALIDATION_DIR.exists():
        return []
    return sorted(VALIDATION_DIR.glob("*.json"))
