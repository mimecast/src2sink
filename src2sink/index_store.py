"""A persisted fleet index, so a trace queries instead of loading (`OI-15`).

`run_trace` used every repo record in the metabase to answer a question about
one repo. Deserialised JSON runs about 6.5x its size on disk, so a 34 GB fleet
needs roughly 222 GB resident just to be held — and past that the tool does not
run slowly, it does not run.

Reading what a trace actually consults shows it never needs the fleet, only four
things, and every one is keyed by the target repo:

* the target's own record, for its endpoints and sinks — one file
* service-call edges arriving at the target — `collect_service_edges`
* outbound HTTP nodes elsewhere that reference the target — a small subset of
  all nodes, not whole records
* producer-index hits for the target — `build_producer_indices`

So this stores those four during aggregation, which already walks every repo, and
`trace` reads them back by key. Nothing fleet-wide is ever resident.

SQLite because it is in the standard library, is a single file, and gives real
indexes without a server. Rows are streamed rather than fetched into lists, which
is the property that matters: memory stays flat as the fleet grows.

**Staleness is not optional.** An index built from a metabase that has since
changed answers confidently and wrongly, which is worse than being slow. Every
read checks a signature over the record files and the versions that produced
them, and a mismatch falls back to loading rather than serving a stale answer.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .derive import DERIVATION_VERSION
from .graph_common import repo_id
from .schema import SCHEMA_VERSION

INDEX_FILENAME = "index.sqlite3"

# Bump when the tables below change shape. Distinct from DETECTION_VERSION and
# DERIVATION_VERSION: those govern what the records *say*, this governs how the
# index stores it. An older index is discarded rather than migrated — it is a
# cache with an authoritative source on disk, so rebuilding is always correct.
INDEX_VERSION = 1

# Only these two families are consulted when looking for callers of a repo, so
# only these are stored. Keeping the rest out is what makes the table small
# enough to scan without holding the fleet.
_OUTBOUND_FAMILIES = frozenset({"http-out", "api-client-consumer"})

_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Locator only. The record itself stays in its JSON file: a trace reads exactly
-- one, so copying every record into the index would duplicate the fleet to save
-- nothing.
CREATE TABLE repo (
    repo_id   TEXT PRIMARY KEY,
    json_path TEXT NOT NULL
);

CREATE TABLE call_edge (
    source_repo TEXT NOT NULL,
    target_repo TEXT NOT NULL,
    target_path TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    refs        TEXT NOT NULL   -- JSON array
);
CREATE INDEX call_edge_by_target ON call_edge (target_repo);

-- The http-out / api-client-consumer subset, flattened to the fields a trace
-- reads. `target_repo` is set only when a binding resolved it; the rest are
-- matched by scanning `raw`, which is why this table is scanned as well as
-- looked up.
CREATE TABLE outbound_node (
    source_repo          TEXT NOT NULL,
    family               TEXT NOT NULL,
    file                 TEXT NOT NULL,
    line                 TEXT NOT NULL,
    target_repo          TEXT,
    target_repo_evidence TEXT,
    import_name          TEXT,
    client               TEXT,
    raw                  TEXT NOT NULL
);
CREATE INDEX outbound_node_by_target ON outbound_node (target_repo);

CREATE TABLE producer_hit (
    target_repo TEXT NOT NULL,
    source_repo TEXT NOT NULL,
    path        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    ref         TEXT NOT NULL
);
CREATE INDEX producer_hit_by_target ON producer_hit (target_repo);
"""


@dataclass(frozen=True)
class OutboundNode:
    """One outbound HTTP node, flattened to what caller-detection reads."""

    source_repo: str
    family: str
    file: str
    line: str
    target_repo: str | None
    target_repo_evidence: str | None
    import_name: str | None
    client: str | None
    raw: str


