import os
from pathlib import Path

import pytest

from senpilot_agent.models import DocumentRequest, DocumentType
from senpilot_agent.uarb import UarbClient, record_from_fields, selection_key


def test_exhibit_identifiers_and_restricted_visibility_are_preserved():
    record = record_from_fields(
        ["Application", "04/07/2025", "Public", "H-1", ".pdf"], DocumentType.EXHIBITS
    )
    assert record.document_id == "H-1"
    assert record.title == "Application"
    private = record_from_fields(
        ["Protected filing", "05/22/2025", "Confidential", "H-4(C)-ii", ".pdf"],
        DocumentType.EXHIBITS,
    )
    assert private.document_id == "H-4(C)-ii"
    assert not private.public
    spaced = record_from_fields(
        ["Affidavit", "08/18/2025", "Public", "A -5", ".pdf"],
        DocumentType.EXHIBITS,
        identifier="A -5",
    )
    assert spaced.document_id == "A -5"


def test_unrecognized_identity_is_not_silently_treated_as_a_verified_id():
    record = record_from_fields(["Recording", "04/07/2025", ".mp3"], DocumentType.RECORDINGS)
    assert record.document_id.startswith("unverified-")


def test_selection_prefers_newest_date_then_natural_source_id():
    records = [
        record_from_fields(["Title", date, "Public", number], DocumentType.OTHER_DOCUMENTS)
        for date, number in [
            ("01/01/2025", "999"),
            ("06/10/2026", "102329"),
            ("06/10/2026", "102330"),
        ]
    ]
    assert [r.document_id for r in sorted(records, key=selection_key, reverse=True)] == [
        "102330",
        "102329",
        "999",
    ]


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE_BOARD") != "1", reason="Public-site access is opt-in")
@pytest.mark.parametrize(
    "matter,category",
    [
        ("M12205", DocumentType.EXHIBITS),
        ("M12383", DocumentType.KEY_DOCUMENTS),
        ("M12205", DocumentType.TRANSCRIPTS),
    ],
)
def test_live_matter_download(tmp_path: Path, matter, category):
    client = UarbClient(browser_channel=os.getenv("BROWSER_CHANNEL") or None, max_documents=1)
    try:
        result = client.fetch(
            DocumentRequest(matter_number=matter, document_type=category), tmp_path
        )
        assert result.snapshot.metadata["Matter No"] == matter
        assert not result.snapshot.inventory_errors
        assert result.snapshot.unique_total is not None
        if result.snapshot.category_counts[category]:
            assert len(result.files) == 1
            assert result.files[0].path.stat().st_size > 0
            assert not result.failures
    finally:
        client.close()
