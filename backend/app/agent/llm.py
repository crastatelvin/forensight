"""LLM adapter (P5.B1).

Deviation from the plan, stated plainly: this is a `ChatModel` Protocol with one
OpenAI-compatible HTTP implementation, not LiteLLM.

Reasoning: every provider we need — OpenAI, Azure, Groq, Together, and Ollama
itself — speaks the OpenAI chat-completions shape over HTTP, and `httpx` is
already a dependency. LiteLLM is a ~50 MB package whose value is multi-provider
routing, cost accounting, and fallbacks we do not use yet. Adding it now buys
none of that and makes the provider layer harder to test. When we have three
real providers and a need for failover, `LiteLLMModel` slots in beside this one
and `resolve_model` picks it from the same env var — no caller changes.

What that does buy, and what the tests rely on: the model is a Protocol, so the
whole agent runs against a scripted fake with no network at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.agent.repository import EMBED_DIM


class ModelError(RuntimeError):
    """The provider could not be reached or returned an unusable response."""


@dataclass
class ChatMessage:
    role: str
    content: str
    #: Tool calls the model requested, if the provider supports them.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ChatResponse:
    #: Defaults to "" so a tool-call-only response does not need a content field.
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    finish_reason: str = ""

    def first_tool_call(self) -> dict[str, Any] | None:
        return self.tool_calls[0] if self.tool_calls else None


@runtime_checkable
class ChatModel(Protocol):
    """What the graph needs from a model. Two methods, no more."""

    name: str

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatModel:
    """Chat + embeddings over any OpenAI-compatible `/v1` endpoint.

    `temperature` defaults to 0.0 on every call: an investigative agent should be
    reproducible given the same inputs, and sampling is one more source of
    variation in a system whose output has to be defensible.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        embed_model: str = "",
        api_key: str = "",
        timeout: float = 60.0,
        embed_dim: int = 1024,
        max_tokens: int = 1024,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embed_model = embed_model or model
        self.api_key = api_key
        self.timeout = timeout
        self.embed_dim = embed_dim
        self.max_tokens = max_tokens

    def _client(self) -> Any:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.Client(timeout=self.timeout, headers=headers)

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.__dict__ for m in messages],
            "temperature": temperature,
            "stream": False,
            # Bounded, because the alternative is unbounded. On a CPU-only host a 3B
            # model generates at about 7 tokens/second, so an unbounded step is tens of
            # seconds of CPU and can outlast the HTTP timeout above. See
            # `Settings.llm_max_tokens` for the measurement.
            "max_tokens": self.max_tokens,
        }
        if tools:
            # Normalise to the OpenAI wire format here rather than trusting the
            # caller. A flat catalog is accepted by the endpoint without error
            # and simply ignored, so the model is never told the tools exist.
            payload["tools"] = [
                entry
                if entry.get("type") == "function" and "function" in entry
                else {
                    "type": "function",
                    "function": {
                        "name": entry["name"],
                        "description": entry.get("description", ""),
                        "parameters": entry.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
                for entry in tools
            ]
            payload["tool_choice"] = "auto"

        try:
            with self._client() as client:
                resp = client.post(f"{self.base_url}/chat/completions", json=payload)
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001 - surface as one error type
            raise ModelError(f"chat completion failed: {exc}") from exc

        choices = body.get("choices") or []
        if not choices:
            raise ModelError("provider returned no choices")
        message = choices[0].get("message") or {}
        return ChatResponse(
            content=message.get("content") or "",
            tool_calls=message.get("tool_calls") or [],
            model=body.get("model", self.model),
            finish_reason=choices[0].get("finish_reason", ""),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.embed_model, "input": texts}
        try:
            with self._client() as client:
                resp = client.post(f"{self.base_url}/embeddings", json=payload)
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ModelError(f"embedding request failed: {exc}") from exc

        data = body.get("data") or []
        vectors = [item.get("embedding") or [] for item in data]
        if len(vectors) != len(texts):
            raise ModelError(f"provider returned {len(vectors)} embeddings for {len(texts)} inputs")
        for vec in vectors:
            if len(vec) != self.embed_dim:
                raise ModelError(f"embedding has dimension {len(vec)}, expected {self.embed_dim}")
        return vectors


def resolve_model(settings: Any) -> ChatModel:
    """Pick a provider from configuration alone — no code change to switch (T5.1).

    `LLM_PROVIDER` decides: `ollama` for a local model, `cloud` (or anything
    else) for the hosted default.
    """
    # Before anything is chosen, so a disallowed provider cannot be reached by
    # picking a different branch below.
    from app.agent.egress import check_settings

    check_settings(settings)

    provider = str(getattr(settings, "llm_provider", "")).strip().lower()
    model = getattr(settings, "llm_model", "") or ""
    base = getattr(settings, "llm_base_url", "") or ""
    key = getattr(settings, "llm_api_key", "") or ""
    embed_model = getattr(settings, "llm_embed_model", "") or ""
    dim = int(getattr(settings, "embed_dim", EMBED_DIM) or EMBED_DIM)
    max_tokens = int(getattr(settings, "llm_max_tokens", 1024) or 1024)

    if provider == "ollama":
        return OpenAICompatModel(
            name="ollama",
            base_url=base or "http://ollama:11434/v1",
            model=model or "qwen2.5",
            embed_model=embed_model or "bge-m3",
            api_key=key or "ollama",  # Ollama ignores it but requires the header
            embed_dim=dim,
            max_tokens=max_tokens,
        )
    if not model:
        raise ModelError("no LLM configured: set LLM_PROVIDER and LLM_MODEL (see .env.example)")
    return OpenAICompatModel(
        name="cloud",
        base_url=base or "https://api.openai.com/v1",
        model=model,
        embed_model=embed_model or model,
        api_key=key,
        embed_dim=dim,
        max_tokens=max_tokens,
    )


class ScriptedModel:
    """Test double: replays a fixed list of responses.

    Included in the package rather than the test directory because the whole
    point of the Protocol is that the agent is testable without a network, and a
    consumer reproducing that needs the same double.
    """

    def __init__(
        self, responses: list[ChatResponse], *, embeddings: list[list[float]] | None = None
    ):
        self.name = "scripted"
        self._responses = list(responses)
        self._embeddings = embeddings or []
        self.calls: list[list[ChatMessage]] = []
        self.tool_specs: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        self.calls.append(list(messages))
        self.tool_specs = tools or []
        if not self._responses:
            return ChatResponse(content="", finish_reason="stop")
        return self._responses.pop(0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._embeddings:
            raise ModelError("ScriptedModel has no embeddings configured")
        return [list(self._embeddings[0]) for _ in texts]


def parse_json_block(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from a model reply.

    Models wrap JSON in prose and fences even when told not to. Returns None
    rather than raising, because the caller treats an unparseable reply as "the
    model did not produce a tool call", not as a crash.
    """
    text = text.strip()
    if text.startswith("```"):
        body = text.split("\n", 1)[-1]
        if body.endswith("```"):
            body = body.rsplit("```", 1)[0]
        text = body.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def env_provider_name() -> str:
    """Which provider the environment selects. Used in reports and tests."""
    return os.environ.get("LLM_PROVIDER", "unset")
