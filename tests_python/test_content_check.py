# -*- coding: utf-8 -*-

"""
Every fixture here uses a fictional domain (``acme`` / ``widget`` / ``part``).

A test asserting on this dataset's real table names does not fail after a swap.
It just becomes a lie describing a domain that no longer exists, and it makes
the next reader think the test is tied to the current data and must not be
touched.
"""

import sqlalchemy as sa

from agent_app import content_check as cc


SITE_TOML_HEAD = """
[brand]
displayName = "Acme Widgets"
"""


def one_action(render: str, action: str, title: str = "Thread") -> str:
    return f"""
[[chat.suggestedActions]]
render = "{render}"
title = "{title}"
label = "A question"
action = "{action}"
"""


def valid_site_toml() -> str:
    """Twelve questions, three of each kind, every phrase agreeing with its render."""
    parts = [SITE_TOML_HEAD]
    for i in range(3):
        parts.append(one_action("free", f"Tell me about widget line {i}."))
        parts.append(
            one_action("table", f"Show widget line {i} as a Markdown table and show the SQL.")
        )
        parts.append(one_action("flowchart", f"Show the part lifecycle {i} as a flowchart."))
        parts.append(one_action("chart", f"Show widget totals {i} as a bar chart."))
    return "".join(parts)


class FakePaths:
    """Stands in for ``path_enum`` so the checks read a tmp_path instead of the repo."""

    def __init__(self, root):
        self.dir_project_root = root
        self.path_bi_agent_system_prompt_md = root / "data" / "system-prompt.md"
        self.path_site_toml = root / "data" / "site.toml"
        self.path_business_context_md = root / "data" / "business-context.md"
        self.dir_semantic_layer = root / "data" / "semantic_layer"
        self.path_llms_txt = root / "data" / "llms.txt"
        self.path_sql_writer_prompt_md = root / "data" / "sql-writer-prompt.md"
        self.path_golden_cases_yml = root / "data" / "eval" / "golden_cases.yml"
        self.path_warehouse_ddl_sql = root / "infra" / "snowflake" / "ddl.sql"


#: The warehouse DDL a fake repo carries: two analytics tables, plus an audit
#: table that must not count as queryable.
WIDGET_DDL = """
CREATE TABLE ANALYTICS.WIDGET (
    id NUMBER(38,0) PRIMARY KEY
);

CREATE TABLE ANALYTICS.PART (
    id NUMBER(38,0) PRIMARY KEY,
    widget_id NUMBER(38,0)
);

CREATE TABLE AUDIT.QUERY_LOG (
    log_id VARCHAR
);
"""


WIDGET_METRIC_YML = """
name: widget_count
display_name: Widget Count
description: Number of widgets.
base_table: widget w
formula: COUNT(*)
"""


