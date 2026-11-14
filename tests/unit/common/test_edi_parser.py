import json

import pytest

from umsi_x_imetris_corporation_capstone.common import edi_parser

pytestmark = pytest.mark.unit


def test_error_messages_preserves_order_and_repeats_without_raw_values():
    report = {
        "errors": [{"err_str": "Envelope mismatch", "err_val": "private value"}],
        "groups": [
            {"err_str": "Invalid date"},
            {"elements": [{"err_str": "Invalid date"}]},
        ],
        "metadata": [None, 834, "not an error message"],
    }

    assert list(edi_parser.error_messages(report)) == [
        "Envelope mismatch",
        "Invalid date",
        "Invalid date",
    ]


def test_error_messages_ignores_empty_reasons():
    report = {"errors": [{"err_str": ""}, {"err_str": None}], "groups": []}

    assert list(edi_parser.error_messages(report)) == []


def test_validation_success_returns_no_quarantine_reasons(monkeypatch, scenario_file):
    sources = []
    path = scenario_file("employee_termination")
    content = path.read_text(encoding="ascii", newline="")

    def validate(**kwargs):
        sources.append(kwargs["src_file"].read())
        return True

    monkeypatch.setattr(edi_parser, "x12n_document", validate)

    assert edi_parser.validate_edi834(str(path)) == (True, None)
    assert sources == [content]


def test_validation_failure_returns_all_reasons_as_strings(monkeypatch, scenario_file):
    path = scenario_file("employee_termination")
    reasons = ["Control number mismatch", "Invalid maintenance code"]

    def validate(**kwargs):
        json.dump(
            {"errors": [{"err_str": reason} for reason in reasons]},
            kwargs["fd_json"],
        )
        return False

    monkeypatch.setattr(edi_parser, "x12n_document", validate)

    assert edi_parser.validate_edi834(str(path)) == (False, reasons)


def test_validation_does_not_disguise_operational_errors_as_invalid_edi(
    monkeypatch, scenario_file
):
    path = scenario_file("employee_termination")
    error = RuntimeError("validation engine unavailable")

    def validate(**kwargs):
        raise error

    monkeypatch.setattr(edi_parser, "x12n_document", validate)

    with pytest.raises(RuntimeError) as captured:
        edi_parser.validate_edi834(str(path))

    assert captured.value is error


@pytest.mark.parametrize(
    "report, reason",
    [
        (" \n", "EDI validation failed; no report was produced."),
        ('{"errors": []}', "Pyx12 validation failed without diagnostic details."),
    ],
)
def test_validation_failure_without_report_reasons_returns_nonempty_fallback(
    report, reason, monkeypatch, scenario_file
):
    path = scenario_file("employee_termination")

    def validate(**kwargs):
        kwargs["fd_json"].write(report)
        return False

    monkeypatch.setattr(edi_parser, "x12n_document", validate)

    assert edi_parser.validate_edi834(str(path)) == (False, [reason])


def test_totals_count_member_records_even_when_identifiers_repeat(edi834_segments):
    for fields in edi834_segments:
        if fields[:2] == ["REF", "0F"]:
            fields[2] = "111223333"

    assert edi_parser.edi834_totals(edi834_segments) == []


@pytest.mark.parametrize(
    "qualifier, declared, actual", [("TO", "99", 3), ("ET", "7", 1), ("DT", "9", 2)]
)
def test_totals_report_the_incorrect_summary(
    qualifier, declared, actual, edi834_segments
):
    quantity = next(
        fields for fields in edi834_segments if fields[:2] == ["QTY", qualifier]
    )
    quantity[2] = declared

    assert edi_parser.edi834_totals(edi834_segments) == [
        f"ST 0001: QTY {qualifier} declares {declared}; counted {actual} INS records."
    ]


@pytest.mark.parametrize("declared", ["", "sNaN", "-1", "1.5"])
def test_totals_reject_invalid_record_counts(declared, edi834_segments):
    quantity = next(fields for fields in edi834_segments if fields[:2] == ["QTY", "TO"])
    quantity[2] = declared

    errors = edi_parser.edi834_totals(edi834_segments)

    assert len(errors) == 1
    assert "ST 0001: QTY TO" in errors[0]


def test_header_totals_are_optional(edi834_segments):
    segments = [fields for fields in edi834_segments if fields[0] != "QTY"]

    assert edi_parser.edi834_totals(segments) == []


