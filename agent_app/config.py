# -*- coding: utf-8 -*-

"""
Everything that varies by machine, read once.

Two files, split by whether a value is a secret -- which is also what decides
who may open them. :func:`load_env_files` reads ``.env`` first so it wins, then
``.env.defaults`` fills in the rest.

There is deliberately no module-level ``config`` singleton: building one would
read the environment at import time, before a test could arrange it.
``ConfigMixin`` gives every caller ``one.config`` lazily instead.
"""

import os
import re
import dataclasses

from .paths import path_enum
from .runtime import runtime
from .constants import LlmProviderEnum, DEFAULT_MODEL_ID
from .tenant_access import parse_role_map


#: The placeholder ``.env.example`` uses for values the reader has to fill in.
PLACEHOLDER = "..."

#: Enforced because the value is interpolated into a ``SET LOCAL search_path``
#: statement in ``one/one_02_db.py``, and because an illegal name otherwise
#: fails deep inside a query with an unhelpful message.
SCHEMA_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: Where an unset EVAL_JUDGE_PROVIDER looks first. API-key providers only: a
#: key in the environment is proof the provider can be called, while Bedrock
#: credentials are not something config can check.
JUDGE_PROVIDER_PREFERENCE = (
    LlmProviderEnum.OPENAI.value,
    LlmProviderEnum.GOOGLE.value,
    LlmProviderEnum.ANTHROPIC.value,
)

#: Bedrock's cross-region inference prefixes, as in ``us.anthropic.claude-...``.
_BEDROCK_REGION_PREFIXES = {"us", "eu", "apac", "global"}


def model_family(provider: str, model_id: str | None) -> str:
    """
    Who trained the model, which is what decides whether a judge is impartial.

    A model grading its own family's answers tends to prefer them, so the
    eval's judge should not share a family with the agent or the SQL writer.
    The API-key providers each serve their own models; Bedrock serves many
    vendors', named in the model ID (``us.anthropic.claude-...``).
    """
    if provider != LlmProviderEnum.BEDROCK.value:
        return provider
    parts = (model_id or "").lower().split(".")
    if len(parts) > 1 and parts[0] in _BEDROCK_REGION_PREFIXES:
        parts = parts[1:]
    return parts[0] or provider


def load_env_files() -> None:
    """
    Load ``.env`` then ``.env.defaults``.

    Order is the whole point: ``load_dotenv`` never overwrites an already-set
    variable, so reading ``.env`` first is what makes it the override layer.
    """
    from dotenv import load_dotenv

    load_dotenv(path_enum.path_env)
    # Between the two files: a secret outranks a committed default, and a
    # local .env still outranks both.
    secret_ids = os.environ.get("APP_SECRET_IDS")
    if secret_ids:
        from .aws_auth import load_secrets_into_env
        from .utils import debug

        names = load_secrets_into_env(secret_ids)
        debug(f"[Config] loaded {len(names)} variables from Secrets Manager: {', '.join(sorted(names))}")
    load_dotenv(path_enum.path_env_defaults)


def get_env(name: str) -> str | None:
    """
    Read an environment variable, treating the ``"..."`` placeholder as unset.

    An uncommented placeholder copied out of ``.env.example`` is set, non-empty
    and useless. boto3 reads env vars before ``~/.aws/credentials``, so it would
    shadow a working local profile and fail with ``UnrecognizedClientException``.
    """
    value = os.environ.get(name)
    if value == PLACEHOLDER:
        return None
    return value


