# -*- coding: utf-8 -*-

"""
When a question is plain enough to compile without the writer. Widget shop.
"""

import pytest

from agent_app.semantic_layer.api import load_registry
from agent_app.semantic_layer.fast_path import plan_fast_path, residue

LAYER = {
    "metrics/defect_rate.yml": """
name: defect_rate
display_name: Defect Rate
description: Share of widgets returned as defective.
base_table: widget w
formula: 1.0 * SUM(w.is_defective) / COUNT(*)
time_column: w.built_on
synonyms: [defects, defect rates]
""",
    "metrics/widget_count.yml": """
name: widget_count
display_name: Widget Count
description: Number of widgets built.
base_table: widget w
formula: COUNT(*)
time_column: w.built_on
synonyms: [how many widgets, widgets built]
""",
    "metrics/report_score.yml": """
name: report_score
display_name: Report Score
description: The precomputed score in widget_summary.
base_table: widget_summary s
formula: AVG(s.score)
synonyms: [score]
""",
    "dimensions/line.yml": """
name: line
display_name: Assembly Line
expression: w.line_code
synonyms: [lines, by line]
""",
    "dimensions/owner_email.yml": """
name: owner_email
display_name: Owner Email
expression: o.email
alias: o
joins:
  w: JOIN owner o ON o.id = w.owner_id
source_column: owner.email
sensitive: true
synonyms: [email]
""",
    "time_windows/q1.yml": """
name: q1
display_name: First Quarter
start: 2026-01-01
end: 2026-04-01
synonyms: [first quarter, q1, first 3 months]
""",
    "glossary/core.yml": """
- term: line report
  definition: The precomputed row per line in widget_summary.
  read_from: widget_summary
""",
}


@pytest.fixture
def registry(tmp_path):
    for rel, text in LAYER.items():
        path = tmp_path / "layer" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return load_registry(tmp_path / "layer")


def plan(registry, intent):
    return plan_fast_path(registry, intent, registry.match(intent))


def test_a_plain_metric_question_compiles(registry):
    p = plan(registry, "What is the defect rate by line in the first quarter?")
    assert p is not None
    assert (p.metric, p.dimensions, p.time_window) == ("defect_rate", ["line"], "q1")
    assert "1.0 * SUM(w.is_defective) / COUNT(*) AS defect_rate" in p.sql
    assert "w.built_on >= '2026-01-01' AND w.built_on < '2026-04-01'" in p.sql
    assert "GROUP BY w.line_code" in p.sql


def test_a_digit_inside_a_defined_windows_name_is_not_a_condition(registry):
    """"first 3 months" names q1; the 3 is the layer's, not the user's."""
    p = plan(registry, "defect rate by line over the first 3 months")
    assert p is not None and p.time_window == "q1"


def test_no_dimension_and_no_window_is_still_plain(registry):
    p = plan(registry, "show me the overall defect rate")
    assert p is not None and p.dimensions == [] and p.time_window is None


@pytest.mark.parametrize(
    "intent, why",
    [
        ("defect rate by line for the north plant", "a condition the layer has no word for"),
        ("defect rate for widgets heavier than 2 kg", "a number"),
        ("defect rate by line, only lines with at least 50 widgets", "a threshold"),
        ("top 3 lines by defect rate", "a top N"),
        ("defect rate where the owner is 'Ada'", "a quoted value"),
        ("defect rate and widget count by line", "two metrics"),
        ("the line report", "a term answered from its own table"),
        ("defect rate by email", "a personal-data dimension"),
        ("widgets shipped in q1", "nothing matched"),
    ],
)
def test_anything_beyond_the_vocabulary_goes_to_the_writer(registry, intent, why):
    assert plan(registry, intent) is None, why


def test_residue_names_what_the_writer_must_read(registry):
    intent = "defect rate by line for the north plant, please"
    assert residue(intent, registry, registry.match(intent)) == ["north", "plant"]
    intent = "What's the defect rate by line in the first quarter?"
    assert residue(intent, registry, registry.match(intent)) == []
