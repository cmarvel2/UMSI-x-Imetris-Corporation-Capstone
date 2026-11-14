import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

from pyx12.error_handler import errh_null
from pyx12.params import params
from pyx12.x12context import X12ContextReader
from pyx12.x12n_document import x12n_document

from umsi_x_imetris_corporation_capstone.common.database import database_connection


def error_messages(value):
    if isinstance(value, dict):
        if value.get("err_str"):
            yield value["err_str"]

        for child in value.values():
            yield from error_messages(child)

    elif isinstance(value, list):
        for child in value:
            yield from error_messages(child)


def edi834_totals(segments: list[list[str]]) -> list[str]:
    """Call after envelope checks; missing QTY totals remain optional."""
    errors = []
    counts = {}
    quantities = []
    in_header = False
    transaction = ""

    for fields in segments:
        tag = fields[0]

        if tag == "ST":
            transaction = fields[2] if len(fields) > 2 else ""
            counts = {"TO": 0, "ET": 0, "DT": 0}
            quantities = []
            in_header = True

        elif tag == "INS":
            in_header = False
            counts["TO"] += 1

            indicator = fields[1] if len(fields) > 1 else ""

            if indicator == "Y":
                counts["ET"] += 1
            elif indicator == "N":
                counts["DT"] += 1

        elif tag == "QTY" and in_header and len(fields) > 1:
            if fields[1] in counts:
                quantities.append((fields[1], fields[2] if len(fields) > 2 else ""))

        elif tag == "SE":
            for qualifier, raw in quantities:
                try:
                    total = Decimal(raw)
                except InvalidOperation:
                    errors.append(f"ST {transaction}: QTY {qualifier} is not numeric.")
                    continue

                if not total.is_finite() or total != counts[qualifier]:
                    errors.append(
                        f"ST {transaction}: QTY {qualifier} declares {raw}; "
                        f"counted {counts[qualifier]} INS records."
                    )

    return errors


def edi834_gap_errors(text: str) -> list[str]:
    if len(text) < 106 or not text.startswith("ISA"):
        return ["File must begin with a complete ISA segment."]

    element, repetition, component, terminator = (
        text[3],
        text[82],
        text[104],
        text[105],
    )

    delimiters = (element, repetition, component, terminator)

    if len(set(delimiters)) != 4 or any(
        char.isalnum() or not 33 <= ord(char) <= 126 for char in delimiters
    ):
        return ["ISA delimiters must be distinct printable punctuation."]

    chunks = text.split(terminator)

    if chunks[-1].strip("\r\n"):
        return ["Unterminated content remains after the last segment."]

    raw_segments = [chunk.lstrip("\r\n") for chunk in chunks[:-1]]

    if any(not raw for raw in raw_segments):
        return ["File contains an empty segment."]

    segments = [raw.split(element) for raw in raw_segments]

    parents = {
        "ISA": [],
        "GS": ["ISA"],
        "ST": ["ISA", "GS"],
    }

    trailers = {"SE": "ST", "GE": "GS", "IEA": "ISA"}

    required_headers = {
        "ISA": {12: "00501"},
        "GS": {1: "BE", 8: "005010X220A1"},
        "ST": {1: "834", 3: "005010X220A1"},
    }

    isa_separators = (
        3,
        6,
        17,
        20,
        31,
        34,
        50,
        53,
        69,
        76,
        81,
        83,
        89,
        99,
        101,
        103,
    )

    stack = []
    groups = transactions = 0
    errors = []

    for number, (raw, fields) in enumerate(zip(raw_segments, segments), 1):
        tag = fields[0]

        if "\r" in raw or "\n" in raw:
            return [f"Segment {number}: line break inside a segment."]

        if tag == "ISA" and (
            len(raw) != 105
            or any(raw[index] != element for index in isa_separators)
            or raw[82] != repetition
            or raw[104] != component
        ):
            return [
                (
                    f"Segment {number}: malformed ISA or changed delimiters; "
                    "this pipeline requires consistent delimiters."
                )
            ]

        if tag in parents:
            if stack != parents[tag]:
                return [f"Segment {number}: unexpected {tag}."]

            stack.append(tag)

            if tag == "ISA":
                groups = 0
            elif tag == "GS":
                groups += 1
                transactions = 0
            else:
                transactions += 1

        elif tag in trailers:
            if not stack or stack[-1] != trailers[tag]:
                return [f"Segment {number}: unexpected {tag}."]

            if tag == "GE" and transactions == 0:
                return [f"Segment {number}: functional group has no transactions."]

            if tag == "IEA" and groups == 0:
                return [f"Segment {number}: interchange has no functional groups."]

            stack.pop()

        elif tag == "TA1" and stack == ["ISA"]:
            continue

        elif stack != ["ISA", "GS", "ST"]:
            return [f"Segment {number}: {tag} is outside a transaction."]

        for index, expected in required_headers.get(tag, {}).items():
            if len(fields) <= index or fields[index] != expected:
                errors.append(f"Segment {number}: {tag}{index:02d} must be {expected}.")

        if tag == "ISA":
            continue

        for index, value in enumerate(fields[1:], 1):
            location = f"Segment {number}, {tag}{index:02d}"

            if repetition in value:
                errors.append(
                    f"{location}: repetitions are unsupported by the current "
                    "parser; this is a pipeline limitation."
                )

            if tag == "DMG" and index == 5:
                for occurrence in value.split(repetition):
                    parts = occurrence.split(component)

                    if len(parts) > 3:
                        errors.append(f"{location}: more than 3 components.")

                    qualifier = parts[1] if len(parts) > 1 else ""
                    code = parts[2] if len(parts) > 2 else ""

                    if bool(qualifier) != bool(code):
                        errors.append(
                            f"{location}: components 02 and 03 must appear together."
                        )

    if stack:
        return [f"Missing trailer for {stack[-1]}."]

    return errors + edi834_totals(segments)


