# -*- coding: utf-8 -*-

"""
Test for Config.

Signature-level only: build a Config, touch every property and method, and check
the shape of what comes back. No environment mutation, no network, no I/O.
"""

import pytest

from agent_app.config import SCHEMA_NAME_PATTERN, Config
from agent_app.runtime import runtime


class TestConfig:
    def test_defaults(self):
        """A bare Config() fills in the selected provider's default model.

        The default is ``openai`` rather than ``bedrock`` on purpose: it needs
        one API key and no cloud account, which is the lowest barrier for
        someone opening this repo for the first time.
        """
        config = Config()
        assert config.llm_provider == "openai"
        assert config.model_id  # filled in by __post_init__

    def test_sql_writer_defaults(self):
        """The writer falls back to the agent's model, at temperature 0."""
        config = Config(model_id="m")
        assert config.sql_writer_model_id == "m"
        assert config.sql_writer_temperature == 0.0
        assert Config(model_id="m", sql_writer_model_id="w").sql_writer_model_id == "w"
        assert config.sql_writer_provider == config.llm_provider

    def test_sql_writer_on_another_provider_gets_that_providers_default(self):
        """The agent's model ID means nothing to a different provider."""
        from agent_app.constants import DEFAULT_MODEL_ID

        config = Config(llm_provider="bedrock", model_id="nova", sql_writer_provider=" Anthropic ")
        assert config.sql_writer_provider == "anthropic"
        assert config.sql_writer_model_id == DEFAULT_MODEL_ID["anthropic"]

    def test_judge_prefers_another_model_family_with_a_key(self):
        """The judge should not grade its own family's answers."""
        config = Config(llm_provider="anthropic", anthropic_api_key="a", openai_api_key="o")
        assert config.eval_judge_provider == "openai"
        assert not config.judge_is_self_graded

    def test_judge_skips_families_the_agent_or_writer_use(self):
        config = Config(
            llm_provider="anthropic",
            sql_writer_provider="openai",
            anthropic_api_key="a",
            openai_api_key="o",
            google_api_key="g",
        )
        assert config.eval_judge_provider == "google"

    def test_judge_without_another_key_falls_back_to_the_writer_and_says_so(self):
        config = Config(llm_provider="anthropic", anthropic_api_key="a")
        assert (config.eval_judge_provider, config.eval_judge_model_id) == (
            config.sql_writer_provider,
            config.sql_writer_model_id,
        )
        assert config.judge_is_self_graded

    def test_explicit_judge_is_kept(self):
        config = Config(llm_provider="anthropic", eval_judge_provider=" Anthropic ", openai_api_key="o")
        assert config.eval_judge_provider == "anthropic"
        assert config.judge_is_self_graded

    @pytest.mark.parametrize(
        "provider, model_id, family",
        [
            ("anthropic", "claude-x", "anthropic"),
            ("openai", None, "openai"),
            ("bedrock", "us.anthropic.claude-sonnet-x", "anthropic"),
            ("bedrock", "us.amazon.nova-lite-v1:0", "amazon"),
            ("bedrock", "meta.llama3-70b", "meta"),
        ],
    )
    def test_model_family(self, provider, model_id, family):
        from agent_app.config import model_family

        assert model_family(provider, model_id) == family

    def test_bedrock_claude_agent_is_graded_as_anthropic(self):
        """Claude on Bedrock is still Claude: an Anthropic judge would be self-grading."""
        config = Config(
            llm_provider="bedrock",
            model_id="us.anthropic.claude-sonnet-x",
            anthropic_api_key="a",
        )
        assert config.eval_judge_provider == "bedrock"
        assert config.judge_is_self_graded

    def test_tenant_access_off_by_default(self):
        access = Config(warehouse="snowflake", snowflake_role="SHOP_RO").tenant_access
        assert not access.enabled and access.internal_role == "SHOP_RO"
        assert Config().tenant_access.internal_role is None  # postgres: no role in the connection

    def test_tenant_access_from_the_map(self):
        config = Config(
            warehouse="snowflake", snowflake_role="SHOP_RO",
            tenant_role_map={"ada": "SHOP_PLANT_NORTH"}, tenant_default_role="SHOP_GUEST",
        )
        access = config.tenant_access
        assert access.enabled
        assert access.role_for("ada").role == "SHOP_PLANT_NORTH"
        assert access.role_for("cy").role == "SHOP_GUEST"

    def test_tenant_roles_must_be_identifiers(self):
        with pytest.raises(ValueError, match="TENANT_ROLE_MAP"):
            Config(tenant_role_map={"ada": "SHOP RO"})
        with pytest.raises(ValueError, match="TENANT_DEFAULT_ROLE"):
            Config(tenant_default_role="SHOP_RO; DROP")

    def test_fast_path_is_off_unless_asked_for(self, monkeypatch):
        assert Config().fast_path is False
        monkeypatch.setenv("FAST_PATH", "true")
        assert Config.new_from_env_var().fast_path is True
        monkeypatch.setenv("FAST_PATH", "nonsense")
        assert Config.new_from_env_var().fast_path is False

    def test_tenant_role_table_must_be_a_table_name(self, monkeypatch):
        assert Config(tenant_role_table="CITYPULSE_AUDIT.USER_ROLE_ACCESS").tenant_role_table
        with pytest.raises(ValueError, match="TENANT_ROLE_TABLE"):
            Config(tenant_role_table="audit.users; DROP TABLE x")
        monkeypatch.setenv("TENANT_ROLE_TABLE", "")
        assert Config.new_from_env_var().tenant_role_table is None

    def test_tenant_map_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("TENANT_ROLE_MAP", "ada=SHOP_PLANT_NORTH, bo=SHOP_RO")
        monkeypatch.setenv("TENANT_DEFAULT_ROLE", "")
        config = Config.new_from_env_var()
        assert config.tenant_role_map == {"ada": "SHOP_PLANT_NORTH", "bo": "SHOP_RO"}
        assert config.tenant_default_role is None

    def test_unknown_sql_writer_provider(self):
        with pytest.raises(ValueError, match="SQL_WRITER_PROVIDER"):
            Config(sql_writer_provider="llama")

    @pytest.mark.parametrize(
        "raw, expected",
        [(None, 0.0), ("", 0.0), ("0.2", 0.2), ("none", None), ("NONE", None), ("hot", 0.0)],
    )
    def test_parse_temperature(self, raw, expected):
        from agent_app.config import parse_temperature

        assert parse_temperature(raw) == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [(None, None), ("", None), ("low", "low"), (" High ", "high"), ("turbo", None)],
    )
    def test_parse_effort(self, raw, expected):
        from agent_app.config import parse_effort

        assert parse_effort(raw) == expected

    def test_unknown_provider(self):
        """An unsupported LLM_PROVIDER fails loudly instead of silently."""
        with pytest.raises(ValueError):
            Config(llm_provider="llama")

    def test_llm_api_key(self):
        """llm_api_key returns the selected provider's key; bedrock has none."""
        assert Config(llm_provider="anthropic", anthropic_api_key="k").llm_api_key == "k"
        assert Config(llm_provider="openai", openai_api_key="k").llm_api_key == "k"
        assert Config(llm_provider="google", google_api_key="k").llm_api_key == "k"
        assert Config(llm_provider="bedrock").llm_api_key is None

    def test_validate(self):
        """validate() checks only the selected provider's credential."""
        Config(openai_api_key="k").validate()  # the default provider
        Config(llm_provider="bedrock", aws_region="us-east-1").validate()
        with pytest.raises(ValueError):
            Config().validate()  # openai, no key
        with pytest.raises(ValueError):
            Config(llm_provider="bedrock").validate()  # no region

    def test_factories(self):
        """Each factory returns a usable Config."""
        assert isinstance(Config.new_in_local_runtime(), Config)
        assert isinstance(Config.new_in_vercel_runtime(), Config)
        assert isinstance(Config.new(), Config)