@dataclass(frozen=True)
class ProducerHitRow:
    """A producer-index hit, detached from the binding it was found for.

    The binding comes from configuration and is re-read at query time, so
    persisting it would store a copy of config that could drift from the config.
    """

    target_repo: str
    source_repo: str
    path: str
    kind: str
    confidence: str
    evidence: str
    ref: str


def index_path(metabase_root: Path) -> Path:
    """Where the index lives for a given metabase."""
    return metabase_root / INDEX_FILENAME


def fleet_signature(record_paths: list[Path]) -> str:
    """A cheap fingerprint of the records an index was built from.

    Size and mtime rather than content: hashing a 34 GB fleet to decide whether
    a cache is fresh would cost more than the cache saves. The failure mode this
    admits — a record rewritten to the same size within the same mtime tick —
    needs a deliberate effort to produce, and the versions folded in below catch
    every change that comes from the tool itself.
    """
    digest = hashlib.sha256()
    digest.update(f"index={INDEX_VERSION} schema={SCHEMA_VERSION} "
                  f"derivation={DERIVATION_VERSION}\n".encode())
    for path in sorted(record_paths):
        try:
            stat = path.stat()
        except OSError:
            # An unreadable record is itself a change worth invalidating on.
            digest.update(f"{path}\tmissing\n".encode())
            continue
        digest.update(f"{path}\t{stat.st_size}\t{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def outbound_nodes_of(record: dict[str, Any], source_repo: str) -> Iterator[OutboundNode]:
    """Yield the outbound HTTP nodes of one record, flattened for storage."""
    for node in record.get("nodes") or []:
        if node.get("family") not in _OUTBOUND_FAMILIES:
            continue
        detail = node.get("detail") or {}
        yield OutboundNode(
            source_repo=source_repo,
            family=str(node.get("family") or ""),
            file=str(node.get("file") or ""),
            line=str(node.get("line") or ""),
            target_repo=detail.get("target_repo"),
            target_repo_evidence=detail.get("target_repo_evidence"),
            import_name=detail.get("import"),
            client=detail.get("client"),
            raw=str(detail.get("raw") or ""),
        )


class FleetIndex:
    """Read side. Every method is keyed by target repo and streams its rows."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an open connection; use :func:`open_index` to obtain one."""
        self._conn = connection

    def close(self) -> None:
        """Release the connection."""
        self._conn.close()

    def __enter__(self) -> FleetIndex:
        """Support `with open_index(...) as index:`."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the connection however the block exits."""
        self.close()

    def record_path(self, repo_id_value: str) -> Path | None:
        """The JSON file for one repo, or None if the index does not know it."""
        row = self._conn.execute(
            "SELECT json_path FROM repo WHERE repo_id = ?", (repo_id_value,)
        ).fetchone()
        return Path(row[0]) if row else None

    def repo_ids(self) -> list[str]:
        """Every repo the index covers, in sorted order."""
        return [r[0] for r in self._conn.execute(
            "SELECT repo_id FROM repo ORDER BY repo_id"
        )]

    def call_edges_into(self, target_repo: str) -> Iterator[tuple[str, str, str, str, list[str]]]:
        """Edges arriving at one repo: (source, target_path, confidence, evidence, refs)."""
        cursor = self._conn.execute(
            "SELECT source_repo, target_path, confidence, evidence, refs "
            "FROM call_edge WHERE target_repo = ? "
            "ORDER BY source_repo, target_path",
            (target_repo,),
        )
        for source_repo, target_path, confidence, evidence, refs in cursor:
            yield source_repo, target_path, confidence, evidence, json.loads(refs)

    def outbound_nodes(self, *, exclude_repo: str | None = None) -> Iterator[OutboundNode]:
        """Stream every outbound node.

        Scanned rather than looked up because a caller is usually identified by a
        literal inside `raw`, which no key can answer. Streaming keeps this flat
        in memory however large the fleet gets — the ceiling `OI-15` is about.
        """
        sql = (
            "SELECT source_repo, family, file, line, target_repo, "
            "target_repo_evidence, import_name, client, raw FROM outbound_node"
        )
        params: tuple[str, ...] = ()
        if exclude_repo is not None:
            sql += " WHERE source_repo != ?"
            params = (exclude_repo,)
        sql += " ORDER BY source_repo, file, line"
        for row in self._conn.execute(sql, params):
            yield OutboundNode(*row)

    def producer_hits_for(self, target_repo: str) -> Iterator[ProducerHitRow]:
        """Producer-index hits recorded against one repo."""
        cursor = self._conn.execute(
            "SELECT target_repo, source_repo, path, kind, confidence, evidence, ref "
            "FROM producer_hit WHERE target_repo = ? ORDER BY source_repo, kind",
            (target_repo,),
        )
        for row in cursor:
            yield ProducerHitRow(*row)


def _write_meta(conn: sqlite3.Connection, signature: str) -> None:
    """Record what this index was built from, so a reader can reject it."""
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [
            ("index_version", str(INDEX_VERSION)),
            ("schema_version", str(SCHEMA_VERSION)),
            ("derivation_version", str(DERIVATION_VERSION)),
            ("fleet_signature", signature),
        ],
    )


