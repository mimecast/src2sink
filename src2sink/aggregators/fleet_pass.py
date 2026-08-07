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

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, TypeVar

from ..graph_common import iter_v2_repo_records

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
        """Fold one repo record into the accumulating result."""
        ...

    def result(self) -> T_co:
        """The finished reduction. Called once, after the pass."""
        ...


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