def validate_edi834(filepath: str) -> tuple[bool, list[str] | None]:
    try:
        with open(filepath, encoding="ascii", newline="") as source:
            text = source.read()
    except UnicodeDecodeError:
        return False, ["EDI input must use ASCII encoding."]

    errors = edi834_gap_errors(text)

    if errors:
        return False, errors

    report = StringIO()

    with StringIO(text) as source:
        valid = x12n_document(
            param=params(),
            src_file=source,
            fd_997=None,
            fd_html=None,
            fd_json=report,
        )

    if valid:
        return True, None

    if not report.getvalue().strip():
        return False, ["EDI validation failed; no report was produced."]

    errors = list(error_messages(json.loads(report.getvalue())))

    return False, errors or ["Pyx12 validation failed without diagnostic details."]


def parse_edi(file_path: str) -> dict:
    root = {}
    active = []

    with open(file_path, "r", encoding="utf-8") as source:
        reader = X12ContextReader(params(), errh_null(), source)

        for node in reader.iter_segments():
            segment = node.seg_data
            seg_id = segment.get_seg_id()
            path = node.cur_path.strip("/").split("/")[:-1]
            starts = {loop.id for loop in node.start_loops}

            if seg_id in {"ISA", "GS", "ST"}:
                starts.add(f"{seg_id}_LOOP")

            common = 0
            while (
                common < min(len(active), len(path))
                and active[common][0] == path[common]
                and path[common] not in starts
            ):
                common += 1

            del active[common:]
            parent = active[-1][1] if active else root

            for loop_id in path[common:]:
                instance = {}
                parent.setdefault(loop_id, []).append(instance)
                active.append((loop_id, instance))
                parent = instance

            parent.setdefault(seg_id, []).append(
                {
                    f"{seg_id}{index:02d}": (
                        [part.get_value() for part in element.elements]
                        if element.is_composite()
                        else element.get_value()
                    )
                    for index, element in enumerate(segment.elements, 1)
                }
            )

    return {
        "source_file": Path(file_path).name,
        "ingested_at": datetime.now(UTC).isoformat(),
        "data": root,
    }


def quarantine(source_file: str, reason):
    with database_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
                INSERT INTO quarantine_table (
                    source_file, quarantine_reason
                )
                VALUES (%s, %s)
                """,
            (source_file, reason),
        )


def ingest_edi_files(filepath: str) -> dict:
    result, reason = validate_edi834(filepath)
    if result == True:
        return parse_edi(filepath)

    else:
        quarantine(filepath, reason)
