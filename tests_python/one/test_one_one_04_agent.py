# -*- coding: utf-8 -*-

"""
Test for the agent's read-only tools.

These call the tool methods directly, the same way the strands agent does at
runtime, but without building a model or an Agent -- so no API key, no network,
no tokens spent. The tools only touch the local SQLite database and the
filesystem, which is what makes that possible.

The write-operation tools are not covered here: they are commented out of the
agent's tool list and target tables that do not exist in this dataset.
"""

import pytest

from agent_app.one.one_00_main import one
from agent_app.paths import path_enum


class TestReadOnlyTools:
    @pytest.mark.warehouse
    def test_get_database_schema(self):
        """The schema tool returns the encoded schema, including real tables."""
        result = one.tool_get_database_schema()
        assert isinstance(result, str)
        assert "service_requests" in result
        assert "tenant_health_snapshots" in result

    @pytest.mark.warehouse
    def test_execute_sql_query(self):
        """A valid SELECT comes back as a Markdown table."""
        result = one.tool_execute_sql_query(sql="SELECT COUNT(*) AS n FROM tenant_health_snapshots")
        assert "|" in result  # Markdown table pipes
        assert "n" in result

    @pytest.mark.warehouse
    def test_execute_sql_query_no_rows(self):
        """A query matching nothing says so instead of returning an empty table."""
        result = one.tool_execute_sql_query(sql="SELECT 1 WHERE 1 = 0")
        assert result == "No result"

    @pytest.mark.warehouse
    def test_execute_sql_query_accepts_cte(self):
        """A CTE opens with WITH, not SELECT, and must still be allowed."""
        result = one.tool_execute_sql_query(
            sql=(
                "WITH d AS (SELECT snapshot_id FROM tenant_health_snapshots) "
                "SELECT COUNT(*) AS n FROM d"
            )
        )
        assert "|" in result
        assert "n" in result

    @pytest.mark.warehouse
    def test_execute_sql_query_rejects_non_select(self):
        """Write statements are refused: this agent is read-only."""
        result = one.tool_execute_sql_query(sql="DROP TABLE tenant_health_snapshots")
        assert "Invalid query" in result

    @pytest.mark.warehouse
    def test_write_hidden_behind_a_cte_does_not_land(self):
        """
        SQLite accepts ``WITH x AS (...) DELETE FROM ...``, so the opening
        keyword alone cannot decide this. What protects the data is that every
        agent query runs in a transaction that is always rolled back -- so the
        assertion is on the row count, not on an error string.
        """
        before = one.tool_execute_sql_query(sql="SELECT COUNT(*) AS n FROM tenant_health_snapshots")
        one.tool_execute_sql_query(
            sql=(
                "WITH d AS (SELECT snapshot_id FROM tenant_health_snapshots) "
                "DELETE FROM tenant_health_snapshots"
            )
        )
        after = one.tool_execute_sql_query(sql="SELECT COUNT(*) AS n FROM tenant_health_snapshots")
        assert before == after

    @pytest.mark.warehouse
    def test_execute_sql_query_reports_bad_sql(self):
        """A broken query returns an error string rather than raising."""
        result = one.tool_execute_sql_query(sql="SELECT * FROM no_such_table")
        assert result.startswith("Error")

    def test_write_debug_report(self):
        """The debug report tool writes the file and returns its path."""
        content = "# Debug Report\n\nHello from the test suite.\n"
        result = one.tool_write_debug_report(content=content)
        assert path_enum.path_debug_report_md.exists()
        assert path_enum.path_debug_report_md.read_text(encoding="utf-8") == content
        assert str(path_enum.path_debug_report_md) in result


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.one.one_04_agent",
        preview=False,
    )


class TestLoadStrandsModelClass:
    """
    The lookup that turns a provider name into a strands class. No client is
    constructed, so no key and no network -- getattr on an already-imported
    module is the whole function.
    """

    def test_finds_a_shipped_class(self):
        from agent_app.one.one_04_agent import load_strands_model_class

        assert load_strands_model_class("BedrockModel", "bedrock").__name__ == (
            "BedrockModel"
        )

    def test_a_missing_class_names_the_extra_to_install(self):
        """
        The message has to say which extra, or the reader is left guessing which
        of four optional SDKs the failure is about.
        """
        from agent_app.one.one_04_agent import load_strands_model_class

        with pytest.raises(ImportError, match="anthropic"):
            load_strands_model_class("NoSuchModel", "anthropic")


