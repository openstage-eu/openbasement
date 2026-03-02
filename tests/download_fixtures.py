"""
Download test fixtures for openbasement from EU Cellar and EUR-Lex.

Standalone script (no openstage dependency). Downloads:
- ~100 procedure RDF/XML tree notices from Cellar (complete with inline event data)
- Corresponding validation data scraped from EUR-Lex procedure pages
- Builds index.json mapping procedure references to metadata

Usage:
    python tests/download_fixtures.py [--limit N] [--delay SECONDS]

Uses the RDF tree notice format (application/rdf+xml;notice=tree) which includes
full event metadata (dates, types, institutions) inline in a single request,
parseable by rdflib with no custom code.

Idempotent: re-running skips already-downloaded files.
"""

import argparse
import csv
import html
import io
import json
import logging
import re
import time
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
PROCEDURE_RESOURCE_URL = "https://publications.europa.eu/resource/procedure/{proc_ref}"
EURLEX_PROCEDURE_URL = "https://eur-lex.europa.eu/procedure/EN/{proc_ref}"

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROCEDURES_DIR = FIXTURES_DIR / "procedures"
VALIDATION_DIR = FIXTURES_DIR / "validation"
INDEX_PATH = FIXTURES_DIR / "index.json"

USER_AGENT = "openbasement-test-fixtures/0.1 (research; +https://github.com/maxhaag)"

# SPARQL query: get procedure references directly (YYYY_NNN format)
SPARQL_QUERY = """
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?procedure
WHERE {
  {
    ?procURI rdf:type cdm:procedure_interinstitutional .
  }
  UNION
  {
    ?subClass rdfs:subClassOf cdm:procedure_interinstitutional .
    ?procURI rdf:type ?subClass .
  }

  ?procURI ?p ?procCodeIRI .
  FILTER(isIRI(?procCodeIRI))
  FILTER(regex(str(?procCodeIRI), "/resource/procedure/"))

  BIND(
    REPLACE(
      STR(?procCodeIRI),
      ".*/resource/procedure/",
      ""
    ) AS ?procedure
  )
}
ORDER BY ?procedure
"""


