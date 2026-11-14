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
    """Correct ISA11, SE counts, and dependent NM1 qualifiers in test copies."""
    segments = [segment.strip() for segment in content.split("~") if segment.strip()]
    transaction_start = None
    for index, segment in enumerate(segments):
        if segment.startswith("ISA*"):
            fields = segment.split("*")
            fields[11] = "^"
            segments[index] = "*".join(fields)
        elif segment.startswith("NM1*03*"):
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


def walk_segments(node):
    for key, entries in node.items():
        for entry in entries:
            if f"{key}01" in entry:
                yield key, entry
            else:
                yield from walk_segments(entry)


def members(document):
    return document["data"]["ISA_LOOP"][0]["GS_LOOP"][0]["ST_LOOP"][0]["DETAIL"][0][
        "2000"
    ]
