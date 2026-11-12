"""All test configuration stays here; no application settings are modified."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.scenarios import FIXTURES, corrected_edi

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "unit: isolated logic; Pyx12 and database boundaries are replaced"
    )
    config.addinivalue_line(
        "markers",
        "integration: real EDI files and Pyx12; database cases use the application's POSTGRES_* settings",
    )


@pytest.fixture
def scenario_file(tmp_path):
    def write(name, *, corrected=False):
        content = (FIXTURES / f"{name}.edi").read_text(encoding="utf-8")
        if corrected:
            content = corrected_edi(content)
        path = tmp_path / f"{name}{'_corrected' if corrected else ''}.edi"
        path.write_text(content, encoding="utf-8")
        return path

    return write


@pytest.fixture
def recording_database(monkeypatch):
    """A boundary fake; these unit tests do not claim real DB coverage."""
    from umsi_x_imetris_corporation_capstone.common import edi_parser

    state = SimpleNamespace(calls=[], events=[], execute_error=None)

    class Cursor:
        def __enter__(self):
            state.events.append("cursor_enter")
            return self

        def execute(self, statement, parameters):
            state.calls.append((statement, parameters))
            if state.execute_error is not None:
                raise state.execute_error

        def __exit__(self, exc_type, exc_value, traceback):
            state.events.append(("cursor_exit", exc_type))
            return False

    class Connection:
        def __enter__(self):
            state.events.append("connection_enter")
            return self

        def cursor(self):
            return Cursor()

        def __exit__(self, exc_type, exc_value, traceback):
            state.events.append(("connection_exit", exc_type))
            return False

    monkeypatch.setattr(edi_parser, "database_connection", Connection)
    return state


@pytest.fixture
def postgres_connection(monkeypatch):
    """Use prototype settings from the environment or .env, with rollback."""
    from umsi_x_imetris_corporation_capstone.common import edi_parser
    from umsi_x_imetris_corporation_capstone.common.database import database_connection

    # Read only PostgreSQL settings. Exported values take priority over .env.
    settings = {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5433"}
    environment_file = PROJECT_ROOT / ".env"
    if environment_file.is_file():
        for line in environment_file.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or key not in {
                "POSTGRES_HOST",
                "POSTGRES_PORT",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
            }:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
                value = value[1:-1]
            else:
                value = value.split(" #", 1)[0].rstrip()
            settings[key] = value
    for key, value in settings.items():
        if key not in os.environ:
            monkeypatch.setenv(key, value)

    migration = (
        PROJECT_ROOT / "db" / "migrations" / "V1__create_quarantine_table.sql"
    ).read_text(encoding="utf-8")
    create_statement = "CREATE TABLE IF NOT EXISTS quarantine_table"
    assert migration.count(create_statement) == 1, (
        "Review test setup for the new migration"
    )
    temporary_migration = migration.replace(
        create_statement, "CREATE TEMP TABLE quarantine_table", 1
    )

    # Missing application settings or an unavailable database fail rather than skip.
    with (
        database_connection() as connection,
        connection.transaction(force_rollback=True),
    ):
        # The session-local table shadows any permanent table of this name.
        connection.execute(temporary_migration)

        @contextmanager
        def borrow_test_connection():
            with connection.transaction():
                yield connection

        monkeypatch.setattr(edi_parser, "database_connection", borrow_test_connection)
        yield connection
