# -*- coding: utf-8 -*-

"""
The semantic layer against a fictional widget factory.

Never this dataset's real metrics: a test asserting on ``sla_breach_rate``
becomes a lie the moment the dataset is swapped.
"""

import sqlalchemy as sa
import pytest

from agent_app import content_check as cc
from agent_app.semantic_layer.api import (
    CompileError,
    compile_metric,
    load_registry,
    replay,
)

FILES = {
    "metrics/defect_rate.yml": """
name: defect_rate
display_name: Defect Rate
description: Share of shipped widgets returned as defective.
base_table: widget w
joins:
  - LEFT JOIN widget_return wr ON wr.widget_id = w.widget_id
formula: 1.0 * SUM(CASE WHEN wr.return_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*)
filters:
  - w.shipped_at IS NOT NULL
time_column: w.built_at
closed_time_column: w.shipped_at
window_end_cap: 2026-03-01
synonyms: [defects, returns]
replay:
  time_window: q1
  expected: 0.5
  tolerance: 0.001
""",
    "metrics/plant_count.yml": """
name: plant_count
display_name: Plant Count
description: Number of plants.
base_table: plant p
formula: COUNT(*)
""",
    "dimensions/line.yml": """
name: line
display_name: Assembly Line
expression: l.line_code
alias: l
joins:
  w: JOIN assembly_line l ON l.line_id = w.line_id
synonyms: [lines]
""",
    "dimensions/month.yml": """
name: month
display_name: Month
expression: "SUBSTR({time_column}, 1, 7)"
synonyms: [monthly]
""",
    "dimensions/inspector_name.yml": """
name: inspector_name
display_name: Inspector Name
expression: i.full_name
alias: i
joins:
  w: JOIN inspector i ON i.inspector_id = w.inspector_id
source_column: inspector.full_name
sensitive: true
""",
    "time_windows/q1.yml": """
name: q1
display_name: First Quarter
start: 2026-01-01
end: 2026-04-01
synonyms: [first quarter]
""",
    "time_windows/shipped_march.yml": """
name: shipped_march
display_name: Shipped in March
start: 2026-03-01
end: 2026-04-01
anchor: closed
""",
    "glossary/core.yml": """
- term: widget
  definition: The thing the factory builds.
""",
}


@pytest.fixture
def layer_dir(tmp_path):
    for rel, text in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (tmp_path / "VERSION").write_text("2.1.0\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(layer_dir):
    return load_registry(layer_dir)


def test_load(registry):
    assert registry.version == "2.1.0"
    assert set(registry.metrics) == {"defect_rate", "plant_count"}
    assert registry.sensitive_columns == {"inspector.full_name"}
    assert [g.term for g in registry.glossary] == ["widget"]


def test_load_rejects_unknown_field(layer_dir):
    path = layer_dir / "metrics" / "plant_count.yml"
    path.write_text(path.read_text() + "formla: oops\n")
    with pytest.raises(ValueError, match="plant_count.yml"):
        load_registry(layer_dir)


def test_load_rejects_sensitive_without_column(layer_dir):
    path = layer_dir / "dimensions" / "inspector_name.yml"
    path.write_text(path.read_text().replace("source_column: inspector.full_name\n", ""))
    with pytest.raises(ValueError, match="needs source_column"):
        load_registry(layer_dir)


def test_load_rejects_replay_of_unknown_window(layer_dir):
    path = layer_dir / "metrics" / "defect_rate.yml"
    path.write_text(path.read_text().replace("time_window: q1", "time_window: q9"))
    with pytest.raises(ValueError, match="unknown time window"):
        load_registry(layer_dir)


def test_match_is_whole_word(registry):
    m = registry.match("Show DEFECTS by line for the first quarter")
    assert [x.name for x in m.metrics] == ["defect_rate"]
    assert [x.name for x in m.dimensions] == ["line"]
    assert [x.name for x in m.time_windows] == ["q1"]
    assert [g.term for g in m.glossary] == []
    # "lines" must not match inside "guidelines".
    assert registry.match("read the guidelines").is_empty


def test_compile_with_dimension_and_window(registry):
    q = compile_metric(registry, "defect_rate", "q1", ["line"], min_rows=10, limit=5)
    assert "JOIN assembly_line l ON l.line_id = w.line_id" in q.sql
    assert "l.line_code AS line" in q.sql
    assert "GROUP BY l.line_code" in q.sql
    assert "HAVING COUNT(*) >= 10" in q.sql
    assert q.sql.rstrip().endswith("LIMIT 5;")
    # The cap moved the end from April to March, and said so.
    assert "w.built_at < '2026-03-01'" in q.sql
    assert len(q.warnings) == 1


def test_compile_closed_anchor_uses_closed_column(registry):
    q = compile_metric(registry, "defect_rate", "shipped_march")
    assert "w.shipped_at >= '2026-03-01'" in q.sql


def test_compile_month_follows_time_column(registry):
    q = compile_metric(registry, "defect_rate", None, ["month"])
    assert "SUBSTR(w.built_at, 1, 7) AS month" in q.sql


def test_compile_refuses_sensitive_dimension(registry):
    with pytest.raises(CompileError, match="personal data"):
        compile_metric(registry, "defect_rate", None, ["inspector_name"])


def test_compile_refuses_dimension_without_join_path(registry):
    with pytest.raises(CompileError, match="cannot be combined"):
        compile_metric(registry, "plant_count", None, ["line"])


def test_compile_ignores_window_without_time_axis(registry):
    q = compile_metric(registry, "plant_count", "q1")
    assert "WHERE" not in q.sql
    assert "no time axis" in q.warnings[0]


def test_compile_substitutes_window_placeholders(layer_dir):
    path = layer_dir / "metrics" / "defect_rate.yml"
    path.write_text(
        path.read_text().replace(
            "  - LEFT JOIN widget_return wr ON wr.widget_id = w.widget_id",
            "  - \"LEFT JOIN (SELECT * FROM widget_return WHERE returned_at >= "
            "'{window_start}' AND returned_at < '{window_end}') wr ON wr.widget_id = w.widget_id\"",
        )
    )
    registry = load_registry(layer_dir)
    q = compile_metric(registry, "defect_rate", "q1")
    assert "returned_at >= '2026-01-01' AND returned_at < '2026-03-01'" in q.sql


def test_replay_against_sqlite(registry):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE widget (widget_id INT, built_at TEXT, shipped_at TEXT)")
        conn.exec_driver_sql("CREATE TABLE widget_return (return_id INT, widget_id INT)")
        conn.exec_driver_sql(
            "INSERT INTO widget VALUES (1, '2026-01-05', '2026-01-09'), (2, '2026-02-01', '2026-02-03')"
        )
        conn.exec_driver_sql("INSERT INTO widget_return VALUES (10, 1)")
    [result] = replay(engine, registry)
    assert result.metric == "defect_rate"
    assert result.actual == 0.5
    assert result.ok


def test_replay_reports_errors_instead_of_raising(registry):
    [result] = replay(sa.create_engine("sqlite://"), registry)
    assert result.actual is None
    assert "no such table" in result.error
    assert not result.ok


def test_semantic_layer_tables_includes_dimension_joins(registry):
    assert cc.semantic_layer_tables(registry) == {
        "widget",
        "widget_return",
        "plant",
        "assembly_line",
        "inspector",
    }