def query_sparql(limit: int | None = None) -> list[str]:
    """Query SPARQL endpoint for procedure references (YYYY_NNN format)."""
    query = SPARQL_QUERY
    if limit:
        query += f"\nLIMIT {limit}"

    log.info("Querying SPARQL endpoint for procedures...")
    resp = requests.post(
        SPARQL_ENDPOINT,
        data={"query": query},
        headers={
            "Accept": "text/csv",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        timeout=120,
    )
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    refs = [row["procedure"].strip() for row in reader if row.get("procedure", "").strip()]
    log.info("SPARQL returned %d procedure references", len(refs))
    return refs


def select_procedures(refs: list[str], target_count: int = 100) -> list[str]:
    """Select ~target_count procedures from the full list.

    Sorts by reference (year_number) to get a spread across time periods.
    """
    unique = sorted(set(refs))
    log.info("Unique procedure references: %d", len(unique))

    # Take evenly spaced samples across the sorted list
    if len(unique) <= target_count:
        selected = unique
    else:
        step = len(unique) / target_count
        selected = [unique[int(i * step)] for i in range(target_count)]

    log.info("Selected %d procedures for download", len(selected))
    return selected


def proc_ref_to_filename(proc_ref: str) -> str:
    """Convert procedure reference to safe filename: 2016_399 -> 2016_399.rdf"""
    return f"{proc_ref}.rdf"


def download_rdf_tree(proc_ref: str, delay: float = 0.5) -> bool:
    """Download RDF/XML tree notice for a procedure. Returns True on success.

    Uses Accept: application/rdf+xml;notice=tree to get the full tree
    including inline event data (dates, types, institutions).
    """
    out_path = PROCEDURES_DIR / proc_ref_to_filename(proc_ref)
    if out_path.exists() and out_path.stat().st_size > 0:
        log.debug("Already downloaded: %s", proc_ref)
        return True

    url = PROCEDURE_RESOURCE_URL.format(proc_ref=proc_ref)
    try:
        resp = requests.get(
            url,
            headers={
                "Accept": "application/rdf+xml;notice=tree",
                "User-Agent": USER_AGENT,
            },
            timeout=60,
        )
        resp.raise_for_status()
        if len(resp.content) == 0:
            log.warning("Empty response for %s", proc_ref)
            return False
        out_path.write_bytes(resp.content)
        log.info("Downloaded RDF tree: %s (%d bytes)", proc_ref, len(resp.content))
        time.sleep(delay)
        return True
    except requests.RequestException as e:
        log.warning("Failed to download RDF for %s: %s", proc_ref, e)
        return False


def _parse_eurlex_timeline(page_html: str) -> dict | None:
    """Extract timeline JSON and procedure metadata from EUR-Lex procedure page.

    The page embeds timeline data as a JavaScript variable:
        var json = { 'startYear':'2019', ..., 'events':[...] };

    The JSON uses JS-style quoting (single quotes, escaped apostrophes,
    HTML with double quotes inside description values). Rather than
    attempting a full JS-to-JSON conversion, we strip the description
    fields (not needed for validation) and convert the remaining simple
    key-value pairs.
    """
    # Extract the var json = {...}; block.
    # Use ]\s*} as the end anchor because the timeline always ends with
    # the events array closing, and a naive {..} match would stop at the
    # first event object's closing brace.
    match = re.search(
        r"var\s+json\s*=\s*(\{.+?\]\s*\})\s*;",
        page_html,
        re.DOTALL,
    )
    if not match:
        return None

    raw = match.group(1)

    # Step 1: Remove description fields entirely. They contain HTML with
    # mixed quoting (double quotes in attributes, escaped apostrophes in text)
    # that makes JS-to-JSON conversion unreliable. We don't need them for
    # validation anyway.
    raw = re.sub(r"'description'\s*:\s*'(?:[^'\\]|\\.)*'", '"description": ""', raw)

    # Step 2: Handle escaped apostrophes (\') that appear in JS strings.
    # Convert to unicode escape so they survive the quote conversion step.
    raw = raw.replace("\\'", "\\u0027")

    # Step 3: Convert remaining single-quoted strings to double-quoted
    raw = re.sub(r"'([^']*?)'", r'"\1"', raw)

    # Step 4: Remove trailing commas before ] or }
    raw = re.sub(r",\s*([}\]])", r"\1", raw)

    try:
        timeline = json.loads(raw)
    except json.JSONDecodeError:
        return None

    return timeline


def _extract_procedure_title(page_html: str) -> str:
    """Extract procedure title from the data-content popover attribute."""
    match = re.search(r'data-content="([^"]+)"', page_html)
    if not match:
        return ""
    raw = html.unescape(match.group(1))
    # data-content is like "COM (2019) 251<br>\n\t\tProposal for a DECISION..."
    # Split on <br> and take the title part (after the COM reference)
    parts = re.split(r"<br\s*/?>", raw)
    if len(parts) >= 2:
        return parts[-1].strip()
    return raw.strip()


def download_eurlex_validation(
    proc_ref: str, delay: float = 0.5
) -> dict | None:
    """Download and parse EUR-Lex procedure page for validation data.

    Returns a dict with procedure metadata and timeline events, or None on failure.
    """
    out_path = VALIDATION_DIR / f"{proc_ref}.json"
    if out_path.exists() and out_path.stat().st_size > 0:
        log.debug("Already downloaded validation: %s", proc_ref)
        with open(out_path) as f:
            return json.load(f)

    url = EURLEX_PROCEDURE_URL.format(proc_ref=proc_ref)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Failed to fetch EUR-Lex page for %s: %s", proc_ref, e)
        return None

    page_html = resp.text

    timeline = _parse_eurlex_timeline(page_html)
    if timeline is None:
        log.warning("No timeline data found in EUR-Lex page for %s", proc_ref)
        return None

    title = _extract_procedure_title(page_html)

    events = timeline.get("events") or []
    # Normalize events into a clean structure
    normalized_events = []
    for evt in events:
        year = evt.get("startYear", "")
        month = evt.get("startMonth", "").zfill(2)
        day = evt.get("startDay", "").zfill(2)
        date_str = f"{year}-{month}-{day}" if year else ""

        normalized_events.append({
            "date": date_str,
            "title": evt.get("title", ""),
            "actor": evt.get("actor", ""),
            "actor_id": evt.get("actorId", ""),
            "link": evt.get("link", ""),
            "icon_class": evt.get("iconClass", ""),
        })

    data = {
        "source": "eurlex",
        "procedure_reference": proc_ref,
        "title": title,
        "timeline_start": f"{timeline.get('startYear', '')}-{timeline.get('startMonth', '').zfill(2)}-{timeline.get('startDay', '').zfill(2)}",
        "timeline_end": f"{timeline.get('endYear', '')}-{timeline.get('endMonth', '').zfill(2)}-{timeline.get('endDay', '').zfill(2)}",
        "event_count": len(normalized_events),
        "events": normalized_events,
    }

    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log.info(
        "Downloaded EUR-Lex validation: %s (%d events)",
        proc_ref,
        len(normalized_events),
    )
    time.sleep(delay)
    return data


def build_index(proc_refs: list[str]) -> dict:
    """Build index.json from downloaded procedures and validation data."""
    index = {}
    for proc_ref in proc_refs:
        rdf_path = PROCEDURES_DIR / proc_ref_to_filename(proc_ref)
        val_path = VALIDATION_DIR / f"{proc_ref}.json"

        entry = {
            "procedure_reference": proc_ref,
            "rdf_downloaded": rdf_path.exists(),
            "rdf_bytes": rdf_path.stat().st_size if rdf_path.exists() else 0,
            "validation_downloaded": val_path.exists(),
        }

        if val_path.exists():
            try:
                with open(val_path) as f:
                    data = json.load(f)
                entry["title"] = data.get("title", "")
                entry["event_count"] = data.get("event_count", 0)
            except (json.JSONDecodeError, KeyError) as e:
                log.warning("Error reading validation for %s: %s", proc_ref, e)

        index[proc_ref] = entry

    return index


def main():
    parser = argparse.ArgumentParser(description="Download openbasement test fixtures")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit SPARQL results (default: fetch all, then select ~100)",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=100,
        help="Target number of procedures to download (default: 100)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests in seconds (default: 0.5)",
    )
    args = parser.parse_args()

    # Ensure directories exist
    PROCEDURES_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Query SPARQL for procedure references
    refs = query_sparql(limit=args.limit)

    # Step 2: Select procedures
    selected = select_procedures(refs, target_count=args.target)

    # Step 3: Download RDF/XML tree notices
    rdf_success = 0
    rdf_fail = 0
    for i, proc_ref in enumerate(selected):
        log.info("RDF %d/%d: %s", i + 1, len(selected), proc_ref)
        if download_rdf_tree(proc_ref, delay=args.delay):
            rdf_success += 1
        else:
            rdf_fail += 1

    log.info("RDF downloads: %d success, %d failed", rdf_success, rdf_fail)

    # Step 4: Download EUR-Lex validation
    val_success = 0
    val_fail = 0
    for i, proc_ref in enumerate(selected):
        log.info(
            "EUR-Lex %d/%d: %s",
            i + 1,
            len(selected),
            proc_ref,
        )
        result = download_eurlex_validation(proc_ref, delay=args.delay)
        if result is not None:
            val_success += 1
        else:
            val_fail += 1

    log.info(
        "EUR-Lex validation downloads: %d success, %d failed",
        val_success,
        val_fail,
    )

    # Step 5: Build index
    index = build_index(selected)
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False))
    log.info("Index written to %s (%d entries)", INDEX_PATH, len(index))

    # Summary
    log.info("--- Summary ---")
    log.info("Procedures selected: %d", len(selected))
    log.info("RDF tree files: %d", rdf_success)
    log.info("Validation files: %d", val_success)
    log.info("Fixtures directory: %s", FIXTURES_DIR)


if __name__ == "__main__":
    main()