class TestModelDispatch:
    """
    Which provider gets which strands class. Nothing is constructed for real:
    the loader is replaced, so this checks the branch mapping and the arguments
    each provider is handed -- the part that is this repo's code.

    Getting a branch wrong does not raise; it silently talks to the wrong
    vendor with the wrong key.
    """

    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    @pytest.fixture
    def one_with(self, monkeypatch):
        from agent_app.config import Config
        from agent_app.one import one_04_agent
        from agent_app.one.one_00_main import One

        loaded = {}

        def fake_loader(class_name, provider):
            loaded["class_name"] = class_name
            loaded["provider"] = provider
            return TestModelDispatch.FakeModel

        monkeypatch.setattr(one_04_agent, "load_strands_model_class", fake_loader)

        def build(**config_kwargs):
            instance = One()
            instance.__dict__["config"] = Config(schema_name="acme_widgets", **config_kwargs)
            return instance, loaded

        return build

    def test_anthropic(self, one_with):
        one, loaded = one_with(llm_provider="anthropic", anthropic_api_key="k")
        model = one.model
        assert loaded["class_name"] == "AnthropicModel"
        assert model.kwargs["client_args"] == {"api_key": "k"}
        # Required by this provider and by no other.
        assert "max_tokens" in model.kwargs

    def test_openai_targets_the_responses_endpoint(self, one_with):
        """
        NOT OpenAIModel: the reasoning models refuse to combine function tools
        with reasoning on /v1/chat/completions, and this agent is nothing but
        tools.
        """
        one, loaded = one_with(llm_provider="openai", openai_api_key="k")
        model = one.model
        assert loaded["class_name"] == "OpenAIResponsesModel"
        assert model.kwargs["client_args"] == {"api_key": "k"}
        assert "max_tokens" not in model.kwargs

    def test_google(self, one_with):
        one, loaded = one_with(llm_provider="google", google_api_key="k")
        assert one.model.kwargs["client_args"] == {"api_key": "k"}
        assert loaded["class_name"] == "GeminiModel"

    def test_bedrock_takes_the_session_path(self, one_with, monkeypatch):
        """Bedrock authenticates with a session, so it skips the key branches."""
        from agent_app.one import one_04_agent

        one, _ = one_with(llm_provider="bedrock", aws_region="us-east-1")
        session = object()
        monkeypatch.setitem(one.__dict__, "boto_ses", session)
        monkeypatch.setattr(one_04_agent, "BedrockModel", TestModelDispatch.FakeModel)
        assert one.model.kwargs == {"boto_session": session, "model_id": one.config.model_id}

    def test_sql_writer_model_has_its_own_id_and_temperature(self, one_with):
        one, _ = one_with(
            llm_provider="google",
            google_api_key="k",
            model_id="small",
            sql_writer_model_id="large",
        )
        assert one.model.kwargs["model_id"] == "small"
        assert "params" not in one.model.kwargs
        assert one.sql_writer_model.kwargs["model_id"] == "large"
        assert one.sql_writer_model.kwargs["params"] == {"temperature": 0.0}

    def test_sql_writer_on_another_provider(self, one_with):
        """The agent stays where it is; only the writer moves, with its own key."""
        one, loaded = one_with(
            llm_provider="google",
            google_api_key="g",
            sql_writer_provider="anthropic",
            sql_writer_model_id="big",
            anthropic_api_key="a",
            sql_writer_temperature=None,
        )
        writer = one.sql_writer_model
        assert loaded["class_name"] == "AnthropicModel"
        assert writer.kwargs["client_args"] == {"api_key": "a"}
        assert writer.kwargs["model_id"] == "big"
        assert one.model.kwargs["client_args"] == {"api_key": "g"}

    def test_anthropic_effort_and_caching(self, one_with):
        """
        The agent caches its conversation (a tool loop re-reads it); the
        writer does not (every call is a fresh one-message conversation).
        """
        one, _ = one_with(
            llm_provider="anthropic",
            anthropic_api_key="k",
            llm_effort="low",
            sql_writer_effort="medium",
            sql_writer_temperature=None,
        )
        assert one.model.kwargs["params"] == {
            "output_config": {"effort": "low"},
            "cache_control": {"type": "ephemeral"},
        }
        assert one.sql_writer_model.kwargs["params"] == {"output_config": {"effort": "medium"}}

    def test_effort_is_anthropic_only(self, one_with):
        one, _ = one_with(llm_provider="google", google_api_key="k", llm_effort="low")
        assert "params" not in one.model.kwargs

    def test_sql_writer_provider_key_is_checked(self, one_with):
        one, _ = one_with(llm_provider="google", google_api_key="g", sql_writer_provider="anthropic")
        _ = one.model  # the agent's own key is fine
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            _ = one.sql_writer_model

    def test_sql_writer_defaults_to_the_agent_model(self, one_with):
        one, _ = one_with(
            llm_provider="openai", openai_api_key="k", model_id="m", sql_writer_temperature=None
        )
        assert one.sql_writer_model.kwargs["model_id"] == "m"
        # None means send nothing: reasoning models reject any temperature.
        assert "params" not in one.sql_writer_model.kwargs

    def test_a_missing_key_fails_before_any_request(self, one_with):
        """
        validate() runs first so the failure is readable here rather than an
        authentication error on the first user question.
        """
        one, _ = one_with(llm_provider="openai")
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            _ = one.model


