"""Write individual taint catalogue markdown + jsonl artefacts."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .taint_buckets import TaintCatalogueBuckets
from ..renderers.markdown import md_table
from ..sanitize import UNTRUSTED_CONTENT_NOTICE

MAX_MD_ROWS = 500
MAX_PII_MD_BYTES = 4_000_000


def _hierarchical_section(
    title: str,
    intro: str,
    summary_rows: list[list[str]],
    detail_rows: list[list[str]],
    detail_headers: list[str],
    tail_count: int,
    jsonl_path: Path,
) -> list[str]:
    """Build markdown lines for a title + intro + summary table + sampled detail table."""
    md: list[str] = [f"# {title}\n", intro, UNTRUSTED_CONTENT_NOTICE, "\n## Summary\n"]
    md.append(md_table(summary_rows[0], summary_rows[1:]))
    md.append("\n## Detail (sampled)\n")
    md.append(md_table(detail_headers, detail_rows[:MAX_MD_ROWS]))
    if tail_count > 0:
        md.append(f"\n_{tail_count} additional rows — see `{jsonl_path.name}`._\n")
    return md


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write records as newline-delimited JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# Postures a sql sink may report (see extractors.patterns.sql_parameterisation).
# Anything else — including the True/False of a pre-1.2.0 metabase — is reported
# as `unknown` rather than guessed at.
_PARAM_POSTURES = frozenset({"parameterised", "mixed", "raw", "static", "unknown"})


def write_sql_catalogues(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write SQL sources, execution sinks, and legacy sql-sinks stub catalogues."""
    src_by_kind = Counter(r.get("detail", {}).get("pattern", "?") for r in buckets.sql_sources)
    md = _hierarchical_section(
        "SQL sources — string construction patterns",
        "_Heuristic: SQL-shaped strings built via concatenation or interpolation "
        "(not execution sinks). Pair with `sql-execution-sinks.md` for full flow._\n",
        [["Pattern", "Count"], *[[k, str(v)] for k, v in src_by_kind.most_common()]],
        [
            [
                r["repo"],
                r.get("detail", {}).get("pattern", ""),
                f"{r.get('file')}:{r.get('line')}",
                str(r.get("detail", {}).get("snippet", ""))[:100],
            ]
            for r in buckets.sql_sources
        ],
        ["Repo", "Pattern", "File:line", "Snippet"],
        max(0, len(buckets.sql_sources) - MAX_MD_ROWS),
        taint_dir / "sql-sources.jsonl",
    )
    _write_jsonl(taint_dir / "sql-sources.jsonl", buckets.sql_sources)
    (taint_dir / "sql-sources.md").write_text("\n".join(md), encoding="utf-8")

    # `parameterised` is a posture, not a boolean. `mixed` (a placeholder in a
    # statement that is also concatenated) and `unknown` must stay out of the
    # safe-looking bucket — collapsing either into `parameterised` is the claim
    # the posture exists to stop making (OI-7, OI-10).
    sink_param = Counter(
        posture
        if (posture := r.get("detail", {}).get("parameterised")) in _PARAM_POSTURES
        else "unknown"
        for r in buckets.sql_sinks
    )
    md = _hierarchical_section(
        "SQL execution sinks",
        "_Tree-sitter + heuristic JDBC/JPA/native execution call sites "
        "(ORM `find`/`save` excluded)._\n",
        [["Posture", "Count"], *[[k, str(v)] for k, v in sink_param.most_common()]],
        [
            [
                r["repo"],
                r.get("detail", {}).get("symbol", ""),
                f"{r.get('file')}:{r.get('line')}",
                str(r.get("detail", {}).get("raw", ""))[:100],
            ]
            for r in buckets.sql_sinks
        ],
        ["Repo", "Symbol", "File:line", "Call"],
        max(0, len(buckets.sql_sinks) - MAX_MD_ROWS),
        taint_dir / "sql-execution-sinks.jsonl",
    )
    _write_jsonl(taint_dir / "sql-execution-sinks.jsonl", buckets.sql_sinks)
    (taint_dir / "sql-execution-sinks.md").write_text("\n".join(md), encoding="utf-8")

    (taint_dir / "sql-sinks.md").write_text(
        "# SQL sinks (execution)\n\n"
        "_Renamed in v2: see `sql-execution-sinks.md` for JDBC/JPA sinks and "
        "`sql-sources.md` for string-concat sources._\n",
        encoding="utf-8",
    )


