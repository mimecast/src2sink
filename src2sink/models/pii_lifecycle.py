"""PII / Business data-class lifecycle model — Phase 3."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from ..sanitize import for_markdown

# GDPR-oriented lifecycle stages (static analysis; not runtime provenance).
LIFECYCLE_STAGES = (
    "collect",
    "process",
    "store",
    "transmit",
    "log",
    "encrypt",
    "delete",
)

# Canonical field keys for fleet aggregation (phone-first for worked examples).
FIELD_ALIASES: dict[str, str] = {
    "phonenumber": "phone",
    "phone_number": "phone",
    "telephone": "phone",
    "mobile": "phone",
    "emailaddress": "email",
    "e_mail": "email",
    "firstname": "first_name",
    "lastname": "last_name",
    "fullname": "full_name",
    "dateofbirth": "date_of_birth",
    "birthdate": "date_of_birth",
    "ipaddress": "ip_address",
    "useragent": "user_agent",
}


def normalize_field_key(field_name: str | None) -> str:
    """Canonicalise a field name to a snake_case alias key for fleet aggregation."""
    if not field_name:
        return "unknown"
    key = re.sub(r"([a-z])([A-Z])", r"\1_\2", field_name).lower()
    key = key.replace("-", "_")
    return FIELD_ALIASES.get(key, key)


@dataclasses.dataclass
class PiiTouchpoint:
    """A single point where a PII field is handled at one lifecycle stage."""

    repo: str
    stage: str
    family: str
    field_key: str
    field_name: str
    pii_classification: str
    data_class: str | None
    file: str
    line: int
    confidence: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the touchpoint as a plain dict."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class FieldLifecycleAggregate:
    """Rolled-up lifecycle coverage for one field key across the fleet."""

    field_key: str
    pii_classification: str
    repos_by_stage: dict[str, set[str]] = dataclasses.field(
        default_factory=lambda: {s: set() for s in LIFECYCLE_STAGES}
    )
    touchpoints: int = 0
    sample_refs: list[str] = dataclasses.field(default_factory=list)

    def add(self, touch: PiiTouchpoint, *, max_samples: int = 5) -> None:
        """Fold a touchpoint into the aggregate, keeping up to max_samples refs."""
        self.touchpoints += 1
        self.repos_by_stage.setdefault(touch.stage, set()).add(touch.repo)
        if len(self.sample_refs) < max_samples:
            # touch.file is an untrusted scanned-repo path; neutralise it before it
            # goes into the code-span (SAST finding 1) so it can't break the span
            # or inject Markdown when rendered as a bullet.
            ref = f"{touch.repo} `{for_markdown(touch.file, max_len=120)}:{touch.line}` ({touch.stage})"
            if ref not in self.sample_refs:
                self.sample_refs.append(ref)
