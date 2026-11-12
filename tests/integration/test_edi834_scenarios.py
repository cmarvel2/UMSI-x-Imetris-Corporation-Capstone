"""Real-file/Pyx12 integration; only the database boundary is replaced."""

from collections import Counter
from datetime import UTC, datetime

import pytest

from tests.scenarios import (
    BUSINESS_VALID,
    SCENARIOS,
    members,
    transaction,
    walk_segments,
)
from umsi_x_imetris_corporation_capstone.common import edi_parser

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_supplied_scenarios_follow_actual_validation_and_quarantine_route(
    scenario, scenario_file, recording_database
):
    case = SCENARIOS[scenario]
    path = scenario_file(scenario)

    result = edi_parser.ingest_edi_files(str(path))

    if case["raw_valid"]:
        assert result["source_file"] == path.name
        assert result["data"]["ISA_LOOP"][0]["ISA"][0]["ISA12"] == "00501"
        assert recording_database.calls == []
    else:
        assert result is None
        assert len(recording_database.calls) == 1
        _, (source, reasons) = recording_database.calls[0]
        assert source == str(path)
        assert reasons and all(isinstance(reason, str) for reason in reasons)
        for fragment in case["raw_error_fragments"]:
            assert any(fragment in reason for reason in reasons), reasons


@pytest.mark.parametrize("scenario", BUSINESS_VALID)
def test_corrected_valid_business_scenarios_preserve_members_and_identifiers(
    scenario, scenario_file, recording_database
):
    path = scenario_file(scenario, corrected=True)
    source = path.read_text(encoding="utf-8")
    expected_member_ids = [
        segment.split("*")[2]
        for line in source.splitlines()
        if (segment := line.removesuffix("~")).startswith("REF*0F*")
    ]

    result = edi_parser.ingest_edi_files(str(path))

    assert result is not None
    assert recording_database.calls == []
    parsed_members = members(result)
    assert len(parsed_members) == SCENARIOS[scenario]["members"]
    actual_member_ids = [
        [
            values["REF02"]
            for seg, values in walk_segments(member)
            if seg == "REF" and values["REF01"] == "0F"
        ]
        for member in parsed_members
    ]
    # Exactly one identifier per member prevents accidental merging of loops.
    assert actual_member_ids == [[identifier] for identifier in expected_member_ids]
    assert [member["INS"][0]["INS01"] for member in parsed_members] == [
        line.split("*")[1] for line in source.splitlines() if line.startswith("INS*")
    ]


