# -*- coding: utf-8 -*-

"""AI Agent mixin for the One class."""

import os
import sys
import typing as T
from functools import cached_property

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from ..paths import path_enum
from ..constants import LlmProviderEnum
from ..tool_budget import ToolCallBudget
from ..turn_observer import TurnObserver
from ..mcp_server.feedback import FEEDBACK_TOOL
from ..aws_auth import SigV4Auth, agentcore_invocation_url

if T.TYPE_CHECKING:  # pragma: no cover
    from .one_00_main import One


def load_strands_model_class(class_name: str, provider: str):
    """
    Import a strands model class, with an actionable error when its SDK is missing.

    Every provider SDK is a core dependency (see ``pyproject.toml``), so reaching
    the error path means the virtualenv is out of step with the lock file rather
    than that something is opt-in. ``strands.models`` lazy-imports each provider,
    so without this wrapper that surfaces as a bare ``ModuleNotFoundError: No
    module named 'anthropic'``, which does not say what to do about it.

    Args:
        class_name: The strands class to import, e.g. ``"AnthropicModel"``.
        provider: The ``LLM_PROVIDER`` value that asked for it.

    Returns:
        The strands model class.

    Raises:
        ImportError: If the provider's SDK is not importable.
    """
    from strands import models

    try:
        return getattr(models, class_name)
    except (AttributeError, ImportError) as e:
        raise ImportError(
            f"LLM_PROVIDER={provider!r} needs a vendor SDK that is not importable, "
            f"even though it is a core dependency. The virtualenv is probably "
            f"stale.\n"
            f"Reinstall with:\n"
            f"    mise run inst-python-deps\n"
            f"Or switch provider with LLM_PROVIDER in .env.defaults."
        ) from e


#: Cache lifetime for the system prompt. An hour rather than the default five
#: minutes: a demo is used in bursts with long gaps, and every gap past five
#: minutes would otherwise re-send the whole prompt uncached.
SYSTEM_PROMPT_CACHE = {"type": "ephemeral", "ttl": "1h"}


def caching_anthropic_model_class(base):
    """
    ``base`` (strands' AnthropicModel) with the system prompt marked for
    prompt caching.

    Strands sends the system prompt as a plain string, which cannot carry a
    cache marker, so every call re-processed it from scratch: about 4k tokens
    for the agent and 13k for the SQL writer (its prompt plus the schema),
    before the model wrote a word. As one text block with ``cache_control``,
    later calls read it from the cache instead.
    """

    class CachingAnthropicModel(base):
        def format_request(self, messages, tool_specs=None, system_prompt=None, tool_choice=None):
            request = super().format_request(messages, tool_specs, system_prompt, tool_choice)
            system = request.get("system")
            if isinstance(system, str) and system:
                request["system"] = [
                    {"type": "text", "text": system, "cache_control": SYSTEM_PROMPT_CACHE}
                ]
            return request

    return CachingAnthropicModel


