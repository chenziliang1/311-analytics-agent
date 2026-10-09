# -*- coding: utf-8 -*-

"""
Data model for the JSON the Vercel AI SDK frontend POSTs to this backend.

This file is the Python mirror of one TypeScript type: the request body that
``useChat()`` sends to ``/api/chat``. It is **only** a data model -- it parses
and validates JSON, and it knows nothing about Bedrock, OpenAI, Strands, or any
other thing that might eventually consume the parsed result. Keep it that way:
the moment provider-specific code lands in here, swapping providers means
editing the frontend contract, and those two things have no business being
coupled.

Vendored from the ``vercel-ai-sdk-mate`` package (constants.py + type_defs.py +
model.py, merged into one file). It was inlined rather than installed for two
reasons: it is ~100 lines of pydantic models, which is not worth a dependency
whose release cadence we do not control; and the forward-compatibility
behaviour below is ours, not upstream's.

Forward compatibility is the whole design goal here
---------------------------------------------------
The AI SDK is a fast-moving frontend library. It regularly adds new message
part types and new fields, and a backend that validates strictly will start
rejecting perfectly valid traffic the day someone runs ``pnpm update`` --
failing in Python, far from the change that caused it.

So this model follows the old rule: be strict about what you consume, lenient
about what you ignore.

- Fields the backend actually reads (``messages``, each part's ``type`` and
  ``text``) are required and validated.
- Unknown message part types (``file``, ``source-url``, ``tool-*``, whatever
  ships next) parse into :class:`UnknownUIPart` instead of raising.
- Unknown top-level fields are kept, not rejected (``extra="allow"``).
- ``trigger`` is a plain ``str``, not a ``Literal``, so a new trigger value is
  data rather than a crash.

The net effect: an AI SDK upgrade that adds things keeps working untouched.
Only an upgrade that *removes* something we read can break us -- and that is a
real breaking change, which we do want to hear about loudly.

Reference:

- https://ai-sdk.dev/docs/introduction
- https://ai-sdk.dev/docs/reference/ai-sdk-core/ui-message
- https://ai-sdk.dev/docs/migration-guides/migration-guide-5-0
"""

import typing as T
import enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ------------------------------------------------------------------------------
# Known values
#
# These enums document the values seen in the wild. They are NOT used to
# constrain the fields below -- the fields take plain strings on purpose (see
# the module docstring). Use them for comparisons, so string literals do not
# get sprinkled through the codebase.
# ------------------------------------------------------------------------------
class RequestBodyTriggerEnum(str, enum.Enum):
    """What made the frontend send this request."""

    SUBMIT_MESSAGE = "submit-message"