@pytest.mark.parametrize("scenario", BUSINESS_VALID)
def test_corrected_scenarios_keep_every_segment_and_metadata(scenario, scenario_file):
    path = scenario_file(scenario, corrected=True)
    source_ids = [
        line.split("*", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    ]
    before = datetime.now(UTC)

    result = edi_parser.parse_edi(str(path))

    after = datetime.now(UTC)
    assert result["source_file"] == path.name
    ingested_at = datetime.fromisoformat(result["ingested_at"])
    assert ingested_at.utcoffset() == UTC.utcoffset(ingested_at)
    assert before <= ingested_at <= after
    assert Counter(seg for seg, _ in walk_segments(result["data"])) == Counter(
        source_ids
    )


def test_termination_keeps_maintenance_codes_and_coverage_end_date(scenario_file):
    result = edi_parser.parse_edi(str(scenario_file("employee_termination")))
    member = members(result)[0]

    assert member["INS"][0]["INS03"] == "024"
    coverage = member["2300"][0]
    assert coverage["HD"][0]["HD01"] == "024"
    assert coverage["DTP"][0] == {
        "DTP01": "349",
        "DTP02": "D8",
        "DTP03": "20261031",
    }


def test_address_change_keeps_updated_demographics(scenario_file):
    result = edi_parser.parse_edi(str(scenario_file("address_change")))
    member = members(result)[0]
    name = member["2100A"][0]

    assert member["INS"][0]["INS03"] == "001"
    assert name["N3"][0]["N301"] == "888 NEW HORIZON WAY"
    assert name["N4"][0]["N401"] == "DEARBORN"
    assert name["N4"][0]["N403"] == "48120"


def test_staggered_coverage_dates_stay_with_their_own_products(scenario_file):
    result = edi_parser.parse_edi(str(scenario_file("staggered_coverage")))
    coverages = members(result)[0]["2300"]

    assert [(loop["HD"][0]["HD03"], loop["DTP"][0]["DTP03"]) for loop in coverages] == [
        ("HLT", "20261101"),
        ("DEN", "20261201"),
    ]


def test_audit_batch_preserves_replacement_intent_without_applying_it(scenario_file):
    result = edi_parser.parse_edi(str(scenario_file("full_replacement")))
    header = transaction(result)["HEADER"][0]

    assert header["BGN"][0]["BGN01"] == "22"
    assert header["BGN"][0]["BGN08"] == "4"
    assert members(result)[0]["INS"][0]["INS03"] == "030"


def test_newborn_stays_separate_from_subscriber_with_own_birth_and_coverage_dates(
    scenario_file,
):
    result = edi_parser.parse_edi(
        str(scenario_file("newborn_addition", corrected=True))
    )
    subscriber, newborn = members(result)

    assert subscriber["INS"][0]["INS01"] == "Y"
    assert newborn["INS"][0]["INS01"] == "N"
    assert newborn["INS"][0]["INS02"] == "19"
    assert newborn["2100A"][0]["DMG"][0]["DMG02"] == "20261010"
    assert newborn["2300"][0]["DTP"][0]["DTP03"] == "20261010"


def test_family_preserves_all_three_people_and_two_coverages_each(scenario_file):
    result = edi_parser.parse_edi(str(scenario_file("clean_family", corrected=True)))
    family = members(result)

    assert [member["INS"][0]["INS02"] for member in family] == ["18", "01", "19"]
    assert [
        [coverage["HD"][0]["HD03"] for coverage in member["2300"]] for member in family
    ] == [["HLT", "DEN"], ["HLT", "DEN"], ["HLT", "DEN"]]


def test_open_enrollment_preserves_each_family_members_effective_date(scenario_file):
    result = edi_parser.parse_edi(
        str(scenario_file("open_enrollment_family", corrected=True))
    )
    family = members(result)

    assert len(family) == 3
    assert [member["2300"][0]["DTP"][0]["DTP03"] for member in family] == [
        "20270101",
        "20270101",
        "20270101",
    ]


def test_formatting_errors_are_reported_after_unrelated_segment_count_is_corrected(
    scenario_file, recording_database
):
    path = scenario_file("formatting_errors", corrected=True)

    result = edi_parser.ingest_edi_files(str(path))

    assert result is None
    _, (source, reasons) = recording_database.calls[0]
    assert source == str(path)
    assert any("invalid date" in reason for reason in reasons)
    assert any("Maintenance Type Code" in reason for reason in reasons)
    assert not any("SE count" in reason for reason in reasons)


@pytest.mark.parametrize("scenario", ["qc_anomalies", "ml_logical_anomalies"])
def test_syntax_validation_alone_does_not_detect_future_business_anomalies(
    scenario, scenario_file
):
    # This records the current boundary, not a claim that anomalous data is clean.
    path = scenario_file(scenario, corrected=True)

    assert edi_parser.validate_edi834(str(path)) == (True, None)


def test_missing_file_remains_an_operational_failure(tmp_path, recording_database):
    with pytest.raises(FileNotFoundError):
        edi_parser.ingest_edi_files(str(tmp_path / "missing.edi"))

    assert recording_database.calls == []


def test_unrecognizable_file_is_quarantined_with_a_nonempty_reason(
    tmp_path, recording_database, monkeypatch
):
    path = tmp_path / "not_x12.edi"
    path.write_text("This is not an X12 document.\n", encoding="utf-8")
    monkeypatch.setattr(
        edi_parser, "parse_edi", lambda path: pytest.fail("Unrecognizable file was parsed")
    )

    assert edi_parser.ingest_edi_files(str(path)) is None
    assert len(recording_database.calls) == 1
    _, (source, reasons) = recording_database.calls[0]
    assert source == str(path)
    assert reasons and all(isinstance(reason, str) and reason.strip() for reason in reasons)
