"""Source-document metadata and narrowly corrected test copies."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "edi834"
SCENARIOS = {
    item["id"]: item
    for item in json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))
}
BUSINESS_VALID = [name for name, case in SCENARIOS.items() if case["business_valid"]]


def corrected_edi(content: str) -> str:
    """Correct only source SE counts and dependent NM1 qualifiers for Pyx12.

    Dates, ZIP codes, IDs, relationships, maintenance codes, and coverage values
    remain unchanged. The original files under fixtures are never modified.
    """
    segments = [segment.strip() for segment in content.split("~") if segment.strip()]
    transaction_start = None
    for index, segment in enumerate(segments):
        if segment.startswith("NM1*03*"):
            segments[index] = segment.replace("NM1*03*", "NM1*IL*", 1)
        elif segment.startswith("ST*"):
            transaction_start = index
        elif segment.startswith("SE*"):
            if transaction_start is None:
                raise ValueError("Test fixture contains SE without ST")
            fields = segment.split("*")
            fields[1] = str(index - transaction_start + 1)
            segments[index] = "*".join(fields)
            transaction_start = None
    return "\n".join(segment + "~" for segment in segments) + "\n"


def walk_segments(value):
    """Yield (segment_id, elements) pairs from the application's nested output."""
    if not isinstance(value, dict):
        return
    for key, entries in value.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and f"{key}01" in entry:
                yield key, entry
            else:
                yield from walk_segments(entry)


def transaction(document):
    return document["data"]["ISA_LOOP"][0]["GS_LOOP"][0]["ST_LOOP"][0]


def members(document):
    return transaction(document)["DETAIL"][0]["2000"]