class MessageRoleEnum(str, enum.Enum):
    """Who a message is from."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class MessagePartTypeEnum(str, enum.Enum):
    """The message part types this file has a dedicated model for."""

    TEXT = "text"
    REASONING = "reasoning"


class MessagePartStateEnum(str, enum.Enum):
    """Whether a part is still arriving or finished."""

    STREAMING = "streaming"
    DONE = "done"


# ------------------------------------------------------------------------------
# Type aliases
# ------------------------------------------------------------------------------
T_REQUEST_BODY_TRIGGER_TYPE = T.Literal["submit-message",]

T_MESSAGE_ROLE_TYPE = T.Literal[
    "system",
    "user",
    "assistant",
]

T_MESSAGE_PART_TYPE_TYPE = T.Literal[
    "text",
    "reasoning",
]

T_MESSAGE_PART_STATE_TYPE = T.Literal[
    "streaming",
    "done",
]

T_RECORD_TYPE = dict[str, T.Any]


# ------------------------------------------------------------------------------
# Message parts
#
# A message is not a string. It is a list of "parts", because one turn can
# carry several kinds of content at once: the text you see, the model's
# reasoning, a file, a tool call. The frontend renders them in order.
# ------------------------------------------------------------------------------
class BaseMessagePart(BaseModel):
    """
    Common base for every message part.

    ``extra="allow"`` is the forward-compatibility switch: when a future AI SDK
    version adds a field to a part we already know about, the field is carried
    along on the parsed object instead of being silently dropped or rejected.
    """

    model_config = ConfigDict(extra="allow")

    #: Discriminates the part. Typed as a plain ``str`` rather than a Literal so
    #: that a part type invented after this file was written still parses.
    type: str = Field()


class TextUIPart(BaseMessagePart):
    """
    Visible text -- what the user typed, or what the model answered.

    Ref: https://ai-sdk.dev/docs/reference/ai-sdk-core/ui-message#textuipart
    """

    type: T.Literal["text"] = Field(default=MessagePartTypeEnum.TEXT.value)
    text: str = Field()
    state: str | None = Field(default=None)


class ReasoningUIPart(BaseMessagePart):
    """
    The model's thinking, shown in the UI as a collapsible block.

    Ref: https://ai-sdk.dev/docs/reference/ai-sdk-core/ui-message#reasoninguipart
    """

    type: T.Literal["reasoning"] = Field(default=MessagePartTypeEnum.REASONING.value)
    text: str = Field()
    state: str | None = Field(default=None)
    providerMetadata: T_RECORD_TYPE | None = Field(default=None)


class UnknownUIPart(BaseMessagePart):
    """
    Any part type this file does not model yet.

    This class is the reason an AI SDK upgrade cannot break the backend by
    adding a part type. The raw JSON survives on the instance (thanks to
    ``extra="allow"``) so it can be logged and inspected, and ``type`` tells
    you what it claimed to be -- but nothing here pretends to understand it.

    If one of these starts showing up in your logs and you need its contents,
    that is the signal to write a real model for it and register it in
    :data:`PART_MODEL_BY_TYPE`.
    """


#: Which model class to build for each known ``type`` string. Anything missing
#: from this mapping becomes an :class:`UnknownUIPart`. To support a new part
#: type, write the class above and add one line here -- nothing else changes.
PART_MODEL_BY_TYPE: dict[str, type[BaseMessagePart]] = {
    MessagePartTypeEnum.TEXT.value: TextUIPart,
    MessagePartTypeEnum.REASONING.value: ReasoningUIPart,
}

T_PART = T.Union[
    TextUIPart,
    ReasoningUIPart,
    UnknownUIPart,
]


def parse_message_part(raw: T.Any) -> T.Any:
    """
    Turn one raw part (a dict from JSON) into the right model class.

    This replaces pydantic's discriminated union, which is the usual way to do
    this but is exactly wrong here: a discriminated union raises on any
    ``type`` it has never heard of, which is the failure mode this file exists
    to prevent.

    Args:
        raw: One entry of a message's ``parts`` list, normally a dict.

    Returns:
        A :class:`TextUIPart`, :class:`ReasoningUIPart`, or
        :class:`UnknownUIPart`. Non-dict input is passed through untouched so
        that pydantic reports the type error itself, with a good message.

    Note:
        A part whose type IS known but whose payload is malformed (say, a
        ``text`` part with no ``text``) is deliberately NOT downgraded to
        ``UnknownUIPart`` -- it raises. Silently accepting it would hand the
        caller an object missing the very field it is about to read.
    """
    if isinstance(raw, BaseMessagePart):
        return raw
    if not isinstance(raw, dict):
        return raw
    model = PART_MODEL_BY_TYPE.get(raw.get("type"), UnknownUIPart)
    return model(**raw)


# ------------------------------------------------------------------------------
# Messages and the request body
# ------------------------------------------------------------------------------
class Message(BaseModel):
    """
    One turn of the conversation.

    Ref: https://ai-sdk.dev/docs/reference/ai-sdk-core/ui-message
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="")
    #: "user", "assistant", or "system" -- see :class:`MessageRoleEnum`. A plain
    #: ``str`` so an unrecognised role is data the caller can filter, not a 500.
    role: str = Field()
    parts: list[T_PART] = Field(default_factory=list)

    @field_validator("parts", mode="before")
    @classmethod
    def _parse_parts(cls, value: T.Any) -> T.Any:
        """Build each part with :func:`parse_message_part` before validation."""
        if isinstance(value, list):
            return [parse_message_part(part) for part in value]
        return value

    def text_parts(self) -> list[TextUIPart]:
        """
        Every visible-text part of this message, in order.

        Reasoning and unknown parts are left out. Callers want this far more
        often than they want the raw ``parts`` list, and doing it here keeps
        the ``isinstance`` check from being copy-pasted around.
        """
        return [part for part in self.parts if isinstance(part, TextUIPart)]


class RequestBody(BaseModel):
    """
    The whole JSON body the AI SDK frontend POSTs to ``/api/chat``.

    Ref: https://ai-sdk.dev/docs/ai-sdk-ui/chatbot#advanced-trigger-based-routing
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="")
    messages: list[Message] = Field(default_factory=list)
    #: See :class:`RequestBodyTriggerEnum` for the value we know about. Kept as
    #: a plain ``str`` so that a trigger added by a later AI SDK release arrives
    #: as data instead of blowing up request parsing.
    trigger: str = Field(default=RequestBodyTriggerEnum.SUBMIT_MESSAGE.value)