def use_tmp_repo(monkeypatch, tmp_path, prompt="", site="", context="", ddl=WIDGET_DDL):
    (tmp_path / "data").mkdir(exist_ok=True)
    if ddl is not None:
        (tmp_path / "infra" / "snowflake").mkdir(parents=True, exist_ok=True)
        (tmp_path / "infra" / "snowflake" / "ddl.sql").write_text(ddl, encoding="utf-8")
    (tmp_path / "data" / "semantic_layer" / "metrics").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "semantic_layer" / "metrics" / "widget_count.yml").write_text(
        WIDGET_METRIC_YML, encoding="utf-8"
    )
    # The index as the layer generates it, so a fake repo starts in sync.
    from agent_app.knowledge import KnowledgeBase
    from agent_app.semantic_layer.api import load_registry

    (tmp_path / "data" / "llms.txt").write_text(
        KnowledgeBase(load_registry(tmp_path / "data" / "semantic_layer")).index(), encoding="utf-8"
    )
    (tmp_path / "data" / "system-prompt.md").write_text(prompt, encoding="utf-8")
    (tmp_path / "data" / "sql-writer-prompt.md").write_text(prompt, encoding="utf-8")
    (tmp_path / "data" / "eval").mkdir(exist_ok=True)
    (tmp_path / "data" / "eval" / "golden_cases.yml").write_text(
        "- id: W01\n  source: t\n  questions: ['How many widgets?']\n"
        "  reference_sql: SELECT COUNT(*) AS n FROM widget\n  match: {values: [n]}\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "site.toml").write_text(site, encoding="utf-8")
    (tmp_path / "data" / "business-context.md").write_text(context, encoding="utf-8")
    monkeypatch.setattr(cc, "path_enum", FakePaths(tmp_path))
    return tmp_path


class TestSourceDatesAgreeWithTheGlossary:
    SOURCE = """
name: plant
display_name: Plant profile
description: One plant.
list_sql: SELECT code AS key, name AS title FROM plant
read_sql: SELECT * FROM plant WHERE code = :key AND snapshot = '{date}'
"""

    def _with_source(self, tmp_path, date, glossary):
        layer = tmp_path / "data" / "semantic_layer"
        (layer / "sources").mkdir(parents=True, exist_ok=True)
        (layer / "sources" / "plant.yml").write_text(self.SOURCE.format(date=date), encoding="utf-8")
        (layer / "glossary").mkdir(exist_ok=True)
        (layer / "glossary" / "core.yml").write_text(glossary, encoding="utf-8")

    def test_a_stated_date_passes(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        self._with_source(tmp_path, "2026-03-01", "- term: snapshot\n  definition: Current means snapshot = '2026-03-01'.\n")
        report = cc.Report()
        assert cc.check_semantic_layer(report, {"widget", "plant"}) and report.errors == []

    def test_a_date_the_glossary_does_not_state_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        self._with_source(tmp_path, "2026-04-01", "- term: snapshot\n  definition: Current means snapshot = '2026-03-01'.\n")
        report = cc.Report()
        assert not cc.check_semantic_layer(report, {"widget", "plant"})
        assert "2026-04-01" in report.errors[0]


class TestCheckKnowledgeIndex:
    def test_in_sync_passes(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        report = cc.Report()
        assert cc.check_knowledge_index(report) and report.errors == []

    def test_stale_index_fails_and_says_how_to_fix_it(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "llms.txt").write_text("# old\n", encoding="utf-8")
        report = cc.Report()
        assert not cc.check_knowledge_index(report)
        assert "stale" in report.errors[0] and "kb-index" in report.errors[0]

    def test_missing_index_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "llms.txt").unlink()
        report = cc.Report()
        assert not cc.check_knowledge_index(report)
        assert "missing" in report.errors[0]


class TestCheckSemanticLayer:
    def test_passes_when_tables_exist(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        report = cc.Report()
        assert cc.check_semantic_layer(report, {"widget"})
        assert report.errors == []

    def test_names_a_table_the_warehouse_lacks(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        report = cc.Report()
        assert not cc.check_semantic_layer(report, {"part"})
        assert "'widget'" in report.errors[0]

    def test_empty_layer_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "semantic_layer" / "metrics" / "widget_count.yml").unlink()
        report = cc.Report()
        assert not cc.check_semantic_layer(report, None)
        assert "no metrics" in report.errors[0]

    def test_invalid_file_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        bad = tmp_path / "data" / "semantic_layer" / "metrics" / "broken.yml"
        bad.write_text("name: [unclosed\n", encoding="utf-8")
        report = cc.Report()
        assert not cc.check_semantic_layer(report, None)
        assert "broken.yml" in report.errors[0]


class TestKindFromPhrases:
    def test_each_kind_is_recognised(self):
        assert cc.kind_from_phrases("show it as a flowchart") == "flowchart"
        assert cc.kind_from_phrases("show it as a bar chart") == "chart"
        assert cc.kind_from_phrases("show it as a line chart") == "chart"
        assert cc.kind_from_phrases("as a markdown table and show the sql") == "table"

    def test_table_needs_both_phrases(self):
        """A table question without "show the SQL" is not a table question."""
        assert cc.kind_from_phrases("show it as a markdown table") == "free"

    def test_no_phrase_is_free(self):
        assert cc.kind_from_phrases("what can you help me with?") == "free"

    def test_two_kinds_at_once_is_ambiguous(self):
        """One question cannot promise two different blocks on one button."""
        wording = "as a flowchart and as a bar chart"
        assert cc.kind_from_phrases(wording) == "ambiguous"


class TestCheckRenderQuota:
    def test_valid_config_passes(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path, site=valid_site_toml())
        report = cc.Report()
        assert cc.check_render_quota(report) is True
        assert report.errors == []

    def test_wrong_counts_fail(self, monkeypatch, tmp_path):
        site = SITE_TOML_HEAD + one_action("free", "Tell me about widgets.")
        use_tmp_repo(monkeypatch, tmp_path, site=site)
        report = cc.Report()
        assert cc.check_render_quota(report) is False
        assert any("expected 3" in e for e in report.errors)

    def test_render_disagreeing_with_wording_fails(self, monkeypatch, tmp_path):
        """The icon promises a chart; the wording produces prose. Both directions matter."""
        site = valid_site_toml().replace(
            'action = "Show widget totals 0 as a bar chart."',
            'action = "Tell me about widget totals."',
        )
        use_tmp_repo(monkeypatch, tmp_path, site=site)
        report = cc.Report()
        assert cc.check_render_quota(report) is False
        assert any("the wording produces" in e for e in report.errors)

    def test_unknown_render_value_fails(self, monkeypatch, tmp_path):
        site = SITE_TOML_HEAD + one_action("sankey", "Draw a widget sankey.")
        use_tmp_repo(monkeypatch, tmp_path, site=site)
        report = cc.Report()
        assert cc.check_render_quota(report) is False
        assert any("unknown render" in e for e in report.errors)

    def test_missing_field_fails(self, monkeypatch, tmp_path):
        site = SITE_TOML_HEAD + '\n[[chat.suggestedActions]]\nrender = "free"\n'
        use_tmp_repo(monkeypatch, tmp_path, site=site)
        report = cc.Report()
        assert cc.check_render_quota(report) is False
        assert any("is missing" in e for e in report.errors)

    def test_missing_file_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "site.toml").unlink()
        report = cc.Report()
        assert cc.check_render_quota(report) is False


class TestCheckPromptTables:
    def test_known_tables_pass(self, monkeypatch, tmp_path):
        prompt = "```sql\nSELECT * FROM widget JOIN part ON part.widget_id = widget.id\n```"
        use_tmp_repo(monkeypatch, tmp_path, prompt=prompt)
        report = cc.Report()
        assert cc.check_prompt_tables(report, {"widget", "part"}) is True

    def test_unknown_table_fails(self, monkeypatch, tmp_path):
        prompt = "```sql\nSELECT * FROM gadget\n```"
        use_tmp_repo(monkeypatch, tmp_path, prompt=prompt)
        report = cc.Report()
        assert cc.check_prompt_tables(report, {"widget"}) is False
        assert any("'gadget'" in e for e in report.errors)

    def test_prose_outside_sql_blocks_is_ignored(self, monkeypatch, tmp_path):
        """The dialect section says EXTRACT(QUARTER FROM d); `d` is not a table."""
        prompt = "Write `EXTRACT(QUARTER FROM d)` instead.\n\n```sql\nSELECT * FROM widget\n```"
        use_tmp_repo(monkeypatch, tmp_path, prompt=prompt)
        report = cc.Report()
        assert cc.check_prompt_tables(report, {"widget"}) is True

    def test_dotted_name_is_a_column_not_a_table(self, monkeypatch, tmp_path):
        prompt = "```sql\nSELECT EXTRACT(QUARTER FROM w.made_on) FROM widget w\n```"
        use_tmp_repo(monkeypatch, tmp_path, prompt=prompt)
        report = cc.Report()
        assert cc.check_prompt_tables(report, {"widget"}) is True

    def test_cte_names_are_not_missing_tables(self, monkeypatch, tmp_path):
        prompt = (
            "```sql\nWITH recent AS (SELECT * FROM widget), old AS (SELECT * FROM part)\n"
            "SELECT * FROM recent JOIN old ON TRUE\n```"
        )
        use_tmp_repo(monkeypatch, tmp_path, prompt=prompt)
        report = cc.Report()
        assert cc.check_prompt_tables(report, {"widget", "part"}) is True

    def test_missing_prompt_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "system-prompt.md").unlink()
        report = cc.Report()
        assert cc.check_prompt_tables(report, {"widget"}) is False


class TestCheckContentFiles:
    def test_full_files_pass(self, monkeypatch, tmp_path):
        filler = "acme widget prose. " * 40
        use_tmp_repo(monkeypatch, tmp_path, prompt=filler, site=filler, context=filler)
        report = cc.Report()
        assert cc.check_content_files(report) is True

    def test_stub_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path, prompt="TODO", site="TODO", context="TODO")
        report = cc.Report()
        assert cc.check_content_files(report) is False
        # The system prompt, the SQL writer prompt (same stub), site, context.
        assert len(report.errors) == 4

    def test_missing_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "business-context.md").unlink()
        report = cc.Report()
        assert cc.check_content_files(report) is False
        assert any("missing" in e for e in report.errors)


