"""Probe the live ClinPGx/PharmGKB chemical API with drug-name queries.

Run from the repository root:

    python3 -m shared.term_normalization.drugs.try_clinpgx_api warfarin Coumadin

Set ``PYTHONPATH=packages/shared`` when the shared package is not installed.
When no terms are provided, a small set of canonical names, aliases, and noisy
inputs is queried to make the API's matching behavior easy to inspect.
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.clinpgx.org/v1/data/chemical"
DEFAULT_TERMS = [
    "warfarin",
    "Coumadin",
    "warfarine",
    "acetaminophen",
    "Tylenol",
]


def query_chemical(term: str, timeout: float = 10) -> tuple[int, dict[str, Any]]:
    """Query the chemical endpoint and return its HTTP status and JSON body."""
    url = f"{API_URL}?{urlencode({'name': term})}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "curl/8.0 autogkb-term-normalization-probe/0.1",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        try:
            body = json.load(error)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"status": "error", "message": error.reason}
        return error.code, body


def summarize(term: str, status: int, body: dict[str, Any]) -> dict[str, Any]:
    """Reduce an API response to fields relevant to term normalization."""
    matches = body.get("data", [])
    if not isinstance(matches, list):
        matches = []

    return {
        "query": term,
        "http_status": status,
        "matches": [
            {
                "id": match.get("id"),
                "name": match.get("name"),
                "types": match.get("types", []),
            }
            for match in matches
        ],
        "raw_response": body,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", nargs="*", help="Drug names to query")
    parser.add_argument(
        "--timeout", type=float, default=10, help="Request timeout in seconds"
    )
    args = parser.parse_args()

    terms = args.terms or DEFAULT_TERMS
    exit_code = 0

    for term in terms:
        try:
            status, body = query_chemical(term, timeout=args.timeout)
            print(json.dumps(summarize(term, status, body), indent=2))
        except (TimeoutError, URLError) as error:
            exit_code = 1
            print(
                json.dumps(
                    {"query": term, "error": str(error)},
                    indent=2,
                )
            )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
