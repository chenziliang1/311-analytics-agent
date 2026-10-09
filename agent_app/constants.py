# -*- coding: utf-8 -*-

"""
Enums that a *second* module needs.

A constant with one reader belongs next to that reader. Nothing dataset-specific
belongs here at all, and anything varying per machine goes to ``config.py``.
"""

from enum_mate.api import BetterStrEnum


class LlmProviderEnum(BetterStrEnum):
    """Exactly one is active per process, chosen by ``LLM_PROVIDER``."""

    BEDROCK = "bedrock"  # AWS Bedrock, authenticated with AWS credentials
    ANTHROPIC = "anthropic"  # Claude API, needs ANTHROPIC_API_KEY
    OPENAI = "openai"  # OpenAI API, needs OPENAI_API_KEY
    GOOGLE = "google"  # Gemini API, needs GOOGLE_API_KEY


#: Used when ``LLM_MODEL_ID`` is unset. One per provider, since the ID formats
#: are not interchangeable.
#:
#: **These go stale.** Vendors rename and retire models on their own schedules,
#: so on an "unknown model" error check the provider's current list before
#: debugging anything else, and override with ``LLM_MODEL_ID`` rather than
#: editing this dict for a one-off.
DEFAULT_MODEL_ID = {
    LlmProviderEnum.BEDROCK.value: "us.amazon.nova-lite-v1:0",
    LlmProviderEnum.ANTHROPIC.value: "claude-haiku-4-5-20251001",
    LlmProviderEnum.OPENAI.value: "gpt-5.6-luna",
    LlmProviderEnum.GOOGLE.value: "gemini-3.5-flash-lite",
}


class LLMColumnConstraintEnum(BetterStrEnum):
    """Constraint markers in the encoded schema."""

    PK = "PK"  # Primary Key
    UQ = "UQ"  # Unique Key
    IDX = "IDX"  # Index
    FK = "FK"  # Foreign Key
    NN = "NN"  # Not Null


class LLMTypeEnum(BetterStrEnum):
    """The compact type vocabulary the agent is shown, in place of SQL types."""

    STR = "str"  # String/text data of any length
    INT = "int"  # Whole numbers without decimal points
    FLOAT = "float"  # Approximate decimal numbers (IEEE floating point)
    DEC = "dec"  # Exact decimal numbers for currency/financial data
    DT = "dt"  # Date and time combined (local timezone)
    TS = "ts"  # Timestamp with timezone information (UTC)
    DATE = "date"  # Date only without time component
    TIME = "time"  # Time only without date component
    BLOB = "blob"  # Large binary files (images, documents)
    BIN = "bin"  # Small fixed-length binary data (hashes, UUIDs)
    BOOL = "bool"  # True/false boolean values
    NULL = "null"  # Null Type, represents no value


class TableTypeEnum(BetterStrEnum):
    """
    What kind of table-like object a reflected relation is.

    The values are the labels the LLM actually sees, and the label is what tells
    it which relations it cannot write to.
    """

    TABLE = "Table"
    VIEW = "View"
    MATERIALIZED_VIEW = "MaterializedView"


class DbTypeEnum(BetterStrEnum):
    """
    Database engines this app actually runs against.

    Add an entry when the code path that uses it is added, not before: a menu of
    options nothing implements reads as support that does not exist.
    """

    SQLITE = "sqlite"  # local seed and sync source
    POSTGRESQL = "postgresql"  # what the agent reads in every environment
