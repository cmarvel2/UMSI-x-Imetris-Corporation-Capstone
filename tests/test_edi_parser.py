"""Unit tests of diagnostic extraction and the Pyx12 adapter, without I/O."""

import json

import pytest

from umsi_x_imetris_corporation_capstone.common import edi_parser

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "level", ["interchanges", "groups", "transactions", "segments", "elements"]
)
def test_error_messages_finds_errors_at_each_report_level(level):
    report = {level: [{"errors": [{"err_str": "Required element missing"}]}]}

    assert list(edi_parser.error_messages(report)) == ["Required element missing"]


def test_error_messages_preserves_order_and_repeats_without_raw_values():
    report = {
        "errors": [{"err_str": "Envelope mismatch", "err_val": "private value"}],
        "groups": [
            {
                "transactions": [
                    {
                        "segments": [
                            {"errors": [{"err_str": "Invalid date"}]},
                            {"elements": [{"errors": [{"err_str": "Invalid date"}]}]},
                        ]
                    }
                ]
            }
        ],
        "metadata": [None, 834, "not an error message"],
    }

    assert list(edi_parser.error_messages(report)) == [
        "Envelope mismatch",
        "Invalid date",
        "Invalid date",
    ]


@pytest.mark.parametrize("report", [{}, {"errors": []}, [None, 0, "text"]])
def test_error_messages_handles_reports_without_errors(report):
    assert list(edi_parser.error_messages(report)) == []


def test_validation_success_returns_no_quarantine_reasons(monkeypatch):
    calls = []

    def validate(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(edi_parser, "x12n_document", validate)

    assert edi_parser.validate_edi834("new_hire.edi") == (True, None)
    assert calls[0]["src_file"] == "new_hire.edi"
    assert calls[0]["fd_997"] is None
    assert calls[0]["fd_html"] is None
    assert calls[0]["fd_json"].getvalue() == ""


def test_validation_failure_returns_all_reasons_as_strings(monkeypatch):
    def validate(**kwargs):
        json.dump(
            {
                "interchanges": [
                    {
                        "errors": [{"err_str": "Control number mismatch"}],
                        "groups": [
                            {"errors": [{"err_str": "Invalid maintenance code"}]}
                        ],
                    }
                ]
            },
            kwargs["fd_json"],
        )
        return False

    monkeypatch.setattr(edi_parser, "x12n_document", validate)

    assert edi_parser.validate_edi834("invalid.edi") == (
        False,
        ["Control number mismatch", "Invalid maintenance code"],
    )


def test_validation_does_not_disguise_operational_errors_as_invalid_edi(monkeypatch):
    def validate(**kwargs):
        raise FileNotFoundError("source is missing")

    monkeypatch.setattr(edi_parser, "x12n_document", validate)

    with pytest.raises(FileNotFoundError, match="source is missing"):
        edi_parser.validate_edi834("missing.edi")
