# -*- coding: utf-8 -*-

"""
The knowledge base over a fictional widget shop's semantic layer.
"""

import pytest

from agent_app.knowledge import KnowledgeBase, citations, slug, source_ids_for
from agent_app.semantic_layer.api import load_registry

LAYER = {
    "metrics/defect_rate.yml": """
name: defect_rate
display_name: Defect Rate
description: Share of widgets returned as defective.
base_table: widget w
joins: [JOIN plant p ON p.id = w.plant_id]
formula: 1.0 * SUM(w.is_defective) / COUNT(*)
filters: [w.shipped_at IS NOT NULL]
time_column: w.built_on
synonyms: [defects]
notes: Count shipped widgets only.
replay:
  time_window: q1
  expected: 0.25
  tolerance: 0.01
""",
    "dimensions/line.yml": """
name: line
display_name: Assembly Line
expression: w.line_code
allowed_values: [A, B]
""",
    "dimensions/notes.yml": """
name: notes
display_name: Inspection Notes
expression: p.notes
alias: p
joins:
  w: JOIN part p ON p.widget_id = w.id
source_column: part.notes
redacted: true
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
""",
    "time_windows/q1.yml": """
name: q1
display_name: First Quarter
start: 2026-01-01
end: 2026-04-01
synonyms: [first quarter]
""",
    "glossary/core.yml": """
- term: Scrap bin
  definition: Where a widget goes when it fails final inspection.
  synonyms: [scrap]
  read_from: scrap_log
""",
    "sources/plant.yml": """
name: plant
display_name: Plant profile
description: One plant, its region and head count.
list_sql: SELECT code AS key, name AS title FROM plant ORDER BY code
read_sql: SELECT code, name, region, headcount FROM plant WHERE code = :key
""",
}


@pytest.fixture
def registry(tmp_path):
    for rel, text in LAYER.items():
        path = tmp_path / "layer" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return load_registry(tmp_path / "layer")


def test_static_sources_cover_every_kind_but_personal_data_and_free_text(registry):
    ids = set(KnowledgeBase(registry).static_sources)
    assert ids == {"metric/defect_rate", "term/scrap-bin", "window/q1", "dimension/line"}
    assert registry.redacted_columns == {"part.notes"} and registry.sensitive_columns == {"owner.email"}


def test_a_redacted_dimension_names_its_column():
    from agent_app.semantic_layer.api import Dimension

    with pytest.raises(ValueError, match="source_column"):
        Dimension(name="n", display_name="N", expression="p.notes", redacted=True)


def test_a_metric_document_is_its_definition(registry):
    doc = KnowledgeBase(registry).static_sources["metric/defect_rate"]
    assert doc.title == "Defect Rate" and doc.citation == "[KB:metric/defect_rate]"
    for needle in ("1.0 * SUM(w.is_defective)", "widget w", "JOIN plant p", "w.shipped_at IS NOT NULL",
                   "w.built_on", "defects", "Count shipped widgets only."):
        assert needle in doc.text, needle
    # The replay value is a test fixture; shown, the agent quoted one as a result.
    assert "0.25" not in doc.text


def test_cited_sources_are_whole_documents(registry):
    from agent_app.knowledge import cited_sources

    docs = cited_sources(registry, registry.match("defects per line in the first quarter"))
    assert [d.id for d in docs] == ["metric/defect_rate", "window/q1"]
    assert docs[0].citation == "[KB:metric/defect_rate]" and "Formula" in docs[0].text


def test_term_window_and_dimension_documents(registry):
    docs = KnowledgeBase(registry).static_sources
    assert "fails final inspection" in docs["term/scrap-bin"].text and "scrap_log" in docs["term/scrap-bin"].text
    assert "2026-01-01" in docs["window/q1"].text and "2026-04-01" in docs["window/q1"].text
    assert "A, B" in docs["dimension/line"].summary


def test_index_lists_every_document_once_and_entity_kinds_as_patterns(registry):
    index = KnowledgeBase(registry).index()
    for line in ("- [metric/defect_rate](metric/defect_rate): Defect Rate. Share of widgets returned as defective.",
                 "- [term/scrap-bin](term/scrap-bin): Scrap bin.",
                 "- [window/q1](window/q1): First Quarter.",
                 "- [dimension/line](dimension/line): Assembly Line.",
                 "## Plant profiles",
                 "- [plant/<key>](plant/<key>): one per key"):
        assert line in index, line
    assert "owner_email" not in index
    assert index.count("metric/defect_rate") == 2  # the link text and its target, once


def test_index_expands_the_entities_the_asker_may_read(registry):
    index = KnowledgeBase(registry).index({"plant": [("oslo", "Oslo"), ("lima", "Lima")]})
    assert "- [plant/oslo](plant/oslo): Oslo" in index and "plant/<key>" not in index
    assert "(none you may read)" in KnowledgeBase(registry).index({"plant": []})


def test_knows_real_ids_and_well_formed_entity_ids_only(registry):
    kb = KnowledgeBase(registry)
    assert kb.knows("metric/defect_rate") and kb.knows("plant/oslo")
    for bogus in ("metric/profit", "plant/", "plant/oslo lima", "factory/oslo", "defect_rate"):
        assert not kb.knows(bogus), bogus


def test_citations_and_source_ids(registry):
    assert citations("x [KB:metric/defect_rate] y [KB:plant/oslo] [KB:metric/defect_rate] [KB:bad id]") == [
        "metric/defect_rate", "plant/oslo"
    ]
    assert citations("") == []
    match = registry.match("defects per line in the first quarter, minus the scrap bin")
    assert source_ids_for(match) == ["metric/defect_rate", "term/scrap-bin", "window/q1"]
    assert slug("Other bucket") == "other-bucket" and slug("311") == "311"


def test_entity_source_validation(tmp_path):
    from agent_app.semantic_layer.api import EntitySource

    with pytest.raises(ValueError, match=":key"):
        EntitySource(name="p", display_name="P", description="d", list_sql="SELECT 1", read_sql="SELECT 1")
    with pytest.raises(Exception):
        EntitySource(name="p", display_name="P", description="d", list_sql="SELECT 1",
                     read_sql="SELECT :key", key_pattern="[")
