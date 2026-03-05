"""
Download test fixtures for openbasement from EU Cellar and EUR-Lex.

Standalone script (no openstage dependency). Downloads:
- ~100 procedure RDF/XML tree notices from Cellar (complete with inline event data)
- Corresponding validation data scraped from EUR-Lex procedure pages
- Builds index.json mapping procedure references to metadata

Usage:
    python tests/download_fixtures.py [--limit N] [--delay SECONDS] [--force-validation]
    python tests/download_fixtures.py --fetch-method browser --force-validation

EUR-Lex fetch methods:
    requests  - Plain HTTP (fast, but blocked by AWS WAF bot protection)
    browser   - Headless Chrome via nodriver (slower, bypasses bot protection)

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

# asyncio is only needed when --fetch-method=browser is used (nodriver is async-only).
# Imported lazily inside _fetch_eurlex_browser().

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
VALIDATION_HTML_DIR = FIXTURES_DIR / "validation_html"
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


def _parse_event_documents(description_html: str) -> tuple[list[dict], list[dict]]:
    """Extract document references from an event description HTML string.

    Description fields contain two kinds of <a> tags:
    1. Actual documents (COM proposals, OJ references, etc.) in plain <dd> blocks
    2. CELEX duplicates of those same documents, wrapped in <span lang="...">

    Returns (documents, celex_main) where celex_main contains the CELEX
    duplicates that should not be counted as separate documents.
    """
    docs = []
    celex_main = []
    seen_refs = set()

    # First pass: <a> tags with uri= parameter (structured document links).
    # Track whether they sit inside a <span lang="..."> wrapper, which marks
    # them as CELEX duplicates of the main documents.
    for m in re.finditer(
        r'(<span\s+lang="[^"]*">\s*)?<a\s[^>]*?uri=([^"&\'>\s]+)[^>]*>([^<]+)</a>',
        description_html,
    ):
        in_span = m.group(1) is not None
        uri_param = m.group(2)
        reference = m.group(3).strip()
        if uri_param and reference:
            entry = {"uri_param": uri_param, "reference": reference}
            if in_span:
                celex_main.append(entry)
            else:
                docs.append(entry)
                seen_refs.add(reference)

    # Second pass: external links without uri= (consilium register, press
    # releases, etc.). Link text may contain inline tags like <i> icons.
    for m in re.finditer(
        r'<a\s[^>]*href="(http[^"]+)"[^>]*>(.*?)</a>',
        description_html,
        re.DOTALL,
    ):
        href = m.group(1)
        # Skip links that have a uri= parameter (already captured above)
        if "uri=" in href:
            continue
        # Strip HTML tags and zero-width spaces from link text
        reference = re.sub(r"<[^>]+>", "", m.group(2))
        reference = reference.replace("\u200b", "").strip()
        if reference and reference not in seen_refs:
            docs.append({"uri_param": "", "reference": reference})
            seen_refs.add(reference)

    return docs, celex_main


def _parse_eurlex_timeline(page_html: str) -> dict | None:
    """Extract timeline JSON and procedure metadata from EUR-Lex procedure page.

    The page embeds timeline data as a JavaScript variable:
        var json = { 'startYear':'2019', ..., 'events':[...] };

    The JSON uses JS-style quoting (single quotes, escaped apostrophes,
    HTML with double quotes inside description values). Rather than
    attempting a full JS-to-JSON conversion, we capture description
    fields separately (for document extraction), then strip them for
    clean JSON parsing.
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

    # Step 0: Capture raw description strings before stripping them.
    # Indexed by position so we can reattach after JSON parsing.
    raw_descriptions = [
        m.group(1)
        for m in re.finditer(
            r"'description'\s*:\s*'((?:[^'\\]|\\.)*)'", raw
        )
    ]

    # Step 1: Remove description fields entirely. They contain HTML with
    # mixed quoting (double quotes in attributes, escaped apostrophes in text)
    # that makes JS-to-JSON conversion unreliable.
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

    # Reattach raw descriptions to events for document extraction
    events = timeline.get("events") or []
    for i, evt in enumerate(events):
        if i < len(raw_descriptions):
            evt["_raw_description"] = raw_descriptions[i]

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


