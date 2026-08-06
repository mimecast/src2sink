"""OI-21: every way untrusted input enters a service, not just HTTP.

`HTTP_IN_RX` is keyed per HTTP framework, so an entry point was recognised only
if it was an HTTP annotation. A queue consumer, a gRPC service, a GraphQL
resolver, a scheduled job, a CLI argument — none of them counted, and some were
already being extracted for other reasons without anyone calling them a front
door.

This gates `OI-17`. Reachability computed from an incomplete entry-point set
produces confident, *incomplete* answers: "no path from any entrypoint" when the
entrypoint was a `@KafkaListener` nobody could see. That is worse than no answer,
because it looks like a clean result.

Entry points are **derived**, not extracted. What counts as a front door is a
classification over observations, so the list can grow without re-parsing the
fleet — the property `docs/plans/observe-then-classify.md` exists to give.
"""

from __future__ import annotations

import pytest

from src2sink.derive import derive_from_observations, is_derived
from src2sink.extractors.unified import extract_from_file


def _nodes(source: str, language: str = "java", rel_path: str = "src/A.java"):
    return extract_from_file(
        repo_id="g/r", rel_path=rel_path, language=language, source=source
    )[0]


def _entry_points(source: str, **kw):
    return [n for n in _nodes(source, **kw) if n.family == "entry-point"]


def test_an_http_endpoint_is_an_entry_point():
    """The mechanism that already worked, now stated rather than implied."""
    eps = _entry_points("""
    @RestController
    public class Api {
        @PostMapping("/stock")
        public String submit(@RequestBody String body) { return "ok"; }
    }
    """)
    assert [(e.detail["mechanism"], e.detail["channel"]) for e in eps] == [
        ("http", "/stock"),
    ]


def test_a_queue_consumer_is_an_entry_point():
    """Already extracted as `queue-sub`, never treated as a front door.

    This is the case that would have made `OI-17` answer "no path" for a whole
    class of service while looking certain about it.
    """
    eps = _entry_points("""
    @Service
    public class Consumer {
        @KafkaListener(topics = "stock-updates")
        public void onMessage(String payload) { }
    }
    """)
    assert [(e.detail["mechanism"], e.detail["channel"]) for e in eps] == [
        ("queue", "stock-updates"),
    ]


@pytest.mark.parametrize(
    ("source", "mechanism"),
    [
        ("""
        @GrpcService
        public class StockService extends StockGrpc.StockImplBase {
            public void getStock(StockRequest req, StreamObserver<StockReply> obs) { }
        }
        """, "grpc"),
        ("""
        @Controller
        public class StockResolver {
            @QueryMapping
            public Stock stockByRef(@Argument String ref) { return null; }
        }
        """, "graphql"),
        ("""
        @Component
        public class Job {
            @Scheduled(cron = "0 0 * * * *")
            public void sweep() { }
        }
        """, "schedule"),
    ],
)
def test_the_other_mechanisms_are_recognised(source, mechanism):
    """Each is a way in that the HTTP-only test could not see."""
    assert [e.detail["mechanism"] for e in _entry_points(source)] == [mechanism]


def test_a_scheduled_job_is_not_externally_triggered():
    """A cron job is a front door, but nobody outside chooses when it opens.

    It carries no untrusted input *by that route*, so a reachability answer must
    be able to tell it apart from an endpoint an attacker can call.
    """
    scheduled = _entry_points("""
    @Component
    public class Job {
        @Scheduled(fixedRate = 60000)
        public void sweep() { }
    }
    """)
    assert scheduled[0].detail["externally_triggered"] is False

    http = _entry_points("""
    @RestController
    public class Api { @GetMapping("/x") public String x() { return "ok"; } }
    """)
    assert http[0].detail["externally_triggered"] is True


def test_a_python_cli_entry_point_is_recognised():
    """`argparse` is how a batch job takes its input, and it was invisible."""
    eps = _entry_points("""
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter")
    args = parser.parse_args()
""", language="python", rel_path="src/cli.py")
    assert [e.detail["mechanism"] for e in eps] == ["cli"]


def test_entry_points_are_derived_not_extracted():
    """The definition of a front door must be revisable without a rescan.

    `OI-21`'s list will grow — every new framework is another mechanism — and
    growing it should cost a re-derive over records, not a fleet re-parse.
    """
    source = """
    @Service
    public class Consumer {
        @KafkaListener(topics = "stock-updates")
        public void onMessage(String payload) { }
    }
    """
    nodes = _nodes(source)
    observed = [n for n in nodes if not is_derived(n)]
    assert [n.family for n in observed if n.family == "entry-point"] == []

    derived, _ = derive_from_observations(observed)
    assert [n.detail["mechanism"] for n in derived if n.family == "entry-point"] == [
        "queue",
    ]


def test_a_queue_producer_is_not_an_entry_point():
    """Direction matters: publishing is an exit, not a way in."""
    eps = _entry_points("""
    @Service
    public class Publisher {
        void send() { kafkaTemplate.send("stock-updates", payload); }
    }
    """)
    assert eps == []
