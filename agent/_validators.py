"""Shared validators for synthetic test fixtures.

Used by:
- `agent/tests/eval/runner.py` for case-load schema validation
- `agent/tools.py` for synthetic patient-fixture PII screening

Per SYNTHETIC_DATA_PLAN.md (approved 2026-05-02), the no-real-PHI validator
is a conservative regex/blocklist pass. Hand-review remains the primary
control for accidental real-name collisions; this is the automated layer
that catches obvious leaks (real-shaped SSNs, phone numbers, non-test
email addresses) before a fixture lands on disk.
"""

from __future__ import annotations

import json
import re
from typing import Any


_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn_dashes": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone_us": re.compile(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b"),
    "email_realish": re.compile(
        r"\b[A-Za-z0-9._%+-]+@(?!example\.|test\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
}


def validate_no_real_pii(fixture: dict[str, Any], source: str) -> None:
    """Regex scan for synthetic patient fixtures.

    Allows `patient@example.com`-style placeholders but flags real-looking
    SSN, phone, and email patterns. Conservative: errs toward false
    positives. Raises ValueError on any match so the offending fixture
    fails to load.
    """
    blob = json.dumps(fixture)
    for label, pattern in _PII_PATTERNS.items():
        match = pattern.search(blob)
        if match:
            raise ValueError(
                f"{source}: synthetic fixture matches PII pattern "
                f"{label!r}: {match.group(0)!r}"
            )