def _fetch_eurlex_requests(url: str) -> str | None:
    """Fetch EUR-Lex page HTML using plain requests."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.warning("requests fetch failed for %s: %s", url, e)
        return None


def _fetch_eurlex_browser(url: str, delay: float = 1.5) -> str | None:
    """Fetch a single EUR-Lex page using headless Chrome.

    Prefer _fetch_eurlex_browser_batch() for multiple pages -- it reuses
    one browser session instead of launching Chrome per page.
    """
    results = _fetch_eurlex_browser_batch({url: url}, delay=delay)
    return results.get(url)


def _fetch_eurlex_browser_batch(
    url_map: dict[str, str],
    delay: float = 1.5,
) -> dict[str, str | None]:
    """Fetch multiple EUR-Lex pages with a single headless Chrome session.

    url_map: {key: url} -- keys are used to index the results dict.
    Returns {key: page_html_or_None}.
    """
    try:
        import asyncio

        import nodriver as uc
    except ImportError:
        log.error(
            "nodriver is required for --fetch-method=browser. "
            "Install it with: pip install 'openbasement[dev]'"
        )
        return {k: None for k in url_map}

    async def _get_pages():
        browser = await uc.start(headless=True)
        results = {}
        try:
            for key, url in url_map.items():
                try:
                    page = await browser.get(url)
                    await asyncio.sleep(delay)
                    content = await page.get_content()
                    results[key] = content
                except Exception as e:
                    log.warning("browser fetch failed for %s: %s", url, e)
                    results[key] = None
        finally:
            browser.stop()
        return results

    try:
        return uc.loop().run_until_complete(_get_pages())
    except Exception as e:
        log.error("browser batch fetch failed: %s", e)
        return {k: None for k in url_map}


def _build_validation_data(page_html: str, proc_ref: str) -> dict | None:
    """Parse EUR-Lex page HTML into structured validation data."""
    timeline = _parse_eurlex_timeline(page_html)
    if timeline is None:
        log.warning("No timeline data found in EUR-Lex page for %s", proc_ref)
        return None

    title = _extract_procedure_title(page_html)

    events = timeline.get("events") or []
    normalized_events = []
    all_doc_uris = set()
    for evt in events:
        year = evt.get("startYear", "")
        month = evt.get("startMonth", "").zfill(2)
        day = evt.get("startDay", "").zfill(2)
        date_str = f"{year}-{month}-{day}" if year else ""

        documents, celex_main = _parse_event_documents(evt.get("_raw_description", ""))
        for doc in documents:
            all_doc_uris.add(doc["uri_param"])

        normalized_events.append({
            "date": date_str,
            "title": evt.get("title", ""),
            "actor": evt.get("actor", ""),
            "actor_id": evt.get("actorId", ""),
            "link": evt.get("link", ""),
            "icon_class": evt.get("iconClass", ""),
            "documents": documents,
            "celex_main": celex_main,
        })

    return {
        "source": "eurlex",
        "procedure_reference": proc_ref,
        "title": title,
        "timeline_start": f"{timeline.get('startYear', '')}-{timeline.get('startMonth', '').zfill(2)}-{timeline.get('startDay', '').zfill(2)}",
        "timeline_end": f"{timeline.get('endYear', '')}-{timeline.get('endMonth', '').zfill(2)}-{timeline.get('endDay', '').zfill(2)}",
        "event_count": len(normalized_events),
        "document_count": len(all_doc_uris),
        "events": normalized_events,
    }


def download_eurlex_validation(
    proc_ref: str,
    delay: float = 0.5,
    force: bool = False,
    fetch_method: str = "requests",
) -> dict | None:
    """Download and parse EUR-Lex procedure page for validation data.

    fetch_method: "requests" for plain HTTP, "browser" for headless Chrome.
    Saves raw HTML to validation_html/ for later re-parsing without network.
    Returns a dict with procedure metadata and timeline events, or None on failure.
    """
    out_path = VALIDATION_DIR / f"{proc_ref}.json"
    html_path = VALIDATION_HTML_DIR / f"{proc_ref}.html"

    if not force and out_path.exists() and out_path.stat().st_size > 0:
        log.debug("Already downloaded validation: %s", proc_ref)
        with open(out_path) as f:
            return json.load(f)

    # Try cached HTML first (avoids re-fetching when only JSON is missing)
    if not force and html_path.exists() and html_path.stat().st_size > 0:
        log.info("Using cached HTML for %s", proc_ref)
        page_html = html_path.read_text(encoding="utf-8")
    else:
        url = EURLEX_PROCEDURE_URL.format(proc_ref=proc_ref)
        if fetch_method == "browser":
            page_html = _fetch_eurlex_browser(url, delay=delay)
        else:
            page_html = _fetch_eurlex_requests(url)

        if page_html is None:
            return None

        # Save raw HTML for future re-parsing
        html_path.write_text(page_html, encoding="utf-8")
        time.sleep(delay)

    data = _build_validation_data(page_html, proc_ref)
    if data is None:
        return None

    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log.info(
        "Downloaded EUR-Lex validation: %s (%d events)",
        proc_ref,
        data["event_count"],
    )
    return data


def reparse_cached_html() -> tuple[int, int]:
    """Re-parse all cached HTML files into validation JSON without network requests.

    Returns (success_count, fail_count).
    """
    if not VALIDATION_HTML_DIR.exists():
        log.warning("No validation_html directory found")
        return 0, 0

    html_files = sorted(VALIDATION_HTML_DIR.glob("*.html"))
    if not html_files:
        log.warning("No cached HTML files found")
        return 0, 0

    log.info("Re-parsing %d cached HTML files", len(html_files))
    success = 0
    fail = 0
    for html_path in html_files:
        proc_ref = html_path.stem
        page_html = html_path.read_text(encoding="utf-8")
        data = _build_validation_data(page_html, proc_ref)
        if data is None:
            log.warning("Failed to parse cached HTML for %s", proc_ref)
            fail += 1
            continue
        out_path = VALIDATION_DIR / f"{proc_ref}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        success += 1

    log.info("Re-parsed: %d success, %d failed", success, fail)
    return success, fail


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
                entry["document_count"] = data.get("document_count", 0)
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
    parser.add_argument(
        "--force-validation",
        action="store_true",
        help="Re-download validation JSONs even if they already exist",
    )
    parser.add_argument(
        "--fetch-method",
        choices=["requests", "browser"],
        default="requests",
        help=(
            "How to fetch EUR-Lex pages: 'requests' for plain HTTP, "
            "'browser' for headless Chrome via nodriver (default: requests)"
        ),
    )
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="Re-parse all cached HTML files into JSON without any network requests",
    )
    args = parser.parse_args()

    # Ensure directories exist
    PROCEDURES_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    VALIDATION_HTML_DIR.mkdir(parents=True, exist_ok=True)

    # Reparse mode: no network, just re-parse cached HTML
    if args.reparse:
        success, fail = reparse_cached_html()
        log.info("--- Reparse Summary ---")
        log.info("Success: %d, Failed: %d", success, fail)
        return

    # Step 1: Query SPARQL for procedure references
    refs = query_sparql(limit=args.limit)
    all_refs = sorted(set(refs))
    log.info("Total unique procedure references: %d", len(all_refs))

    # Step 2: Select procedures with oversampling (20% extra to cover 404s)
    oversample = int(args.target * 1.2)
    candidates = select_procedures(all_refs, target_count=oversample)

    # Step 3: Download RDF/XML tree notices
    rdf_ok = []
    rdf_fail = 0
    for i, proc_ref in enumerate(candidates):
        log.info("RDF %d/%d: %s", i + 1, len(candidates), proc_ref)
        if download_rdf_tree(proc_ref, delay=args.delay):
            rdf_ok.append(proc_ref)
        else:
            rdf_fail += 1

    log.info("RDF downloads: %d success, %d failed", len(rdf_ok), rdf_fail)

    # Step 4: Download EUR-Lex validation (batch browser for speed)
    # Only attempt EUR-Lex for procedures with successful RDF downloads
    to_fetch = rdf_ok
    if args.fetch_method == "browser":
        log.info("Using headless Chrome (batch) for EUR-Lex downloads")

    val_ok = []
    val_fail = 0

    if args.fetch_method == "browser":
        # Batch: build URL map for pages that need fetching
        need_fetch = {}
        already_done = []
        for proc_ref in to_fetch:
            out_path = VALIDATION_DIR / f"{proc_ref}.json"
            html_path = VALIDATION_HTML_DIR / f"{proc_ref}.html"
            if not args.force_validation and out_path.exists() and out_path.stat().st_size > 0:
                already_done.append(proc_ref)
            elif not args.force_validation and html_path.exists() and html_path.stat().st_size > 0:
                # Have cached HTML, just need to parse
                already_done.append(proc_ref)
            else:
                need_fetch[proc_ref] = EURLEX_PROCEDURE_URL.format(proc_ref=proc_ref)

        log.info(
            "EUR-Lex: %d already cached, %d to fetch",
            len(already_done), len(need_fetch),
        )

        # Parse already-cached ones
        for proc_ref in already_done:
            result = download_eurlex_validation(
                proc_ref, delay=0, force=False, fetch_method="requests",
            )
            if result is not None:
                val_ok.append(proc_ref)
            else:
                val_fail += 1

        # Batch-fetch the rest with one browser session
        if need_fetch:
            html_results = _fetch_eurlex_browser_batch(need_fetch, delay=args.delay)
            for proc_ref, page_html in html_results.items():
                if page_html is None:
                    log.warning("No HTML returned for %s", proc_ref)
                    val_fail += 1
                    continue

                html_path = VALIDATION_HTML_DIR / f"{proc_ref}.html"
                html_path.write_text(page_html, encoding="utf-8")

                data = _build_validation_data(page_html, proc_ref)
                if data is None:
                    log.warning("No timeline data for %s (likely 404)", proc_ref)
                    val_fail += 1
                    continue

                out_path = VALIDATION_DIR / f"{proc_ref}.json"
                out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
                log.info("EUR-Lex validation: %s (%d events)", proc_ref, data["event_count"])
                val_ok.append(proc_ref)
    else:
        for i, proc_ref in enumerate(to_fetch):
            log.info("EUR-Lex %d/%d: %s", i + 1, len(to_fetch), proc_ref)
            result = download_eurlex_validation(
                proc_ref,
                delay=args.delay,
                force=args.force_validation,
                fetch_method=args.fetch_method,
            )
            if result is not None:
                val_ok.append(proc_ref)
            else:
                val_fail += 1

    log.info(
        "EUR-Lex validation: %d success, %d failed",
        len(val_ok), val_fail,
    )

    # Step 5: Keep only procedures with both RDF and EUR-Lex validation
    # Trim to target if we have more than enough
    final = val_ok[:args.target]
    log.info(
        "Final fixture set: %d (target was %d)",
        len(final), args.target,
    )

    # Clean up RDF/validation files for procedures not in final set
    final_set = set(final)
    for rdf_path in PROCEDURES_DIR.glob("*.rdf"):
        if rdf_path.stem not in final_set:
            rdf_path.unlink()
            log.debug("Removed extra RDF: %s", rdf_path.stem)
    for val_path in VALIDATION_DIR.glob("*.json"):
        if val_path.stem not in final_set:
            val_path.unlink()
            log.debug("Removed extra validation: %s", val_path.stem)

    # Step 6: Build index
    index = build_index(final)
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False))
    log.info("Index written to %s (%d entries)", INDEX_PATH, len(index))

    # Summary
    log.info("--- Summary ---")
    log.info("Procedures with both RDF + EUR-Lex: %d", len(final))
    log.info("Target: %d", args.target)
    log.info("RDF downloads: %d success, %d failed", len(rdf_ok), rdf_fail)
    log.info("EUR-Lex validation: %d success, %d failed", len(val_ok), val_fail)
    log.info("Fixtures directory: %s", FIXTURES_DIR)


if __name__ == "__main__":
    main()