class AgentMixin:
    """Mixin providing AI agent and tool definitions for database queries."""

    @cached_property
    def model(self: "One"):
        """The agent's model: the provider selected by ``LLM_PROVIDER``."""
        return self.new_model(effort=self.config.llm_effort)

    @cached_property
    def sql_writer_model(self: "One"):
        """
        The warehouse tool's model: its own provider, ID and temperature, so
        the model every reported number depends on can be raised alone.
        """
        return self.new_model(
            provider=self.config.sql_writer_provider,
            model_id=self.config.sql_writer_model_id,
            temperature=self.config.sql_writer_temperature,
            effort=self.config.sql_writer_effort,
            # Every call is a fresh one-message conversation, so the only
            # prefix two calls share is the system prompt.
            cache_conversation=False,
        )

    def new_model(
        self: "One",
        provider: str | None = None,
        model_id: str | None = None,
        temperature: float | None = None,
        effort: str | None = None,
        cache_conversation: bool = True,
    ):
        """
        Build a model for one provider.

        The credential check happens first so a missing API key fails here,
        with a readable message, rather than as an authentication error on the
        first user question.

        :param provider: None means ``LLM_PROVIDER``.
        :param model_id: None means ``LLM_MODEL_ID``. Only meaningful to its
            own provider.
        :param temperature: None leaves the provider's default.
        :param effort: Anthropic's thinking effort; None leaves the model's
            default. Ignored by the other providers.
        :param cache_conversation: Anthropic only. Also cache the conversation
            so far, so each call in a tool loop re-reads the previous one's
            prefix from the cache. Off for one-shot calls, where it would
            only pay to write a cache entry nothing reads.
        """
        provider = provider or self.config.llm_provider
        self.config.validate(provider)
        model_id = model_id or self.config.model_id
        params = {} if temperature is None else {"temperature": temperature}

        if provider == LlmProviderEnum.BEDROCK.value:  # pragma: no cover - needs AWS
            return BedrockModel(
                boto_session=self.boto_ses,
                model_id=model_id,
                **params,
            )

        if provider == LlmProviderEnum.ANTHROPIC.value:
            anthropic_model_class = caching_anthropic_model_class(
                load_strands_model_class("AnthropicModel", provider)
            )
            if effort:
                params["output_config"] = {"effort": effort}
            if cache_conversation:
                # Automatic caching: the API moves this breakpoint to the end
                # of each request by itself.
                params["cache_control"] = {"type": "ephemeral"}
            return anthropic_model_class(
                client_args={"api_key": self.config.anthropic_api_key},
                model_id=model_id,
                # max_tokens is required by the Anthropic provider, unlike the others.
                max_tokens=self.config.max_tokens,
                **({"params": params} if params else {}),
            )

        if provider == LlmProviderEnum.OPENAI.value:
            # OpenAIResponsesModel targets /v1/responses, NOT /v1/chat/completions.
            # This matters: the reasoning models (gpt-5.x) refuse to combine
            # function tools with reasoning on the chat-completions endpoint --
            #     "Function tools with reasoning_effort are not supported for
            #      <model> in /v1/chat/completions. To use function tools, use
            #      /v1/responses or set reasoning_effort to 'none'."
            # This agent is nothing but tools, so the endpoint is the fix.
            # Setting reasoning_effort='none' would work too, but only for the
            # reasoning models -- the older ones reject the argument outright.
            # Responses is also OpenAI's current primary API, so this keeps
            # working across model generations.
            openai_model_class = load_strands_model_class(
                "OpenAIResponsesModel", provider
            )
            return openai_model_class(
                client_args={"api_key": self.config.openai_api_key},
                model_id=model_id,
                **({"params": params} if params else {}),
            )

        if provider == LlmProviderEnum.GOOGLE.value:
            gemini_model_class = load_strands_model_class("GeminiModel", provider)
            return gemini_model_class(
                client_args={"api_key": self.config.google_api_key},
                model_id=model_id,
                **({"params": params} if params else {}),
            )

        raise NotImplementedError(  # pragma: no cover
            f"No model builder for LLM_PROVIDER={provider!r}."
        )

    @cached_property
    def mcp_client(self: "One") -> MCPClient:
        """
        The connection to the MCP server the data tools live on.

        With ``MCP_SERVER_URL`` unset, the server is this same codebase started
        as a child process over stdio: nothing listens on a port, and it dies
        with the agent. Its environment is passed through whole, because the
        stdio transport otherwise hands a child only a handful of variables,
        and a deployment's database credentials arrive as variables, not files.
        """
        # record_feedback is for the web app, not for the model.
        hidden = {"rejected": [FEEDBACK_TOOL]}
        if self.config.mcp_runtime_arn:
            # Deployed: the tools are their own AgentCore runtime, which only
            # answers requests signed by a role allowed to invoke it.
            region = self.config.aws_region
            runtime_url = agentcore_invocation_url(self.config.mcp_runtime_arn, region)
            auth = SigV4Auth(self.boto_ses, region)
            return MCPClient(lambda: streamablehttp_client(runtime_url, auth=auth), tool_filters=hidden)
        url = self.config.mcp_server_url
        if url:
            return MCPClient(lambda: streamablehttp_client(url), tool_filters=hidden)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_app.mcp_server"],
            cwd=str(path_enum.dir_project_root),
            env=dict(os.environ),
        )
        return MCPClient(lambda: stdio_client(params), tool_filters=hidden)

    @cached_property
    def agent(self: "One") -> Agent:  # pragma: no cover - builds a real Agent
        """
        The agent, with its tools served over MCP.

        It never touches SQL or the database itself: every number comes from
        warehouse_query, which owns the semantic layer, the SQL and the guard.
        Strands starts the MCP client when the agent first needs its tools.
        Write operation tools are dataset specific: when a dataset needs them,
        serve them from the MCP server next to warehouse_query.
        """
        return Agent(
            model=self.model,
            system_prompt=path_enum.path_bi_agent_system_prompt_content,
            tools=[self.mcp_client],
            # Most turns are one or two questions; twelve messages keeps a
            # follow-up's antecedent in view without carrying a long session.
            conversation_manager=SlidingWindowConversationManager(window_size=12),
            # TurnObserver reports progress to the UI and keeps the tool
            # results the answer was written from (see chat.run_agent_turn).
            hooks=[ToolCallBudget(), TurnObserver()],
        )

    @tool(
        name="get_database_schema",
    )
    def tool_get_database_schema(
        # self: "One",  # keep for IDE type hints, strands @tool doesn't support typed self
        self,  # uncomment this and comment above when running with strands
    ) -> str:
        """
        Retrieve the complete database schema information in LLM-optimized compact format.

        This tool returns the structure of all tables in the business database,
        including:
        - Table names and column definitions
        - Data types (simplified to STR, INT, DEC, TS, DT, etc.)
        - Constraints: Primary Key (*PK), Unique (*UQ), Not Null (*NN), Index (*IDX)
        - Foreign key relationships (*FK->Table.Column)

        Use this tool FIRST to understand the database structure before writing SQL queries.
        The compact format reduces token usage by ~70% compared to verbose SQL DDL.

        Returns:
            A string containing the encoded database schema in compact format.
        """
        return self.database_schema_str

    @tool(
        name="execute_sql_query",
    )
    def tool_execute_sql_query(
        # self: "One",  # keep for IDE type hints, strands @tool doesn't support typed self
        self,  # uncomment this and comment above when running with strands
        sql: str,
    ) -> str:
        """
        Execute a SQL SELECT query and return results as a Markdown table.

        This tool runs the provided SQL query against the business database and
        returns the results formatted as a Markdown table.
        Markdown tables are token-efficient (~24% fewer tokens than JSON) and
        easy for LLMs to parse.

        Args:
            sql: A valid SQL SELECT query string to execute.

        Returns:
            - On success: A Markdown-formatted table with query results
            - If no rows match: "No result"
            - On error: An error message describing what went wrong

        Note:
            Only SELECT queries are supported. Use get_database_schema first to
            understand available tables and columns before constructing queries.
        """
        return self.execute_sql_query(sql=sql)

    @tool(
        name="write_debug_report",
    )
    def tool_write_debug_report(
        self,
        content: str,
    ) -> str:
        """
        Write a debug report documenting the reasoning process and intermediate steps.

        This tool writes a markdown report to tmp/debug_report.md for debugging and
        transparency purposes. Use this to document your reasoning process.

        Args:
            content: The full markdown content to write to the debug report file.
                     Should include sections like:
                     - User Question
                     - Schema Analysis
                     - SQL Queries and Results
                     - Reasoning Steps
                     - Final Answer

        Returns:
            A confirmation message with the file path.
        """
        try:
            path_enum.path_debug_report_md.write_text(content, encoding="utf-8")
        except FileNotFoundError:
            path_enum.dir_tmp.mkdir(parents=True, exist_ok=True)
            path_enum.path_debug_report_md.write_text(content, encoding="utf-8")
        return f"Debug report written to: {path_enum.path_debug_report_md}"
