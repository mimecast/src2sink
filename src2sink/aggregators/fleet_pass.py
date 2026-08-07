"""One streamed pass over the metabase, shared by every aggregator (`OI-41`).

Aggregation parsed the whole metabase **fourteen times per run** — 2.2 GB each on
the observed fleet, so ~31 GB of JSON decoded to read the same bytes over and
over. Measured A/B, that repetition is **67% of aggregation time**, and
aggregation is 78% of the run.

The one-line fix is the wrong line. Memoising the load removes the repetition and
keeps one *held* copy of the fleet where re-parsing created and discarded:

```
peak RSS, 14 parses  : 148 MB
peak RSS, 1 parse    : 266 MB     (+118 MB on a 29 MB metabase)
```

At the measured ~6.5x expansion that is several more GB resident on a fleet whose
aggregation already peaks at 5.75 GiB, on a host observed swapping — `OI-15`'s
ceiling, reached through the fix.

So: **stream once, and reduce as you go.** Each collector sees every record in
turn and retains only its own reduced state, which is orders smaller than the
records it came from. Time is one parse; memory is one record plus the results.

Every aggregator's `_collect_*` was already a pure `for data in records:`
reduction, so a collector is that loop turned inside out — accumulators in
`__init__`, body in `consume`, return in `result`. The conversion is mechanical,
and `tests/test_aggregate_output_golden.py` pins all 27 generated artefacts, so
one that changes any output fails immediately.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from ..graph_common import iter_v2_repo_records, load_v2_repo_records

# Covariant: a collector only ever *produces* its result type, so a
# `RecordCollector[QueueGraph]` is usable wherever a `RecordCollector[object]`
# is expected.
T_co = TypeVar("T_co", covariant=True)


class RecordCollector(Protocol[T_co]):
    """A reduction over the fleet that sees one record at a time.

    The contract is deliberately narrow: `consume` may not retain the record it
    is given, because retaining records is the memory cost this exists to avoid.
    It retains whatever it derives from them instead.
    """

    def consume(self, record: dict[str, Any]) -> None:
        """Fold one repo record into the accumulating result.

        Must not retain ``record``: holding records is the memory cost this
        whole mechanism exists to avoid.
        """

    def result(self) -> T_co:
        """The finished reduction. Called once, after the pass."""


def run_fleet_pass(
    metabase_root: Path,
    collectors: Iterable[RecordCollector[Any]],
    *,
    json_paths: list[Path] | None = None,
) -> None:
    """Stream every record once, offering it to every collector.

    The ordering guarantee matters: records arrive in the same order
    `load_v2_repo_records` produced them, so a collector whose output depends on
    record order — several sort only at the end — is unaffected by the move.
    """
    live = list(collectors)
    if not live:
        return
    for record in iter_v2_repo_records(metabase_root, json_paths=json_paths):
        for collector in live:
            collector.consume(record)


class MapCollector(Generic[T_co]):
    """Applies a per-record function and keeps the results, in record order.

    Several aggregators reduce with a plain `[fn(data) for data in records]` —
    one card per repo. They share this rather than each restating the loop, and
    it keeps the derived cards while the records that produced them are released.
    """

    def __init__(self, fn: Callable[[dict[str, Any]], T_co]) -> None:
        """Collect ``fn`` applied to every record."""
        self._fn = fn
        self._results: list[T_co] = []

    def consume(self, record: dict[str, Any]) -> None:
        """Map one record and keep only what came back."""
        self._results.append(self._fn(record))

    def result(self) -> list[T_co]:
        """The mapped results, in the order the records arrived."""
        return self._results


def records_or_load(
    records: list[dict[str, Any]] | None,
    metabase_root: Path,
    json_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """Return the caller's records, loading the fleet only if it supplied none.

    Every converted aggregator keeps a non-streaming entry point so it still
    works when called directly. Writing that fallback as a guard clause inside
    each `write_*` added a branch to functions already at the complexity limit,
    for no reader benefit — the interesting code is below it.
    """
    if records is not None:
        return records
    return load_v2_repo_records(metabase_root, json_paths=json_paths)
