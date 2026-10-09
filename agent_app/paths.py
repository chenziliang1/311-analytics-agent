# -*- coding: utf-8 -*-

"""
Every path this package needs, in one place.

Paths are facts about the source tree, not configuration: a swap rewrites the
*contents* of ``data/``, never the filenames.

Only paths that are facts about *this* source tree. Anything whose name belongs
to the dataset is discovered in ``dataset.py`` instead, so that swapping a
dataset never edits this package.

Do not add a path speculatively. An entry with no reader silently rots into a
pointer at a directory that no longer exists, which is what happened to every
entry this module has ever lost.
"""

from pathlib import Path
from functools import cached_property


class PathEnum:
    dir_package = Path(__file__).absolute().parent
    dir_project_root = dir_package.parent

    # Coverage report, written by agent_app.tests.helper.
    dir_htmlcov = dir_project_root / "htmlcov"

    # Split by whether the value is a secret. ``.env`` is gitignored and loaded
    # first, so it wins; ``.env.defaults`` is committed. See ``config.py``.
    path_env = dir_project_root / ".env"
    path_env_defaults = dir_project_root / ".env.defaults"

    # Authored content only -- copy, prompt, explainer, seed. Keeping it in one
    # directory is what makes a dataset swap a directory replacement rather than
    # a diff scattered across the repo.
    dir_data = dir_project_root / "data"

    # Read by the frontend, never parsed here. Named so that the preflight
    # check can verify they exist without importing TypeScript.
    path_site_toml = dir_data / "site.toml"
    path_business_context_md = dir_data / "business-context.md"

    # The agent's domain brain.
    path_bi_agent_system_prompt_md = dir_data / "system-prompt.md"

    # Metric, dimension and time window definitions, one YAML file each. Read
    # by ``agent_app.semantic_layer``; a swap replaces the whole directory.
    dir_semantic_layer = dir_data / "semantic_layer"

    # What the warehouse tool's own model is told before it writes SQL: the
    # domain and the dialect, without anything about talking to a user.
    path_sql_writer_prompt_md = dir_data / "sql-writer-prompt.md"

    # The golden conversations `agent-app eval` grades the agent on.
    path_golden_cases_yml = dir_data / "eval" / "golden_cases.yml"

    dir_tmp = dir_project_root / "tmp"

    # Written by the agent's ``write_debug_report`` tool.
    path_debug_report_md = dir_tmp / "debug_report.md"

    # One JSON line per warehouse query, written by the MCP server's audit sink.
    path_query_log_jsonl = dir_tmp / "query_log.jsonl"

    # The warehouse's tables as created in Snowflake: the list of tables that
    # exist, for the content check.
    path_warehouse_ddl_sql = dir_project_root / "infra" / "snowflake" / "ddl.sql"

    # One JSON line per thumbs up or down, written by the MCP server.
    path_feedback_jsonl = dir_tmp / "feedback.jsonl"

    # Golden case drafts made from thumbs-downs by `agent-app feedback --draft`.
    dir_feedback_drafts = dir_tmp / "feedback"

    # One JSON report per `agent-app grade` run.
    dir_eval_reports = dir_tmp / "eval"

    # The interpreter this project installs into. Named here because
    # ``dataset.py`` shells out to it to run the dataset's generator.
    path_venv_python = dir_project_root / ".venv" / "bin" / "python"

    @cached_property
    def path_bi_agent_system_prompt_content(self) -> str:
        """Read once per process; the prompt is re-read on every agent build."""
        return self.path_bi_agent_system_prompt_md.read_text(encoding="utf-8")


path_enum = PathEnum()
