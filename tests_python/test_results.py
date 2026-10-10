# -*- coding: utf-8 -*-

from agent_app.results import PREVIEW_ROWS, expand_result_refs, markdown_table, preview, referenced_ids, result_id


def test_preview_keeps_a_short_result_and_trims_a_long_one():
    short = {"status": "ok", "rows": [[1], [2]], "row_count": 2}
    assert preview(short) == short and "preview" not in preview(short)
    long = {"status": "ok", "rows": [[i] for i in range(PREVIEW_ROWS + 10)], "row_count": PREVIEW_ROWS + 10}
    trimmed = preview(long)
    assert len(trimmed["rows"]) == PREVIEW_ROWS
    assert trimmed["preview"] is True and trimmed["row_count"] == PREVIEW_ROWS + 10
    # The original is untouched: it is what the browser gets.
    assert len(long["rows"]) == PREVIEW_ROWS + 10


def test_ids_are_short_and_ordered():
    assert [result_id(n) for n in (1, 2, 10)] == ["r1", "r2", "r10"]


def test_markdown_table_escapes_and_counts():
    text = markdown_table(["tenant", "rate"], [["Plano | TX", 0.21394], ["Austin", None]], row_count=5)
    assert text.splitlines()[:2] == ["| tenant | rate |", "| --- | --- |"]
    assert "| Plano \\| TX | 0.21394 |" in text
    assert "| Austin |  |" in text
    assert text.endswith("(2 of 5 rows shown)")


def test_references_expand_to_the_table_and_unknown_ones_stay():
    tables = [{"result_id": "r1", "columns": ["line", "n"], "rows": [["A", 3], ["B", 1]], "row_count": 2}]
    answer = "Line A leads.\n\n[RESULT:r1]\n\nHow this was computed: counts. [KB:metric/x]\n\n[RESULT:r9]"
    out = expand_result_refs(answer, tables)
    assert "| line | n |" in out and "| A | 3 |" in out and "| B | 1 |" in out
    assert "[RESULT:r1]" not in out
    assert "[RESULT:r9]" in out
    # A reference inside a sentence is prose, not a table.
    assert expand_result_refs("See [RESULT:r1] above", tables) == "See [RESULT:r1] above"
    assert referenced_ids(answer) == ["r1", "r9"]
    assert expand_result_refs("", tables) == ""


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(__file__, "agent_app.results", preview=False)
