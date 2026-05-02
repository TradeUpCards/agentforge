"""Unit tests for the Langfuse PHI mask.

The mask runs at the SDK serialization boundary — it filters the copy of
inputs/outputs being sent to Langfuse Cloud, NOT what the LLM, verifier,
or user sees. Tests here only cover the date-bucketing transformation;
the broader "mask doesn't break the agent" property is covered by the
existing eval suite (which exercises the full pipeline with real dates).

Per DECISIONS.md §4a: this is the week-1 first cut. Year-month bucketing
removes day-precision PHI per HIPAA Safe Harbor §3 while keeping enough
trace signal to debug agent behavior. Broader scrubbing (names, MRNs,
free-text date detection) is documented as deferred work.
"""

from __future__ import annotations

from agent.agent import _bucket_dates_in_string, _mask_phi


def test_iso_date_buckets_to_year_month() -> None:
    assert _bucket_dates_in_string("HbA1c 7.8 on 2026-03-15") == "HbA1c 7.8 on 2026-03"


def test_us_slash_date_buckets_to_year_month() -> None:
    assert _bucket_dates_in_string("Visit on 03/15/2026") == "Visit on 2026-03"


def test_us_dash_date_buckets_to_year_month() -> None:
    assert _bucket_dates_in_string("Visit on 03-15-2026") == "Visit on 2026-03"


def test_multiple_dates_in_same_string_all_bucketed() -> None:
    s = "A1c 7.8 (2026-03-15), up from 6.8 (2025-12-10)"
    expected = "A1c 7.8 (2026-03), up from 6.8 (2025-12)"
    assert _bucket_dates_in_string(s) == expected


def test_year_month_date_passes_through_unchanged() -> None:
    # Already at the bucket granularity — leave alone.
    assert _bucket_dates_in_string("Visit in 2026-03") == "Visit in 2026-03"


def test_year_only_passes_through_unchanged() -> None:
    # Year-only is HIPAA Safe Harbor compliant for events.
    assert _bucket_dates_in_string("Started in 2024") == "Started in 2024"


def test_non_date_digit_groups_left_alone() -> None:
    # Lab values, ratios, IDs that look date-shaped should not be touched
    # because they don't match the YYYY-MM-DD / MM/DD/YYYY shapes.
    assert _bucket_dates_in_string("BP 120/80") == "BP 120/80"
    assert _bucket_dates_in_string("LOINC 4548-4") == "LOINC 4548-4"
    assert _bucket_dates_in_string("pid 92") == "pid 92"


def test_mask_walks_dict_recursively() -> None:
    data = {
        "input": "Visit on 2026-03-15",
        "metadata": {
            "patient_id": 92,
            "first_seen": "2024-08-12",
        },
    }
    result = _mask_phi(data)
    assert result["input"] == "Visit on 2026-03"
    assert result["metadata"]["patient_id"] == 92  # ints untouched
    assert result["metadata"]["first_seen"] == "2024-08"


def test_mask_walks_list_recursively() -> None:
    data = [
        {"date": "2026-03-15", "value": 7.8},
        {"date": "2025-12-10", "value": 6.8},
    ]
    result = _mask_phi(data)
    assert result[0]["date"] == "2026-03"
    assert result[1]["date"] == "2025-12"
    assert result[0]["value"] == 7.8


def test_mask_handles_nested_records_blob() -> None:
    # Realistic shape — a `retrieved_records` entry with a date in fields.
    data = {
        "retrieved_records": [
            {
                "table": "procedure_result",
                "record_id": "5421",
                "fields": {
                    "name": "HbA1c",
                    "value": "7.8",
                    "date": "2026-03-15",
                },
            }
        ]
    }
    result = _mask_phi(data)
    assert result["retrieved_records"][0]["fields"]["date"] == "2026-03"
    assert result["retrieved_records"][0]["fields"]["value"] == "7.8"
    assert result["retrieved_records"][0]["record_id"] == "5421"


def test_mask_returns_unchanged_on_unhandled_type() -> None:
    # Numbers, bools, None pass through.
    assert _mask_phi(42) == 42
    assert _mask_phi(3.14) == 3.14
    assert _mask_phi(None) is None
    assert _mask_phi(True) is True


def test_mask_does_not_mutate_input_dict() -> None:
    original = {"date": "2026-03-15"}
    _mask_phi(original)
    # Original should be untouched — mask returns a new dict.
    assert original["date"] == "2026-03-15"