def test_totals_reset_between_transactions_and_identify_the_failing_transaction():
    segments = [
        ["ST", "834", "0001"],
        ["QTY", "TO", "1"],
        ["QTY", "ET", "1"],
        ["QTY", "DT", "0"],
        ["INS", "Y"],
        ["SE"],
        ["ST", "834", "0002"],
        ["QTY", "TO", "2"],
        ["QTY", "ET", "1"],
        ["QTY", "DT", "1"],
        ["INS", "Y"],
        ["INS", "N"],
        ["SE"],
    ]
    assert edi_parser.edi834_totals(segments) == []

    segments[7][2] = "3"

    assert edi_parser.edi834_totals(segments) == [
        "ST 0002: QTY TO declares 3; counted 2 INS records."
    ]


@pytest.mark.parametrize(
    "missing_tag, reason",
    [
        ("GS", "unexpected ST"),
        ("ST", "outside a transaction"),
        ("SE", "unexpected GE"),
        ("GE", "unexpected IEA"),
        ("IEA", "Missing trailer for ISA"),
    ],
)
def test_envelope_boundaries_are_required(missing_tag, reason, edi834_text):
    text = "\n".join(
        line
        for line in edi834_text.splitlines()
        if not line.startswith(missing_tag + "*")
    )

    assert any(reason in error for error in edi_parser.edi834_gap_errors(text))


@pytest.mark.parametrize(
    "tags, reason",
    [
        ({"ISA", "GS", "GE", "IEA"}, "functional group has no transactions"),
        ({"ISA", "IEA"}, "interchange has no functional groups"),
    ],
)
def test_empty_envelopes_are_rejected(tags, reason, edi834_text):
    text = "\n".join(
        line for line in edi834_text.splitlines() if line.split("*", 1)[0] in tags
    )

    assert any(reason in error for error in edi_parser.edi834_gap_errors(text))


def test_gap_errors_collect_composite_and_balancing_reasons(edi834_text):
    text = edi834_text.replace("DMG*D8*19880324*F~", "DMG*D8*19880324*F**:RET~")
    text = text.replace("QTY*TO*3~", "QTY*TO*99~")

    errors = edi_parser.edi834_gap_errors(text)

    assert len(errors) == 2
    assert any("components 02 and 03" in error for error in errors)
    assert any("QTY TO declares 99; counted 3" in error for error in errors)


def test_valid_file_is_parsed_and_never_quarantined(monkeypatch):
    calls = []
    parsed = {"source_file": "new_hire.edi", "data": {"member": "synthetic"}}
    monkeypatch.setattr(edi_parser, "validate_edi834", lambda path: (True, None))

    def parse(path):
        calls.append(("parse", path))
        return parsed

    monkeypatch.setattr(edi_parser, "parse_edi", parse)
    monkeypatch.setattr(
        edi_parser, "quarantine", lambda *args: pytest.fail("Valid file quarantined")
    )

    assert edi_parser.ingest_edi_files("new_hire.edi") is parsed
    assert calls == [("parse", "new_hire.edi")]


def test_invalid_file_is_quarantined_with_all_reasons_without_parsing(monkeypatch):
    reasons = ["Invalid date", "Invalid maintenance code"]
    quarantined = []
    monkeypatch.setattr(edi_parser, "validate_edi834", lambda path: (False, reasons))
    monkeypatch.setattr(
        edi_parser, "parse_edi", lambda path: pytest.fail("Invalid file was parsed")
    )
    monkeypatch.setattr(
        edi_parser,
        "quarantine",
        lambda path, errors: quarantined.append((path, errors)),
    )

    assert edi_parser.ingest_edi_files("formatting_errors.edi") is None
    assert quarantined == [("formatting_errors.edi", reasons)]


@pytest.mark.parametrize(
    "failing_stage", ["validate_edi834", "parse_edi", "quarantine"]
)
def test_ingestion_propagates_failures_so_the_task_can_fail(monkeypatch, failing_stage):
    error = RuntimeError(f"{failing_stage} unavailable")
    monkeypatch.setattr(
        edi_parser,
        "validate_edi834",
        lambda path: (
            (False, ["Invalid date"]) if failing_stage == "quarantine" else (True, None)
        ),
    )
    monkeypatch.setattr(edi_parser, "parse_edi", lambda path: {"data": {}})
    monkeypatch.setattr(edi_parser, "quarantine", lambda *args: None)

    def fail(*args):
        raise error

    monkeypatch.setattr(edi_parser, failing_stage, fail)

    with pytest.raises(RuntimeError) as captured:
        edi_parser.ingest_edi_files("source.edi")
    assert captured.value is error


def test_quarantine_releases_contexts_and_propagates_insert_failure(recording_database):
    recording_database.execute_error = RuntimeError("database write failed")

    with pytest.raises(RuntimeError, match="database write failed"):
        edi_parser.quarantine("source.edi", ["Invalid date"])

    assert recording_database.events == [
        ("cursor_exit", RuntimeError),
        ("connection_exit", RuntimeError),
    ]
