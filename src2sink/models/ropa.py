"""GDPR Article 30 ROPA projection — Phase 3."""

from __future__ import annotations

import dataclasses
from typing import Any

# Map v2 `pii_classification` to Article 30 category labels.
ROPA_CATEGORY_BY_CLASSIFICATION: dict[str, str] = {
    "direct-pii": "Contact and identity data",
    "sensitive": "Financial and government identifiers",
    "special-category-gdpr": "Special category personal data",
    "quasi-id": "Online identifiers and technical data",
    "unknown": "Unclassified personal data",
}

# Worked-example field keys for privacy narrative (prefer phone over email).
ROPA_SHOWCASE_FIELDS = frozenset({"phone", "email", "ip_address"})


@dataclasses.dataclass
class RopaProcessingActivity:
    """One processing activity row (repo × ROPA category)."""

    category: str
    pii_classification: str
    repo: str
    purposes: list[str]
    data_subjects: str
    recipients: str
    retention_hint: str
    security_measures: list[str]
    field_keys: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return the processing activity as a plain dict."""
        return dataclasses.asdict(self)