class TestMcpClient:
    """Which transport the agent reaches its tools over. Nothing connects."""

    @pytest.fixture
    def build(self, monkeypatch):
        from agent_app.config import Config
        from agent_app.one import one_04_agent
        from agent_app.one.one_00_main import One

        made = {}

        def fake_client(factory, tool_filters=None):
            made["factory"] = factory
            made["filters"] = tool_filters
            return "client"

        monkeypatch.setattr(one_04_agent, "MCPClient", fake_client)
        monkeypatch.setattr(one_04_agent, "stdio_client", lambda params: ("stdio", params))
        monkeypatch.setattr(one_04_agent, "streamablehttp_client", lambda url: ("http", url))

        def make(**config_kwargs):
            instance = One()
            instance.__dict__["config"] = Config(schema_name="acme_widgets", **config_kwargs)
            assert instance.mcp_client == "client"
            return made["factory"]()

        return make

    def test_stdio_child_process_by_default(self, build, monkeypatch):
        monkeypatch.setenv("ACME_SECRET", "s3cret")
        kind, params = build()
        assert kind == "stdio"
        assert params.args == ["-m", "agent_app.mcp_server"]
        # A deployment's credentials arrive as variables; the child needs them.
        assert params.env["ACME_SECRET"] == "s3cret"

    def test_http_when_a_url_is_set(self, build):
        assert build(mcp_server_url="http://tools:9000/mcp") == ("http", "http://tools:9000/mcp")

    def test_the_feedback_tool_is_hidden_from_the_model(self, build, monkeypatch):
        from agent_app.mcp_server.feedback import FEEDBACK_TOOL
        from agent_app.one import one_04_agent

        seen = {}
        original = one_04_agent.MCPClient

        def spy(factory, tool_filters=None):
            seen["filters"] = tool_filters
            return original(factory, tool_filters)

        monkeypatch.setattr(one_04_agent, "MCPClient", spy)
        build()
        assert seen["filters"] == {"rejected": [FEEDBACK_TOOL]}


class TestWriteDebugReportCreatesTmp:
    def test_missing_tmp_directory_is_created(self, monkeypatch, tmp_path):
        """
        A fresh checkout has no tmp/, and the first thing that writes there is
        this tool. Failing would lose the report the agent just wrote.
        """
        from agent_app.paths import path_enum

        missing = tmp_path / "not-created-yet"
        monkeypatch.setattr(path_enum, "dir_tmp", missing)
        monkeypatch.setattr(path_enum, "path_debug_report_md", missing / "debug_report.md")

        result = one.tool_write_debug_report(content="# Report")
        assert (missing / "debug_report.md").read_text() == "# Report"
        assert "debug_report.md" in result


def test_system_prompt_is_sent_as_one_cached_block():
    from agent_app.one.one_04_agent import SYSTEM_PROMPT_CACHE, caching_anthropic_model_class

    class Base:
        def format_request(self, messages, tool_specs=None, system_prompt=None, tool_choice=None):
            return {"messages": messages, **({"system": system_prompt} if system_prompt else {})}

    model = caching_anthropic_model_class(Base)()
    request = model.format_request([], system_prompt="You answer questions about widgets.")
    assert request["system"] == [
        {"type": "text", "text": "You answer questions about widgets.", "cache_control": SYSTEM_PROMPT_CACHE}
    ]
    assert "system" not in model.format_request([])
