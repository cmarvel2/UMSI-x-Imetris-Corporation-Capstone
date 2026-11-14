"""PostgreSQL tests use a session-local quarantine table and roll back."""

from uuid import UUID

import pytest

from umsi_x_imetris_corporation_capstone.common import edi_parser

pytestmark = pytest.mark.integration


def test_quarantine_round_trips_reason_array_and_database_defaults(postgres_connection):
    source = "incoming/O'Brien'); DROP TABLE quarantine_table; --.edi"
    reasons = [
        "Invalid date",
        "Comma, quote ' and Unicode é",
        "Repeated reason",
        "Repeated reason",
    ]

    edi_parser.quarantine(source, reasons)

    rows = postgres_connection.execute(
        "SELECT quarantine_id, quarantined_at, source_file, quarantine_reason, "
        "resolution_status, resolved_at FROM quarantine_table"
    ).fetchall()
    assert len(rows) == 1
    identifier, timestamp, stored_source, stored_reasons, status, resolved_at = rows[0]
    assert isinstance(identifier, UUID)
    assert timestamp.tzinfo is not None
    assert stored_source == source
    assert stored_reasons == reasons
    assert status == "unresolved"
    assert resolved_at is None


def test_invalid_edi_flows_from_pyx12_into_real_quarantine_table(
    postgres_connection, scenario_file
):
    path = scenario_file("formatting_errors")

    assert edi_parser.ingest_edi_files(str(path)) is None

    rows = postgres_connection.execute(
        "SELECT source_file, quarantine_reason FROM quarantine_table"
    ).fetchall()
    assert len(rows) == 1
    source, reasons = rows[0]
    assert source == str(path)
    assert any("invalid date" in reason for reason in reasons)
    assert any("Maintenance Type Code" in reason for reason in reasons)


def test_valid_edi_is_parsed_without_a_database_quarantine_row(
    postgres_connection, scenario_file
):
    path = scenario_file("employee_termination")

    result = edi_parser.ingest_edi_files(str(path))

    assert result["source_file"] == path.name
    assert postgres_connection.execute(
        "SELECT count(*) FROM quarantine_table"
    ).fetchone() == (0,)
