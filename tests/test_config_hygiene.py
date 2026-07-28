"""B6 — config-load observability and CI log hygiene (threat-model I-2, I-3).

TA-008: a malformed/missing sensitive api-clients file surfaces a warning and
loads 0 bindings, without echoing the file's contents into the log.
TA-009: a per-repo extraction failure is recorded as the exception *type* + repo
id only — never a message that could carry paths or scanned content.
"""

from __future__ import annotations

import logging

from src2sink import build_metabase_v2
from src2sink.build_metabase_v2 import process_one_v2
from src2sink.known_api_clients import load_api_client_bindings

SECRET_MARKER = "TOPSECRET_INTERNAL_SERVICE_NAME"


def test_load_api_clients_warns_on_malformed_without_leaking(tmp_path, caplog):
    path = tmp_path / "api-clients.json"
    # Malformed JSON that also contains a sensitive-looking string.
    path.write_text('{"bindings": [ "' + SECRET_MARKER + '" not json', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_api_client_bindings(path, warn=True)
    assert result == ()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    # The warning names the file but never echoes its contents.
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "api-clients.json" in blob
    assert SECRET_MARKER not in blob


def test_load_api_clients_warns_when_no_bindings_list(tmp_path, caplog):
    path = tmp_path / "api-clients.json"
    path.write_text('{"not_bindings": []}', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_api_client_bindings(path, warn=True)
    assert result == ()
    assert any("no 'bindings' list" in r.getMessage() for r in caplog.records)


def test_load_api_clients_quiet_by_default(tmp_path, caplog):
    # Per-worker loads (warn=False) must stay silent even on failure.
    path = tmp_path / "missing.json"
    with caplog.at_level(logging.WARNING):
        result = load_api_client_bindings(path)  # warn defaults to False
    assert result == ()
    assert caplog.records == []


def test_load_api_clients_logs_count_on_success(tmp_path, caplog):
    path = tmp_path / "api-clients.json"
    path.write_text(
        '{"bindings": [{"target_repo": "g/r", "maven_artifact": "r-client", '
        '"import_prefix": "com.example.r"}]}',
        encoding="utf-8",
    )
    with caplog.at_level(logging.INFO):
        result = load_api_client_bindings(path, warn=True)
    assert len(result) == 1
    assert any("loaded 1 api-client" in r.getMessage() for r in caplog.records)


def test_process_one_v2_error_record_has_no_message(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise ValueError(f"parse failed at /secret/path/{SECRET_MARKER}")

    monkeypatch.setattr(build_metabase_v2, "analyse_repo_v2", _boom)
    repo = tmp_path / "repo"
    repo.mkdir()
    result = process_one_v2((repo, "grp", "repo", tmp_path / "mb", True))
    assert result["_error"] is True
    assert result["error"] == "ValueError"  # type only, no message
    assert SECRET_MARKER not in repr(result)
    assert "/secret/path" not in repr(result)