def get_env_bool(name: str, default: bool = False) -> bool:
    """
    Read an environment variable as a boolean.

    An unrecognized spelling falls back to ``default`` rather than raising: a
    malformed flag should leave its feature off, not stop the app booting.
    """
    value = get_env(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_temperature(value: str | None) -> float | None:
    """
    Unset means 0, the reproducible default. ``none`` means "send nothing",
    for reasoning models, which reject any temperature. Malformed falls back
    to 0 rather than stopping the app booting.
    """
    if value is None or not value.strip():
        return 0.0
    if value.strip().lower() == "none":
        return None
    try:
        return float(value)
    except ValueError:
        return 0.0


#: What Anthropic's ``output_config.effort`` accepts, lowest first.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def parse_effort(value: str | None) -> str | None:
    """
    An effort level, or None for the model's own default. Only the anthropic
    provider sends it. An unknown word is dropped rather than sent: the API
    would reject every request over it.
    """
    word = (value or "").strip().lower()
    return word if word in EFFORT_LEVELS else None


def get_env_int(name: str) -> int | None:
    """
    Read an environment variable as an int.

    A malformed value yields None, which callers already treat as "not
    configured", rather than a ValueError at import time.
    """
    value = get_env(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


#: Environment variable holding the credential for each API-key based provider.
#: Bedrock is absent on purpose: it authenticates with AWS credentials, not a key.
PROVIDER_API_KEY_ENV_VAR = {
    LlmProviderEnum.ANTHROPIC.value: "ANTHROPIC_API_KEY",
    LlmProviderEnum.OPENAI.value: "OPENAI_API_KEY",
    LlmProviderEnum.GOOGLE.value: "GOOGLE_API_KEY",
}


@dataclasses.dataclass
class Config:
    """
    Everything that varies by machine. Build it with the factories, read it as
    ``one.config``.

    Only the fields whose behaviour is not obvious from the name are described.

    Attributes:
        schema_name: From ``SCHEMA_NAME`` in ``.env.defaults``; overridable per
            machine in ``.env`` to point one checkout at a staging schema.
        model_id: None means "fill in the provider's default" -- see
            :data:`~agent_app.constants.DEFAULT_MODEL_ID`.
        max_tokens: Required by the Anthropic provider, ignored by the others.
        aws_access_key_id: None means fall through to boto3's credential chain
            (``~/.aws/credentials``, an IAM role), which is what local wants.
        aws_secret_access_key: As above.
        quota_enabled: Off by default. Turning it on needs a DynamoDB table that
            does not exist until someone creates it.
    """

    # --- Dataset ---
    schema_name: str = dataclasses.field(default="")
    # --- LLM ---
    llm_provider: str = dataclasses.field(default=LlmProviderEnum.OPENAI.value)
    model_id: str | None = dataclasses.field(default=None)
    max_tokens: int = dataclasses.field(default=8192)
    #: The provider the warehouse tool writes SQL through. None means
    #: ``llm_provider``.
    sql_writer_provider: str | None = dataclasses.field(default=None)
    #: The model the warehouse tool writes SQL with. None means ``model_id``
    #: when the provider is the same, else that provider's default.
    sql_writer_model_id: str | None = dataclasses.field(default=None)
    #: None leaves the provider's default; reasoning models reject any value.
    sql_writer_temperature: float | None = dataclasses.field(default=0.0)
    #: How hard the agent's model thinks (anthropic only). None is the
    #: model's default, which is ``high`` on Sonnet 5.5.
    llm_effort: str | None = dataclasses.field(default=None)
    #: The same for the SQL writer.
    sql_writer_effort: str | None = dataclasses.field(default=None)
    #: The eval's LLM judge. None means the first provider in
    #: JUDGE_PROVIDER_PREFERENCE that has an API key and a different model
    #: family from both the agent and the writer; with no such provider, the
    #: SQL writer's provider and model, flagged by ``judge_is_self_graded``.
    eval_judge_provider: str | None = dataclasses.field(default=None)
    eval_judge_model_id: str | None = dataclasses.field(default=None)
    # --- Tools ---
    #: Where the MCP server listens. None starts it as a child process over
    #: stdio, which is what local development wants.
    mcp_server_url: str | None = dataclasses.field(default=None)
    #: The MCP server deployed on AgentCore Runtime. Wins over mcp_server_url;
    #: the agent reaches it over HTTPS, signed with its IAM role.
    mcp_runtime_arn: str | None = dataclasses.field(default=None)
    # --- Deployment ---
    #: The agent deployed on AgentCore Runtime. When set, the web app hands
    #: each turn to it instead of running the agent in its own process.
    agentcore_runtime_arn: str | None = dataclasses.field(default=None)
    #: CloudWatch namespace for the operational metrics. None publishes nothing,
    #: which is what local development and tests want.
    metrics_namespace: str | None = dataclasses.field(default=None)
    # --- AWS (used by the bedrock provider) ---
    aws_region: str | None = dataclasses.field(default=None)
    aws_access_key_id: str | None = dataclasses.field(default=None)
    aws_secret_access_key: str | None = dataclasses.field(default=None)
    # --- API keys (one per non-bedrock provider) ---
    anthropic_api_key: str | None = dataclasses.field(default=None)
    openai_api_key: str | None = dataclasses.field(default=None)
    google_api_key: str | None = dataclasses.field(default=None)
    # --- Warehouse ---
    #: Which warehouse the agent's queries run on: ``postgres`` (Neon) or
    #: ``snowflake``. The SQL writer writes PostgreSQL either way; the guard
    #: transpiles to the warehouse's dialect before running it.
    warehouse: str = dataclasses.field(default="postgres")
    snowflake_account: str | None = dataclasses.field(default=None)
    snowflake_user: str | None = dataclasses.field(default=None)
    snowflake_role: str | None = dataclasses.field(default=None)
    snowflake_warehouse: str | None = dataclasses.field(default=None)
    snowflake_database: str | None = dataclasses.field(default=None)
    snowflake_schema: str | None = dataclasses.field(default=None)
    #: The key-pair private key, PEM text. A secret: deployed runtimes get it
    #: from Secrets Manager. Locally, snowflake_private_key_path is simpler.
    snowflake_private_key: str | None = dataclasses.field(default=None)
    snowflake_private_key_path: str | None = dataclasses.field(default=None)
    #: Per-user tenant scope: sign-in login -> the warehouse role that user's
    #: queries run as, so the row access policy sees their tenant's role and
    #: not the service connection's cross-tenant one. Empty, with no default
    #: role: every user is internal and nothing is switched. See tenant_access.
    tenant_role_map: dict[str, str] = dataclasses.field(default_factory=dict)
    #: The role for a signed-in user the map does not name. None refuses
    #: their queries once a map is set: an unknown user is not an internal one.
    tenant_default_role: str | None = dataclasses.field(default=None)
    #: Compile a plain metric question from the semantic layer instead of
    #: asking the SQL writer (semantic_layer.fast_path). Off by default; the
    #: eval with it on and off decides.
    fast_path: bool = dataclasses.field(default=False)
    #: The warehouse table of operator switches (kill, suspend:<metric>), read
    #: every SWITCH_TTL seconds; see agent_app.switches. None: no switches.
    switch_table: str | None = dataclasses.field(default=None)
    switch_ttl_seconds: float = dataclasses.field(default=30.0)
    #: A warehouse table of (login, role_name) rows read as the map, refreshed
    #: every few minutes, with tenant_role_map laid over it: a customer is
    #: added by one row, not a redeploy. The loader creates
    #: CITYPULSE_AUDIT.USER_ROLE_ACCESS for it.
    tenant_role_table: str | None = dataclasses.field(default=None)
    # --- Database ---
    db_host: str | None = dataclasses.field(default=None)
    db_port: int | None = dataclasses.field(default=None)
    db_user: str | None = dataclasses.field(default=None)
    db_pass: str | None = dataclasses.field(default=None)
    db_name: str | None = dataclasses.field(default=None)
    # --- Monthly LLM spend cap (see agent_app/quota.py) ---
    quota_enabled: bool = dataclasses.field(default=False)
    dynamodb_table_name_quota: str | None = dataclasses.field(default=None)
    quota_max_invoke_per_month: int = dataclasses.field(default=1000)
    quota_max_input_token_per_month: int = dataclasses.field(default=50_000_000)
    quota_max_output_token_per_month: int = dataclasses.field(default=10_000_000)

    def __post_init__(self):
        """
        Normalize the provider, fill in its default model ID, check the schema name.

        Here rather than in the factories so that a hand-built ``Config()`` in a
        test behaves exactly like one built from the environment. Both checks
        turn what would otherwise be a failure on some user's first request --
        a PostgreSQL error several layers from the typo, or a metering call
        against a table that was never named -- into a start-up failure.
        """
        if self.schema_name and not SCHEMA_NAME_PATTERN.fullmatch(self.schema_name):
            raise ValueError(
                f"Illegal SCHEMA_NAME {self.schema_name!r}. It must be a "
                f"PostgreSQL identifier -- lowercase letters, digits and "
                f"underscores, not starting with a digit, at most 63 "
                f"characters. Set it in .env.defaults."
            )
        if self.quota_enabled and not self.dynamodb_table_name_quota:
            raise ValueError(
                "QUOTA_ENABLED is on but DYNAMODB_TABLE_NAME_QUOTA is not set. "
                "Point it at a DynamoDB table whose partition key is `app_id` (S), "
                "or turn the cap off in .env.defaults."
            )
        from .tenant_access import check_role_name

        self.tenant_role_map = {
            login: check_role_name(role, "TENANT_ROLE_MAP role")
            for login, role in (self.tenant_role_map or {}).items()
        }
        if self.tenant_default_role is not None:
            check_role_name(self.tenant_default_role, "TENANT_DEFAULT_ROLE")
        if self.tenant_role_table is not None:
            from .tenant_access import check_table_name

            check_table_name(self.tenant_role_table, "TENANT_ROLE_TABLE")
        if self.switch_table is not None:
            from .tenant_access import check_table_name

            check_table_name(self.switch_table, "SWITCH_TABLE")
        self.warehouse = (self.warehouse or "postgres").strip().lower()
        if self.warehouse not in ("postgres", "snowflake"):
            raise ValueError(
                f"Unknown WAREHOUSE {self.warehouse!r}. Expected postgres or snowflake."
            )
        self.llm_provider = (self.llm_provider or "").strip().lower()
        if self.llm_provider not in DEFAULT_MODEL_ID:
            raise ValueError(
                f"Unknown LLM_PROVIDER {self.llm_provider!r}. "
                f"Expected one of: {', '.join(sorted(DEFAULT_MODEL_ID))}."
            )
        if self.model_id is None:
            self.model_id = DEFAULT_MODEL_ID[self.llm_provider]
        self.sql_writer_provider = (
            (self.sql_writer_provider or self.llm_provider).strip().lower()
        )
        if self.sql_writer_provider not in DEFAULT_MODEL_ID:
            raise ValueError(
                f"Unknown SQL_WRITER_PROVIDER {self.sql_writer_provider!r}. "
                f"Expected one of: {', '.join(sorted(DEFAULT_MODEL_ID))}."
            )
        if self.sql_writer_model_id is None:
            # A model ID only means something to its own provider.
            self.sql_writer_model_id = (
                self.model_id
                if self.sql_writer_provider == self.llm_provider
                else DEFAULT_MODEL_ID[self.sql_writer_provider]
            )
        if not self.eval_judge_provider:
            graded = self._graded_families()
            self.eval_judge_provider = next(
                (p for p in JUDGE_PROVIDER_PREFERENCE if self.api_key_for(p) and p not in graded),
                self.sql_writer_provider,
            )
        self.eval_judge_provider = self.eval_judge_provider.strip().lower()
        if self.eval_judge_provider not in DEFAULT_MODEL_ID:
            raise ValueError(
                f"Unknown EVAL_JUDGE_PROVIDER {self.eval_judge_provider!r}. "
                f"Expected one of: {', '.join(sorted(DEFAULT_MODEL_ID))}."
            )
        if self.eval_judge_model_id is None:
            self.eval_judge_model_id = (
                self.sql_writer_model_id
                if self.eval_judge_provider == self.sql_writer_provider
                else DEFAULT_MODEL_ID[self.eval_judge_provider]
            )

    def _graded_families(self) -> set[str]:
        """The model families whose output the eval grades: agent and writer."""
        return {
            model_family(self.llm_provider, self.model_id),
            model_family(self.sql_writer_provider, self.sql_writer_model_id),
        }

    @property
    def tenant_access(self):
        """Who runs as which warehouse role; see :mod:`agent_app.tenant_access`."""
        from .tenant_access import TenantAccess

        return TenantAccess(
            internal_role=self.snowflake_role if self.warehouse == "snowflake" else None,
            user_roles=dict(self.tenant_role_map),
            default_role=self.tenant_default_role,
        )

    @property
    def judge_is_self_graded(self) -> bool:
        """The judge shares a model family with what it grades."""
        return model_family(self.eval_judge_provider, self.eval_judge_model_id) in self._graded_families()

    @property
    def llm_api_key(self) -> str | None:
        """The selected provider's API key. None for bedrock, which uses AWS."""
        return self.api_key_for(self.llm_provider)

    def api_key_for(self, provider: str) -> str | None:
        """``provider``'s API key. None for bedrock, which uses AWS."""
        return {
            LlmProviderEnum.ANTHROPIC.value: self.anthropic_api_key,
            LlmProviderEnum.OPENAI.value: self.openai_api_key,
            LlmProviderEnum.GOOGLE.value: self.google_api_key,
        }.get(provider)

    def validate(self, provider: str | None = None):
        """
        Fail fast when a provider's credential is missing.

        :param provider: None means ``LLM_PROVIDER``. Only the provider about
            to be used is checked; the others being absent is the point of
            switching.
        """
        provider = provider or self.llm_provider
        if provider == LlmProviderEnum.BEDROCK.value:
            # boto3 resolves credentials from env vars, ~/.aws/credentials, or an
            # IAM role, so there is nothing to assert here beyond the region.
            if not self.aws_region:
                raise ValueError(
                    "llm_provider is 'bedrock' but aws_region is not set. "
                    "Set AWS_REGION in your .env file."
                )
            return

        env_var = PROVIDER_API_KEY_ENV_VAR[provider]
        if not self.api_key_for(provider):
            raise ValueError(
                f"llm_provider is {provider!r} but {env_var} is not set. "
                f"Add {env_var}=... to your .env file, or switch provider."
            )

    @classmethod
    def new_from_env_var(cls, **overrides) -> "Config":
        """
        Build a Config from ``os.environ``. Reads nothing off disk.

        AWS credentials are deliberately absent: boto3 reads them before
        ``~/.aws/credentials``, so picking them up here would let a placeholder
        in a local ``.env`` shadow a working profile. The runtime that needs
        them passes them through ``overrides``, which is also what keeps the
        difference between runtimes to an argument list instead of a second copy
        of this call.
        """
        values = dict(
            schema_name=get_env("SCHEMA_NAME") or "",
            llm_provider=get_env("LLM_PROVIDER") or LlmProviderEnum.BEDROCK.value,
            model_id=get_env("LLM_MODEL_ID"),
            sql_writer_provider=get_env("SQL_WRITER_PROVIDER") or None,
            sql_writer_model_id=get_env("SQL_WRITER_MODEL_ID"),
            sql_writer_temperature=parse_temperature(get_env("SQL_WRITER_TEMPERATURE")),
            llm_effort=parse_effort(get_env("LLM_EFFORT")),
            sql_writer_effort=parse_effort(get_env("SQL_WRITER_EFFORT")),
            eval_judge_provider=get_env("EVAL_JUDGE_PROVIDER") or None,
            eval_judge_model_id=get_env("EVAL_JUDGE_MODEL_ID") or None,
            mcp_server_url=get_env("MCP_SERVER_URL") or None,
            mcp_runtime_arn=get_env("MCP_RUNTIME_ARN") or None,
            agentcore_runtime_arn=get_env("AGENTCORE_RUNTIME_ARN") or None,
            metrics_namespace=get_env("METRICS_NAMESPACE") or None,
            aws_region=get_env("AWS_REGION") or "us-east-1",
            anthropic_api_key=get_env("ANTHROPIC_API_KEY"),
            openai_api_key=get_env("OPENAI_API_KEY"),
            google_api_key=get_env("GOOGLE_API_KEY"),
            warehouse=get_env("WAREHOUSE") or "postgres",
            snowflake_account=get_env("SNOWFLAKE_ACCOUNT"),
            snowflake_user=get_env("SNOWFLAKE_USER"),
            snowflake_role=get_env("SNOWFLAKE_ROLE"),
            snowflake_warehouse=get_env("SNOWFLAKE_WAREHOUSE"),
            snowflake_database=get_env("SNOWFLAKE_DATABASE"),
            snowflake_schema=get_env("SNOWFLAKE_SCHEMA"),
            snowflake_private_key=get_env("SNOWFLAKE_PRIVATE_KEY"),
            snowflake_private_key_path=get_env("SNOWFLAKE_PRIVATE_KEY_PATH"),
            tenant_role_map=parse_role_map(get_env("TENANT_ROLE_MAP")),
            tenant_default_role=get_env("TENANT_DEFAULT_ROLE") or None,
            tenant_role_table=get_env("TENANT_ROLE_TABLE") or None,
            fast_path=get_env_bool("FAST_PATH"),
            switch_table=get_env("SWITCH_TABLE") or None,
            db_host=get_env("DB_HOST"),
            db_port=get_env_int("DB_PORT"),
            db_user=get_env("DB_USER"),
            db_pass=get_env("DB_PASS"),
            db_name=get_env("DB_NAME"),
            quota_enabled=get_env_bool("QUOTA_ENABLED"),
            dynamodb_table_name_quota=get_env("DYNAMODB_TABLE_NAME_QUOTA"),
            quota_max_invoke_per_month=get_env_int("QUOTA_MAX_INVOKE_PER_MONTH") or 1000,
            quota_max_input_token_per_month=(
                get_env_int("QUOTA_MAX_INPUT_TOKEN_PER_MONTH") or 50_000_000
            ),
            quota_max_output_token_per_month=(
                get_env_int("QUOTA_MAX_OUTPUT_TOKEN_PER_MONTH") or 10_000_000
            ),
        )
        values.update(overrides)
        return cls(**values)

    @classmethod
    def new_in_local_runtime(cls) -> "Config":
        """
        Build config for local development.

        Passes no AWS credentials, so boto3 falls through to
        ``~/.aws/credentials``.
        """
        load_env_files()
        return cls.new_from_env_var()

    @classmethod
    def new_in_vercel_runtime(cls) -> "Config":
        """
        Build config for Vercel.

        Serverless has no ``~/.aws``, so AWS credentials must come from the
        environment.

        Loading the files here looks pointless but is not: ``.env`` will not
        exist, while ``.env.defaults`` is committed and ships with the
        deployment, so the schema name arrives without anyone re-entering it in
        the dashboard.
        """
        load_env_files()
        return cls.new_from_env_var(
            aws_access_key_id=get_env("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=get_env("AWS_SECRET_ACCESS_KEY"),
        )

    @classmethod
    def new(cls) -> "Config":
        """
        The entry point: detect the runtime, then build the config for it.

        The only place the runtime is consulted, so adding a deployment target
        touches this function and nothing else.

        The final branch is unreachable today -- ``is_local`` is defined as "not
        Vercel" -- and exists so that adding a third target and forgetting this
        function fails loudly rather than silently getting the local config.
        """
        if runtime.is_local():
            return cls.new_in_local_runtime()
        elif runtime.is_vercel():
            return cls.new_in_vercel_runtime()
        else:  # pragma: no cover
            raise RuntimeError(
                "No Config factory for the current runtime. Add a branch here "
                "to agent_app/config.py."
            )