class TestQuotaConfig:
    def test_cap_on_without_a_table_is_refused(self):
        """
        Caught at start-up rather than on some user's first request. The check
        used to live in ``quota._table``, where a misconfigured public
        deployment booted fine and failed the moment someone typed.
        """
        with pytest.raises(ValueError, match="DYNAMODB_TABLE_NAME_QUOTA"):
            Config(quota_enabled=True)

    def test_cap_on_with_a_table_is_fine(self):
        assert Config(
            quota_enabled=True, dynamodb_table_name_quota="my-quota"
        ).quota_enabled is True

    def test_cap_off_needs_no_table(self):
        assert Config().quota_enabled is False


class TestFactories:
    """
    Two runtimes, one shared reader, exactly one difference between them.

    ``new_from_env_var`` does the reading; the factories differ only in whether
    they hand it AWS credentials. These tests pin that difference, because it is
    the entire reason there are two factories rather than one.
    """

    def test_new_from_env_var_reads_the_environment(self, monkeypatch):
        monkeypatch.setenv("SCHEMA_NAME", "acme_widgets")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("DB_PORT", "5433")
        monkeypatch.setenv("QUOTA_ENABLED", "true")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME_QUOTA", "my-quota")
        config = Config.new_from_env_var()
        assert config.schema_name == "acme_widgets"
        assert config.llm_provider == "openai"
        assert config.db_port == 5433
        assert config.quota_enabled is True

    def test_new_from_env_var_never_reads_aws_credentials(self, monkeypatch):
        """
        The trap this guards: boto3 checks the environment BEFORE
        ~/.aws/credentials, so a placeholder picked up here would shadow a
        working local profile and Bedrock would fail with an opaque
        UnrecognizedClientException.
        """
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPLACEHOLDER")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shhh")
        config = Config.new_from_env_var()
        assert config.aws_access_key_id is None
        assert config.aws_secret_access_key is None

    def test_overrides_win_over_the_environment(self, monkeypatch):
        """How a runtime states its one difference, instead of copying the lot."""
        monkeypatch.setenv("SCHEMA_NAME", "from_env")
        assert Config.new_from_env_var(schema_name="acme_override").schema_name == (
            "acme_override"
        )

    def test_local_leaves_aws_to_the_credential_chain(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPLACEHOLDER")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shhh")
        config = Config.new_in_local_runtime()
        assert config.aws_access_key_id is None
        assert config.aws_secret_access_key is None

    def test_vercel_takes_aws_from_the_environment(self, monkeypatch):
        """Serverless has no ~/.aws, so there is no chain to fall back to."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREAL")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "real")
        config = Config.new_in_vercel_runtime()
        assert config.aws_access_key_id == "AKIAREAL"
        assert config.aws_secret_access_key == "real"

    def test_new_dispatches_to_the_vercel_factory(self, monkeypatch):
        """
        `new()` exists only to pick a factory, and picking the Vercel one is the
        half that never happens on a laptop. Forcing it here is what stops a
        broken dispatch from shipping green.

        `runtime` caches its answer, so the cache has to be dropped on the way
        in and on the way out -- a stale True would leak into every later test.
        """
        runtime.__dict__.pop("_is_vercel", None)
        try:
            monkeypatch.setenv("VERCEL", "1")
            monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAREAL")
            monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "real")
            assert runtime.is_vercel()
            # Reading AWS from the environment is what only the Vercel factory
            # does, so it is the proof of which branch ran.
            assert Config.new().aws_access_key_id == "AKIAREAL"
        finally:
            runtime.__dict__.pop("_is_vercel", None)

    def test_new_dispatches_to_the_local_factory(self, monkeypatch):
        runtime.__dict__.pop("_is_vercel", None)
        try:
            monkeypatch.delenv("VERCEL", raising=False)
            monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAPLACEHOLDER")
            assert runtime.is_local()
            assert Config.new().aws_access_key_id is None
        finally:
            runtime.__dict__.pop("_is_vercel", None)

    def test_placeholder_reads_as_unset(self, monkeypatch):
        """A .env copied from .env.example with "..." left in must not count."""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "...")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "...")
        config = Config.new_in_vercel_runtime()
        assert config.aws_access_key_id is None
        assert config.aws_secret_access_key is None


class TestSchemaName:
    """
    The dataset's only identifier, from SCHEMA_NAME in the committed
    ``.env.defaults``.

    Checked at construction rather than at first use: the alternative failure is
    a query error from deep inside PostgreSQL, several layers from the typo. The
    value is also interpolated into a ``SET LOCAL search_path`` statement in
    ``one/one_02_db.py``, so it had better be an identifier.
    """

    @pytest.mark.parametrize("ok", ["a", "_private", "acme_widgets", "acme_2", "a" * 63])
    def test_legal_names_are_accepted(self, ok):
        assert Config(schema_name=ok).schema_name == ok
        assert SCHEMA_NAME_PATTERN.fullmatch(ok)

    @pytest.mark.parametrize(
        "bad",
        [
            "My-Schema",  # uppercase and a hyphen
            "acme widgets",  # whitespace
            "2acme",  # leading digit
            "acme;DROP",  # punctuation with no business in an identifier
            "a" * 64,  # one over PostgreSQL's limit
        ],
    )
    def test_illegal_names_are_rejected(self, bad):
        with pytest.raises(ValueError, match="SCHEMA_NAME"):
            Config(schema_name=bad)

    def test_unset_is_allowed_at_construction(self):
        """
        A bare ``Config()`` in a test must not have to know about the dataset.
        An empty schema name fails later, at reflection, with its own message.
        """
        assert Config().schema_name == ""

    def test_committed_default_is_loaded(self):
        """
        The real .env.defaults reaches Config. This is the one test that would
        catch the file being renamed, or Vercel's factory forgetting to load it.
        """
        for config in (Config.new_in_local_runtime(), Config.new_in_vercel_runtime()):
            assert SCHEMA_NAME_PATTERN.fullmatch(config.schema_name)


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.config",
        preview=False,
    )


class TestSystemPromptContent:
    """The prompt is a committed file; reading it needs no infrastructure."""

    def test_reads_the_committed_prompt(self):
        from agent_app.paths import path_enum

        content = path_enum.path_bi_agent_system_prompt_content
        assert isinstance(content, str)
        assert content.strip(), "an empty system prompt would give the agent no brain"

    def test_read_once_per_process(self):
        """cached_property: the agent is rebuilt more often than the file changes."""
        from agent_app.paths import path_enum

        assert (
            path_enum.path_bi_agent_system_prompt_content
            is path_enum.path_bi_agent_system_prompt_content
        )


class TestEnvReaders:
    """
    The three readers between ``os.environ`` and a typed field. Small, but every
    branch is a decision about what a malformed value means, and getting one
    wrong changes behaviour silently rather than raising.
    """

    def test_placeholder_and_unset_both_read_as_none(self, monkeypatch):
        from agent_app.config import PLACEHOLDER, get_env

        monkeypatch.setenv("X_TEST", PLACEHOLDER)
        assert get_env("X_TEST") is None
        monkeypatch.delenv("X_TEST")
        assert get_env("X_TEST") is None

    def test_a_real_value_comes_through(self, monkeypatch):
        from agent_app.config import get_env

        monkeypatch.setenv("X_TEST", "value")
        assert get_env("X_TEST") == "value"

    @pytest.mark.parametrize("spelling", ["1", "true", "TRUE", "Yes", " on "])
    def test_bool_accepts_what_people_actually_type(self, monkeypatch, spelling):
        from agent_app.config import get_env_bool

        monkeypatch.setenv("X_FLAG", spelling)
        assert get_env_bool("X_FLAG") is True

    @pytest.mark.parametrize("spelling", ["0", "false", "NO", "Off"])
    def test_bool_falsy_spellings(self, monkeypatch, spelling):
        from agent_app.config import get_env_bool

        monkeypatch.setenv("X_FLAG", spelling)
        assert get_env_bool("X_FLAG", default=True) is False

    def test_bool_unset_takes_the_default(self, monkeypatch):
        from agent_app.config import get_env_bool

        monkeypatch.delenv("X_FLAG", raising=False)
        assert get_env_bool("X_FLAG", default=True) is True

    def test_bool_gibberish_takes_the_default(self, monkeypatch):
        """A malformed flag should leave its feature off, not stop the app booting."""
        from agent_app.config import get_env_bool

        monkeypatch.setenv("X_FLAG", "maybe")
        assert get_env_bool("X_FLAG") is False
        assert get_env_bool("X_FLAG", default=True) is True

    def test_int_parses(self, monkeypatch):
        from agent_app.config import get_env_int

        monkeypatch.setenv("X_PORT", "5432")
        assert get_env_int("X_PORT") == 5432

    def test_int_unset_or_malformed_is_none(self, monkeypatch):
        """
        None is what callers already treat as "not configured", so a typo in
        DB_PORT degrades instead of raising at import time.
        """
        from agent_app.config import get_env_int

        monkeypatch.delenv("X_PORT", raising=False)
        assert get_env_int("X_PORT") is None
        monkeypatch.setenv("X_PORT", "not-a-number")
        assert get_env_int("X_PORT") is None