class TestCheckRetiredTerms:
    def test_no_terms_is_a_no_op(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        report = cc.Report()
        assert cc.check_retired_terms(report, []) is True

    def test_finds_a_leftover_word(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path, site="displayName = 'gadget shop'")
        report = cc.Report()
        assert cc.check_retired_terms(report, ["gadget"]) is False
        assert any("gadget" in e for e in report.errors)

    def test_matching_is_whole_word(self, monkeypatch, tmp_path):
        """`bed` must flag a hospital leftover without flagging `bedrock`."""
        use_tmp_repo(monkeypatch, tmp_path, site="techStack = ['AWS Bedrock']")
        report = cc.Report()
        assert cc.check_retired_terms(report, ["bed"]) is True

    def test_unreadable_file_is_skipped(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "blob.json").write_bytes(b"\xff\xfe\x00binary")
        report = cc.Report()
        assert cc.check_retired_terms(report, ["gadget"]) is True

    def test_scan_skips_noise_directories(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        for junk in ("node_modules", "__pycache__"):
            (tmp_path / "data" / junk).mkdir()
            (tmp_path / "data" / junk / "x.js").write_text("gadget", encoding="utf-8")
        (tmp_path / "data" / "notes.png").write_text("gadget", encoding="utf-8")
        report = cc.Report()
        assert cc.check_retired_terms(report, ["gadget"]) is True

    def test_exempt_files_are_skipped(self, monkeypatch, tmp_path):
        """The checker names these words itself; flagging them is pure noise."""
        (tmp_path / "agent_app").mkdir()
        (tmp_path / "agent_app" / "cli.py").write_text("# gadget", encoding="utf-8")
        use_tmp_repo(monkeypatch, tmp_path)
        report = cc.Report()
        assert cc.check_retired_terms(report, ["gadget"]) is True


class TestWarehouseTables:
    def test_reads_the_analytics_tables_from_the_ddl(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        assert cc.warehouse_tables() == {"widget", "part"}

    def test_no_ddl_is_no_tables(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path, ddl=None)
        assert cc.warehouse_tables() == set()


class TestReport:
    def test_marks(self, capsys):
        report = cc.Report()
        report.section("passing", True)
        report.section("failing", False)
        report.section("skipped", None)
        out = capsys.readouterr().out
        assert "PASS  passing" in out
        assert "FAIL  failing" in out
        assert "SKIP  skipped" in out

    def test_warn_and_error_are_tagged(self):
        report = cc.Report()
        report.warn("ddl", "no tables")
        report.error("quota", "bad count")
        assert report.warnings == ["[ddl] no tables"]
        assert report.errors == ["[quota] bad count"]


class TestRun:
    def test_all_green(self, monkeypatch, tmp_path, capsys):
        filler = "acme widget prose. " * 40
        use_tmp_repo(
            monkeypatch,
            tmp_path,
            prompt=filler + "\n```sql\nSELECT * FROM widget JOIN part ON TRUE\n```",
            site=valid_site_toml(),
            context=filler,
        )
        assert cc.run() == 0
        assert "All checks passed." in capsys.readouterr().out

    def test_missing_ddl_reports_once(self, monkeypatch, tmp_path, capsys):
        """One error about the missing DDL, not one per table the prompt names."""
        filler = "acme widget prose. " * 40
        use_tmp_repo(
            monkeypatch,
            tmp_path,
            prompt=filler + "\n```sql\nSELECT * FROM widget JOIN part ON TRUE\n```",
            site=valid_site_toml(),
            context=filler,
            ddl=None,
        )
        assert cc.run() == 1
        out = capsys.readouterr().out
        assert out.count("ERROR") == 1
        assert "ddl.sql" in out

    def test_an_audit_table_is_not_queryable(self, monkeypatch, tmp_path, capsys):
        filler = "acme widget prose. " * 40
        use_tmp_repo(
            monkeypatch,
            tmp_path,
            prompt=filler + "\n```sql\nSELECT * FROM query_log\n```",
            site=valid_site_toml(),
            context=filler,
        )
        assert cc.run() == 1
        assert "'query_log'" in capsys.readouterr().out


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.content_check",
        preview=False,
    )


class TestHeldOutCases:
    HOLDOUT = (
        "- id: H01\n  source: a colleague\n  questions: ['How many widgets?']\n"
        "  reference_sql: SELECT COUNT(*) AS n FROM widget\n  match: {values: [n]}\n"
    )

    def test_a_held_out_file_loads_and_is_counted(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "eval" / "holdout").mkdir()
        (tmp_path / "data" / "eval" / "holdout" / "colleague.yml").write_text(self.HOLDOUT, encoding="utf-8")
        report = cc.Report()
        assert cc.check_golden_cases(report) and report.errors == []

    def test_an_id_shared_with_the_golden_set_fails(self, monkeypatch, tmp_path):
        use_tmp_repo(monkeypatch, tmp_path)
        (tmp_path / "data" / "eval" / "holdout").mkdir()
        (tmp_path / "data" / "eval" / "holdout" / "colleague.yml").write_text(self.HOLDOUT.replace("H01", "W01"), encoding="utf-8")
        report = cc.Report()
        assert not cc.check_golden_cases(report)
        assert "W01" in report.errors[0] and "also in the golden set" in report.errors[0]