def open_index(metabase_root: Path, record_paths: list[Path]) -> FleetIndex | None:
    """Open the index if it exists and matches ``record_paths``, else None.

    Returning None rather than raising is deliberate: a missing or stale index is
    a cache miss, and the caller falls back to computing from records. The one
    thing that must never happen is serving an answer from an index that no
    longer describes the metabase.
    """
    path = index_path(metabase_root)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        stored = dict(conn.execute("SELECT key, value FROM meta"))
    except sqlite3.Error:
        conn.close()
        return None  # not our schema, or corrupt

    if stored.get("index_version") != str(INDEX_VERSION):
        conn.close()
        return None
    if stored.get("fleet_signature") != fleet_signature(record_paths):
        conn.close()
        return None
    return FleetIndex(conn)


def build_index(
    metabase_root: Path,
    record_paths: list[Path],
    *,
    call_edges: list[Any],
    producer_indices: list[Any],
    records: Iterator[dict[str, Any]],
) -> Path:
    """Write the fleet index. ``records`` is consumed once, as a stream.

    Called from aggregation, which already walks every repo — so the index costs
    one pass that was happening anyway rather than a second walk of the fleet.
    """
    path = index_path(metabase_root)
    path.unlink(missing_ok=True)
    metabase_root.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(_SCHEMA)

        for record in records:
            rid = repo_id(record)
            json_path = record.get("_json_path")
            if json_path:
                conn.execute(
                    "INSERT OR REPLACE INTO repo (repo_id, json_path) VALUES (?, ?)",
                    (rid, str(json_path)),
                )
            conn.executemany(
                "INSERT INTO outbound_node (source_repo, family, file, line, "
                "target_repo, target_repo_evidence, import_name, client, raw) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (n.source_repo, n.family, n.file, n.line, n.target_repo,
                     n.target_repo_evidence, n.import_name, n.client, n.raw)
                    for n in outbound_nodes_of(record, rid)
                ],
            )

        conn.executemany(
            "INSERT INTO call_edge (source_repo, target_repo, target_path, "
            "confidence, evidence, refs) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (e.source_repo, e.target_repo, e.target_path, e.confidence,
                 e.evidence, json.dumps(list(e.refs)))
                for e in call_edges
            ],
        )
        conn.executemany(
            "INSERT INTO producer_hit (target_repo, source_repo, path, kind, "
            "confidence, evidence, ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (index.binding.target_repo, h.source_repo, h.path, h.kind,
                 h.confidence, h.evidence, h.ref)
                for index in producer_indices
                for h in index.hits
            ],
        )

        # Last, and from the same paths the reader will check, so an index that
        # exists is an index that is complete.
        _write_meta(conn, fleet_signature(record_paths))
        conn.commit()
    return path
