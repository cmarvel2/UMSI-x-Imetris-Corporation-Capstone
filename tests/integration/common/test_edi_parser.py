from collections import Counter
from datetime import UTC, datetime

import pytest

from tests.scenarios import (
    BUSINESS_VALID,
    SCENARIOS,
    corrected_edi,
    members,
    walk_segments,
)
from umsi_x_imetris_corporation_capstone.common import edi_parser

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scenario", BUSINESS_VALID)
def test_business_scenarios_preserve_members_and_segments(
    scenario, scenario_file, recording_database
):
    path = scenario_file(scenario)
    source_segments = [
        line.removesuffix("~").split("*")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    expected_member_ids = [
        fields[2] for fields in source_segments if fields[:2] == ["REF", "0F"]
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
    assert actual_member_ids == [[identifier] for identifier in expected_member_ids]
    assert [member["INS"][0]["INS01"] for member in parsed_members] == [
        fields[1] for fields in source_segments if fields[0] == "INS"
    ]
    assert Counter(seg for seg, _ in walk_segments(result["data"])) == Counter(
        fields[0] for fields in source_segments
    )


def test_parsed_metadata_records_source_and_current_utc_time(scenario_file):
    path = scenario_file("employee_termination")
    before = datetime.now(UTC)

    result = edi_parser.parse_edi(str(path))

    after = datetime.now(UTC)
    ingested_at = datetime.fromisoformat(result["ingested_at"])
    assert result["source_file"] == path.name
    assert ingested_at.utcoffset() == UTC.utcoffset(ingested_at)
    assert before <= ingested_at <= after


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
    header = result["data"]["ISA_LOOP"][0]["GS_LOOP"][0]["ST_LOOP"][0]["HEADER"][0]

    assert header["BGN"][0]["BGN01"] == "22"
    assert header["BGN"][0]["BGN08"] == "4"
    assert members(result)[0]["INS"][0]["INS03"] == "030"


def test_newborn_stays_separate_from_subscriber_with_own_birth_and_coverage_dates(
    scenario_file,
):
    result = edi_parser.parse_edi(str(scenario_file("newborn_addition")))
    subscriber, newborn = members(result)

    assert subscriber["INS"][0]["INS01"] == "Y"
    assert newborn["INS"][0]["INS01"] == "N"
    assert newborn["INS"][0]["INS02"] == "19"
    assert newborn["2100A"][0]["DMG"][0]["DMG02"] == "20261010"
    assert newborn["2300"][0]["DTP"][0]["DTP03"] == "20261010"


def test_family_preserves_all_three_people_and_two_coverages_each(scenario_file):
    result = edi_parser.parse_edi(str(scenario_file("clean_family")))
    family = members(result)

    assert [member["INS"][0]["INS02"] for member in family] == ["18", "01", "19"]
    assert [
        [coverage["HD"][0]["HD03"] for coverage in member["2300"]] for member in family
    ] == [["HLT", "DEN"], ["HLT", "DEN"], ["HLT", "DEN"]]


def test_open_enrollment_preserves_each_family_members_effective_date(scenario_file):
    result = edi_parser.parse_edi(str(scenario_file("open_enrollment_family")))
    family = members(result)

    assert len(family) == 3
    assert [member["2300"][0]["DTP"][0]["DTP03"] for member in family] == [
        "20270101",
        "20270101",
        "20270101",
    ]


@pytest.mark.parametrize("scenario", ["qc_anomalies", "ml_logical_anomalies"])
def test_validation_does_not_apply_unimplemented_business_rules(
    scenario, scenario_file
):
    # This records the current boundary, not a claim that anomalous data is clean.
    path = scenario_file(scenario)

    assert edi_parser.validate_edi834(str(path)) == (True, None)


def test_missing_file_remains_an_operational_failure(tmp_path, recording_database):
    with pytest.raises(FileNotFoundError):
        edi_parser.ingest_edi_files(str(tmp_path / "missing.edi"))

    assert recording_database.calls == []


@pytest.mark.parametrize(
    "content, reason",
    [(b"not an EDI file", "complete ISA"), (b"ISA*\xff", "ASCII encoding")],
)
def test_unreadable_edi_is_quarantined_before_validation_or_parsing(
    content, reason, tmp_path, recording_database, monkeypatch
):
    path = tmp_path / "not_x12.edi"
    path.write_bytes(content)
    monkeypatch.setattr(
        edi_parser,
        "x12n_document",
        lambda **kwargs: pytest.fail("Unreadable EDI reached Pyx12"),
    )
    monkeypatch.setattr(
        edi_parser,
        "parse_edi",
        lambda path: pytest.fail("Unreadable EDI was parsed"),
    )

    assert edi_parser.ingest_edi_files(str(path)) is None
    assert len(recording_database.calls) == 1
    _, (source, reasons) = recording_database.calls[0]
    assert source == str(path)
    assert any(reason in error for error in reasons)


@pytest.mark.parametrize(
    "old, new, expected_reason",
    [
        pytest.param(
            "*^*00501", "*:*00501", "distinct printable", id="delimiter-collision"
        ),
        pytest.param(
            "*^*00501",
            "*U*00501",
            "distinct printable",
            id="legacy-alphanumeric-delimiter",
        ),
        pytest.param("ISA*", "\nISA*", "complete ISA", id="leading-newline"),
        pytest.param("N1*P5*", "N1*\nP5*", "line break inside", id="embedded-newline"),
        pytest.param("~\nGS*", "~~\nGS*", "empty segment", id="empty-segment"),
        pytest.param(
            "IEA*1*000000101~",
            "IEA*1*000000101~TRAILING",
            "Unterminated",
            id="trailing-content",
        ),
        pytest.param(
            "ST*834*", "ST**", "ST01 must be 834", id="missing-transaction-type"
        ),
        pytest.param(
            "ST*834*", "ST*835*", "ST01 must be 834", id="wrong-transaction-type"
        ),
        pytest.param(
            "GS*BE*", "GS*HC*", "GS01 must be BE", id="wrong-functional-group"
        ),
        pytest.param(
            "*00501*", "*00401*", "ISA12 must be 00501", id="wrong-envelope-version"
        ),
        pytest.param(
            "005010X220A1",
            "005010X220A2",
            "GS08 must be 005010X220A1",
            id="wrong-group-guide",
        ),
        pytest.param(
            "ST*834*0001*005010X220A1",
            "ST*834*0001*005010X220A2",
            "ST03 must be 005010X220A1",
            id="wrong-transaction-guide",
        ),
        pytest.param(
            "DMG*D8*19880324*F~",
            "DMG*D8*19880324*F**:RET~",
            "components 02 and 03",
            id="missing-composite-code",
        ),
        pytest.param(
            "DMG*D8*19880324*F~",
            "DMG*D8*19880324*F**::2186-5~",
            "components 02 and 03",
            id="missing-composite-qualifier",
        ),
        pytest.param(
            "DMG*D8*19880324*F~",
            "DMG*D8*19880324*F**:RET:2186-5:EXTRA~",
            "more than 3 components",
            id="excess-composite-components",
        ),
        pytest.param("*SARAH*", "*SARAH^JOAN*", "repetitions", id="nonrepeating-name"),
        pytest.param(
            "QTY*TO*3~",
            "QTY*TO*99~",
            "QTY TO declares 99; counted 3",
            id="member-total",
        ),
        pytest.param(
            "DMG*D8*19880324*F~",
            "DMG*D8*19880324*F**:RET:2186-5^:RET:2131-1~",
            "DMG05: repetitions are unsupported",
            id="valid-repetition-unsupported",
        ),
        pytest.param("SE*35*", "SE*99*", "SE count", id="pyx12-se-count"),
        pytest.param(
            "*261015*",
            "*261332*",
            "without diagnostic details",
            id="pyx12-header-fallback",
        ),
    ],
)
def test_validation_failures_are_quarantined_with_their_reason(
    old, new, expected_reason, edi834_text, tmp_path, recording_database
):
    assert old in edi834_text
    path = tmp_path / "invalid.edi"
    path.write_text(edi834_text.replace(old, new, 1), encoding="ascii")
    assert edi_parser.ingest_edi_files(str(path)) is None

    assert len(recording_database.calls) == 1
    _, (source, reasons) = recording_database.calls[0]
    assert source == str(path)
    assert any(expected_reason in reason for reason in reasons), reasons


@pytest.mark.parametrize("layout", ["ends_dmg", "ends_n4", "amt_and_cob"])
def test_totals_handle_members_without_coverage_and_extra_plan_details(
    layout, edi834_segments, tmp_path, recording_database
):
    if layout in {"ends_dmg", "ends_n4"}:
        segments = [
            fields
            for fields in edi834_segments
            if fields[0] != "HD"
            and not (fields[0] == "DTP" and fields[1] in {"348", "349"})
            and not (layout == "ends_n4" and fields[0] == "DMG")
        ]
    else:
        segments = edi834_segments
        coverage_date = next(
            index
            for index, fields in enumerate(segments)
            if fields[:2] == ["DTP", "348"]
        )
        segments[coverage_date + 1 : coverage_date + 1] = [
            ["AMT", "P3", "250"],
            ["COB", "P", "POLICY123", "1"],
        ]

    text = corrected_edi("\n".join("*".join(fields) + "~" for fields in segments))
    path = tmp_path / f"{layout}.edi"
    path.write_text(text, encoding="ascii")

    result = edi_parser.ingest_edi_files(str(path))

    assert recording_database.calls == []
    assert len(members(result)) == 3
    member = members(result)[0]
    if layout == "amt_and_cob":
        coverage = member["2300"][0]
        assert coverage["AMT"][0]["AMT02"] == "250"
        assert coverage["2320"][0]["COB"][0]["COB02"] == "POLICY123"
    else:
        assert "2300" not in member
        assert ("DMG" in member["2100A"][0]) is (layout == "ends_dmg")
        assert member["2100A"][0]["N4"][0]["N403"] == "48104"


def test_alternative_punctuation_delimiters_are_supported(
    edi834_text, tmp_path, recording_database
):
    text = edi834_text.translate(
        str.maketrans({"*": "|", "^": "!", ":": ">", "~": ";"})
    )
    path = tmp_path / "alternative_delimiters.edi"
    path.write_text(text, encoding="ascii")

    result = edi_parser.ingest_edi_files(str(path))

    assert recording_database.calls == []
    assert len(members(result)) == 3


def test_multiple_interchanges_are_validated_and_preserved(
    edi834_text, scenario_file, tmp_path, recording_database
):
    second = scenario_file("employee_termination").read_text(encoding="ascii")
    path = tmp_path / "multiple_interchanges.edi"
    path.write_text(edi834_text + second, encoding="ascii")

    result = edi_parser.ingest_edi_files(str(path))

    assert recording_database.calls == []
    assert len(result["data"]["ISA_LOOP"]) == 2
    assert [loop["ISA"][0]["ISA13"] for loop in result["data"]["ISA_LOOP"]] == [
        "000000101",
        "000000003",
    ]
