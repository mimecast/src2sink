"""Precision tests for data-class-field / dangerous-payload heuristics."""

from __future__ import annotations

from src2sink.extractors.unified import extract_from_file


def test_expression_java_type_not_data_class_field(tmp_path) -> None:
    src = tmp_path / "HyperscanRegexSearcher.java"
    src.write_text(
        """
        package com.example.regex;
        import java.util.List;
        class HyperscanRegexSearcher {
            private final List<Expression> termsToSearch = new ArrayList<>();
            Expression expression = new Expression(term, options);
        }
        """,
        encoding="utf-8",
    )
    nodes, _ = extract_from_file(
        repo_id="g/svc",
        rel_path="com/example/regex/HyperscanRegexSearcher.java",
        language="java",
        source=src.read_text(encoding="utf-8"),
    )
    organization = [n for n in nodes if n.family == "data-class-field"]
    fields = {(n.detail or {}).get("field_name") for n in organization}
    assert "Expression" not in fields


def test_lowercase_expression_variable_is_data_class_field(tmp_path) -> None:
    src = tmp_path / "Runner.java"
    src.write_text(
        """
        class Runner {
            void run() {
                String expression = userInput.getFilter();
                eval(expression);
            }
        }
        """,
        encoding="utf-8",
    )
    nodes, _ = extract_from_file(
        repo_id="g/svc",
        rel_path="Runner.java",
        language="java",
        source=src.read_text(encoding="utf-8"),
    )
    assert any(
        n.family == "data-class-field"
        and (n.detail or {}).get("field_name") == "expression"
        and n.data_class == "dangerous-payload"
        for n in nodes
    )


def test_html_script_builder_skipped(tmp_path) -> None:
    rel = "src/main/java/com/example/htmlPages/AccountResource.java"
    src = tmp_path / "AccountResource.java"
    src.write_text(
        """
        class AccountResource {
            String build() {
                StringBuilder script = new StringBuilder();
                script.append("<script language=\\"javascript\\">");
                return script.toString();
            }
        }
        """,
        encoding="utf-8",
    )
    nodes, _ = extract_from_file(
        repo_id="g/svc",
        rel_path=rel,
        language="java",
        source=src.read_text(encoding="utf-8"),
    )
    assert not any(
        n.family == "data-class-field" and (n.detail or {}).get("field_name") == "script"
        for n in nodes
    )
