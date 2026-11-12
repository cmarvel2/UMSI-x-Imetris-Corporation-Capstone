import json
from datetime import UTC, datetime
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


def validate_edi834(filepath: str) -> tuple[bool, None | list[str]]:
    report = StringIO()
    validation_result = x12n_document(
        param=params(),
        src_file=filepath,
        fd_997=None,
        fd_html=None,
        fd_json=report,
    )

    if validation_result:
        return validation_result, None
    elif not report.getvalue().strip():
        return False, ["Edi validation failed, report not found"]
    else:
        errors = json.loads(report.getvalue())
        reasons = list(error_messages(errors))
        return False, reasons


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
                (source_file, reason)
            ) 


def ingest_edi_files(filepath: str) -> dict:
    result, reason = validate_edi834(filepath)
    if result == True:
        return parse_edi(filepath)

    else:
        quarantine(filepath, reason)