def write_file_and_http_catalogues(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write file-write/archive-extraction and outbound HTTP/RPC sink catalogues."""
    by_op = Counter(r.get("detail", {}).get("operation", "?") for r in buckets.file_sinks)
    md = _hierarchical_section(
        "File-write and archive-extraction sinks",
        "_Disk I/O sinks detected across the fleet._\n",
        [["Operation", "Count"], *[[k, str(v)] for k, v in by_op.most_common()]],
        [
            [r["repo"], r.get("detail", {}).get("operation", ""), f"{r.get('file')}:{r.get('line')}"]
            for r in buckets.file_sinks
        ],
        ["Repo", "Operation", "File:line"],
        max(0, len(buckets.file_sinks) - MAX_MD_ROWS),
        taint_dir / "file-sinks.jsonl",
    )
    _write_jsonl(taint_dir / "file-sinks.jsonl", buckets.file_sinks)
    (taint_dir / "file-sinks.md").write_text("\n".join(md), encoding="utf-8")

    md = _hierarchical_section(
        "HTTP / RPC outbound sinks",
        "_Client calls and URL construction (SSRF surface)._\n",
        [["Purpose", "Count"],
         *[[k, str(v)] for k, v in Counter(
             r.get("detail", {}).get("purpose", "?") for r in buckets.http_sinks
         ).most_common()]],
        [
            [r["repo"], r.get("detail", {}).get("purpose", ""),
             f"{r.get('file')}:{r.get('line')}", str(r.get("detail", {}).get("raw", ""))[:80]]
            for r in buckets.http_sinks
        ],
        ["Repo", "Purpose", "File:line", "Raw"],
        max(0, len(buckets.http_sinks) - MAX_MD_ROWS),
        taint_dir / "http-sinks.jsonl",
    )
    _write_jsonl(taint_dir / "http-sinks.jsonl", buckets.http_sinks)
    (taint_dir / "http-sinks.md").write_text("\n".join(md), encoding="utf-8")


def _write_pii_sources(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write the GDPR PII-field source catalogue grouped by classification."""
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in buckets.pii_sources:
        key = r.get("pii_classification") or r.get("data_class") or "unknown"
        by_class[key].append(r)

    pii_md: list[str] = [
        "# PII field sources (GDPR axis)\n",
        "_Identifiers classified under `pii_classification` (`pii-field` nodes "
        "only). Business-data-class and dangerous-payload field names are in "
        "`dangerous-payload-fields.jsonl` — not personal data._\n\n",
        UNTRUSTED_CONTENT_NOTICE,
        "## By classification\n",
        md_table(
            ["Classification", "Occurrences", "Distinct repos"],
            [
                [cls, str(len(recs)), str(len({r["repo"] for r in recs}))]
                for cls, recs in sorted(by_class.items(), key=lambda x: -len(x[1]))
            ],
        ),
        "\n## Sample occurrences (max 500)\n",
        md_table(
            ["Repo", "Field", "PII class", "Data class", "File:line"],
            [
                [
                    r["repo"],
                    r.get("detail", {}).get("field_name", ""),
                    r.get("pii_classification") or "",
                    r.get("data_class") or "",
                    f"{r.get('file')}:{r.get('line')}",
                ]
                for r in buckets.pii_sources[:MAX_MD_ROWS]
            ],
        ),
    ]
    if len(buckets.pii_sources) > MAX_MD_ROWS:
        pii_md.append(f"\n_{len(buckets.pii_sources) - MAX_MD_ROWS} more in pii-sources.jsonl._\n")
    _write_jsonl(taint_dir / "pii-sources.jsonl", buckets.pii_sources)
    body = "\n".join(pii_md)
    if len(body.encode()) > MAX_PII_MD_BYTES:
        body = "\n".join(pii_md[:6]) + "\n\n_(truncated — use pii-sources.jsonl)_\n"
    (taint_dir / "pii-sources.md").write_text(body, encoding="utf-8")


def _write_dangerous_payload_fields(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write the dangerous-payload / business-data-class field-name catalogue."""
    dp_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in buckets.data_class_fields:
        dp_by_class[r.get("data_class") or "unknown"].append(r)
    dp_md: list[str] = [
        "# Dangerous-payload and business-data-class field names\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_`data-class-field` nodes: tenant content, credentials, or **dangerous-payload** "
        "(query/sql/script/expression variables that may carry injection taint — "
        "not GDPR PII). Full stream in `dangerous-payload-fields.jsonl`._\n\n",
        "## By data_class\n",
        md_table(
            ["Class", "Occurrences", "Distinct repos"],
            [
                [cls, str(len(recs)), str(len({r["repo"] for r in recs}))]
                for cls, recs in sorted(dp_by_class.items(), key=lambda x: -len(x[1]))
            ],
        ),
        "\n## Sample (max 500)\n",
        md_table(
            ["Repo", "Field", "Data class", "File:line"],
            [
                [
                    r["repo"],
                    r.get("detail", {}).get("field_name", ""),
                    r.get("data_class") or "",
                    f"{r.get('file')}:{r.get('line')}",
                ]
                for r in buckets.data_class_fields[:MAX_MD_ROWS]
            ],
        ),
    ]
    if len(buckets.data_class_fields) > MAX_MD_ROWS:
        dp_md.append(
            f"\n_{len(buckets.data_class_fields) - MAX_MD_ROWS} more in "
            "dangerous-payload-fields.jsonl._\n",
        )
    _write_jsonl(taint_dir / "dangerous-payload-fields.jsonl", buckets.data_class_fields)
    (taint_dir / "dangerous-payload-fields.md").write_text("\n".join(dp_md), encoding="utf-8")


def _write_pii_sinks(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write the PII storage/logging sink catalogue."""
    md = _hierarchical_section(
        "PII sinks — storage and logging",
        "_`pii-log`: logger calls with a PII field name near the call site. "
        "`pii-storage`: `.save`/`.persist`/S3/email patterns; `field_name` is "
        "set only when a PII token appears within ±120 characters (else "
        "`field_name` is null and `confidence` is low — generic persistence, "
        "not proven PII). See `SCHEMA.md` § `taint/pii-sinks.md`._\n",
        [["Family", "Count"],
         *[[str(k), str(v)] for k, v in Counter(r.get("family") for r in buckets.pii_sinks).most_common()]],
        [
            [
                r["repo"],
                r.get("family", ""),
                r.get("detail", {}).get("field_name", ""),
                f"{r.get('file')}:{r.get('line')}",
            ]
            for r in buckets.pii_sinks
        ],
        ["Repo", "Family", "Field", "File:line"],
        max(0, len(buckets.pii_sinks) - MAX_MD_ROWS),
        taint_dir / "pii-sinks.jsonl",
    )
    _write_jsonl(taint_dir / "pii-sinks.jsonl", buckets.pii_sinks)
    (taint_dir / "pii-sinks.md").write_text("\n".join(md), encoding="utf-8")


def write_pii_catalogues(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write PII sources, dangerous-payload fields, and PII sink catalogues."""
    _write_pii_sources(taint_dir, buckets)
    _write_dangerous_payload_fields(taint_dir, buckets)
    _write_pii_sinks(taint_dir, buckets)


def write_crypto_and_payload_catalogues(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write crypto-operations and raw-code/SQL payload-endpoint catalogues."""
    weak = [r for r in buckets.crypto_ops if r.get("detail", {}).get("weak")]
    md = [
        "# Crypto operations\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Algorithm references from v2 flow nodes._\n\n",
        md_table(
            ["Algorithm", "Count"],
            [[a, str(c)] for a, c in Counter(
                (r.get("detail", {}).get("algorithm") or "?").upper()
                for r in buckets.crypto_ops
            ).most_common(40)],
        ),
        "\n## Weak algorithm uses\n",
        md_table(
            ["Repo", "Algorithm", "File:line"],
            [
                [r["repo"], r.get("detail", {}).get("algorithm", ""), f"{r.get('file')}:{r.get('line')}"]
                for r in weak[:200]
            ],
        ),
    ]
    _write_jsonl(taint_dir / "crypto-operations.jsonl", buckets.crypto_ops)
    (taint_dir / "crypto-operations.md").write_text("\n".join(md), encoding="utf-8")

    md = _hierarchical_section(
        "Raw code / SQL payload endpoints",
        "_Inbound endpoints whose handler accepts a `sql`/`query`/… field and "
        "reaches an execution sink in the same file. Use `trace.py` for cross-repo "
        "producer analysis._\n",
        [["Confidence", "Count"],
         *[[str(k), str(v)] for k, v in Counter(r.get("confidence") for r in buckets.raw_payload).most_common()]],
        [
            [
                r["repo"],
                r.get("detail", {}).get("endpoint_path", ""),
                f"{r.get('file')}:{r.get('line')}",
                r.get("detail", {}).get("sink_symbol", ""),
            ]
            for r in buckets.raw_payload
        ],
        ["Repo", "Endpoint", "Field location", "Sink"],
        max(0, len(buckets.raw_payload) - MAX_MD_ROWS),
        taint_dir / "raw-code-payload-endpoints.jsonl",
    )
    _write_jsonl(taint_dir / "raw-code-payload-endpoints.jsonl", buckets.raw_payload)
    (taint_dir / "raw-code-payload-endpoints.md").write_text("\n".join(md), encoding="utf-8")

    # The outbound dual: this repo *sends* SQL rather than accepting it. Written
    # as its own catalogue because the reader is different — one asks "what can
    # be injected into my service", the other "where does my service ship
    # executable input to someone else" (OI-9).
    md = _hierarchical_section(
        "Outbound SQL payloads",
        "_Outbound requests carrying a `sql`/`dql`/… field in the body. The "
        "far end of the hop that `raw-code-payload-endpoints` records from the "
        "receiving side._\n",
        [["Confidence", "Count"],
         *[[str(k), str(v)] for k, v in Counter(r.get("confidence") for r in buckets.sql_payload_out).most_common()]],
        [
            [
                r["repo"],
                r.get("detail", {}).get("field_name", ""),
                r.get("detail", {}).get("path", ""),
                r.get("detail", {}).get("target_repo", "") or "?",
                f"{r.get('file')}:{r.get('line')}",
            ]
            for r in buckets.sql_payload_out
        ],
        ["Repo", "Field", "Path", "Target", "Location"],
        max(0, len(buckets.sql_payload_out) - MAX_MD_ROWS),
        taint_dir / "sql-payload-out.jsonl",
    )
    _write_jsonl(taint_dir / "sql-payload-out.jsonl", buckets.sql_payload_out)
    (taint_dir / "sql-payload-out.md").write_text("\n".join(md), encoding="utf-8")


def write_config_catalogues(taint_dir: Path, buckets: TaintCatalogueBuckets) -> None:
    """Write config data-store, security-key, and (optional) crypto-config catalogues."""
    store_by_vendor = Counter(r.get("detail", {}).get("vendor", "?") for r in buckets.config_stores)
    md = _hierarchical_section(
        "Data stores referenced in configuration",
        "_JDBC / MongoDB / Redis / S3 bucket references from YAML, "
        "properties, Helm values, and `.env`._\n",
        [["Vendor", "Count"], *[[k, str(v)] for k, v in store_by_vendor.most_common()]],
        [
            [
                r["repo"],
                r.get("detail", {}).get("vendor", ""),
                str(r.get("detail", {}).get("url") or r.get("detail", {}).get("bucket", ""))[:80],
                f"{r.get('file')}:{r.get('line')}",
            ]
            for r in buckets.config_stores
        ],
        ["Repo", "Vendor", "Target", "File:line"],
        max(0, len(buckets.config_stores) - MAX_MD_ROWS),
        taint_dir / "config-data-stores.jsonl",
    )
    _write_jsonl(taint_dir / "config-data-stores.jsonl", buckets.config_stores)
    (taint_dir / "config-data-stores.md").write_text("\n".join(md), encoding="utf-8")

    md = _hierarchical_section(
        "Security-sensitive configuration keys",
        "_Same key set as v1 `config_security_flags`, as v2 flow nodes._\n",
        [["Key", "Count"],
         *[[k, str(v)] for k, v in Counter(
             r.get("detail", {}).get("key", "?") for r in buckets.config_security
         ).most_common(30)]],
        [
            [r["repo"], r.get("detail", {}).get("key", ""), f"{r.get('file')}:{r.get('line')}"]
            for r in buckets.config_security
        ],
        ["Repo", "Key", "File:line"],
        max(0, len(buckets.config_security) - MAX_MD_ROWS),
        taint_dir / "config-security.jsonl",
    )
    _write_jsonl(taint_dir / "config-security.jsonl", buckets.config_security)
    (taint_dir / "config-security.md").write_text("\n".join(md), encoding="utf-8")

    if not buckets.crypto_config:
        return
    md = [
        "# Crypto settings from configuration\n",
        UNTRUSTED_CONTENT_NOTICE,
        "_Algorithm / cipher suite keys in config (config-driven crypto)._ \n\n",
        md_table(
            ["Repo", "Subkind", "File:line"],
            [
                [r["repo"], r.get("detail", {}).get("subkind", ""), f"{r.get('file')}:{r.get('line')}"]
                for r in buckets.crypto_config[:MAX_MD_ROWS]
            ],
        ),
    ]
    _write_jsonl(taint_dir / "config-crypto.jsonl", buckets.crypto_config)
    (taint_dir / "config-crypto.md").write_text("\n".join(md), encoding="utf-8")
