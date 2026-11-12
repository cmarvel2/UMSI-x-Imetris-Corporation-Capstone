"""Unit tests of ingestion routing and PostgreSQL boundary calls."""

import pytest

from umsi_x_imetris_corporation_capstone.common import database, edi_parser

pytestmark = pytest.mark.unit


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


def test_quarantine_binds_file_and_reason_array_without_interpolating_sql(
    recording_database,
):
    source = "incoming/O'Brien'); DROP TABLE quarantine_table; --.edi"
    reasons = ["Invalid date", "Unexpected 'quote', comma; and Unicode: é"]

    edi_parser.quarantine(source, reasons)

    assert len(recording_database.calls) == 1
    statement, parameters = recording_database.calls[0]
    assert "INSERT INTO quarantine_table" in statement
    assert statement.count("%s") == 2
    assert source not in statement
    assert all(reason not in statement for reason in reasons)
    assert parameters == (source, reasons)
    assert recording_database.events == [
        "connection_enter",
        "cursor_enter",
        ("cursor_exit", None),
        ("connection_exit", None),
    ]


def test_quarantine_releases_contexts_and_propagates_insert_failure(recording_database):
    recording_database.execute_error = RuntimeError("database write failed")

    with pytest.raises(RuntimeError, match="database write failed"):
        edi_parser.quarantine("source.edi", ["Invalid date"])

    assert recording_database.events[-2:] == [
        ("cursor_exit", RuntimeError),
        ("connection_exit", RuntimeError),
    ]


@pytest.mark.parametrize("host, port", [("localhost", "5433"), ("db", "5432")])
def test_connection_factory_uses_local_or_container_configuration(
    monkeypatch, host, port
):
    for key, value in {
        "POSTGRES_HOST": host,
        "POSTGRES_PORT": port,
        "POSTGRES_USER": "test_user",
        "POSTGRES_PASSWORD": "synthetic_password",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []

    def connect(**kwargs):
        connection = object()
        calls.append((kwargs, connection))
        return connection

    monkeypatch.setattr(database.psycopg, "connect", connect)

    first = database.database_connection()
    second = database.database_connection()

    assert first is not second
    assert len(calls) == 2
    for arguments, _ in calls:
        assert arguments == {
            "dbname": "healthcare_edi",
            "host": host,
            "port": port,
            "user": "test_user",
            "password": "synthetic_password",
        }


def test_missing_database_configuration_fails_before_connecting(monkeypatch):
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.setattr(
        database.psycopg,
        "connect",
        lambda **kwargs: pytest.fail("Unexpected connection"),
    )

    with pytest.raises(KeyError, match="POSTGRES_HOST"):
        database.database_connection()
