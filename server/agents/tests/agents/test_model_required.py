"""A server with no AI model connected says so plainly, instead of failing later with a provider's refusal."""

from types import SimpleNamespace

import pytest

from loamz.engine.errors import ServiceUnavailableError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.bootstrap.settings import LlmSettings


@pytest.fixture(autouse=True)
def _no_keys_in_the_environment(monkeypatch):
    for name in (
        "CHAT_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def check(**llm) -> None:
    Container.require_model(SimpleNamespace(settings=SimpleNamespace(llm=LlmSettings(**llm))))  # type: ignore[arg-type]


def test_no_key_is_said_plainly():
    with pytest.raises(ServiceUnavailableError, match="No AI model is connected"):
        check(model="openai/gpt-5.4-mini")


def test_the_key_of_the_models_own_provider_is_what_counts():
    with pytest.raises(ServiceUnavailableError):
        check(model="groq/openai/gpt-oss-120b", openai_api_key="sk-other")
    check(model="groq/openai/gpt-oss-120b", groq_api_key="gsk-x")
    check(model="openai/gpt-5.4-mini", openai_api_key="sk-x")


def test_an_openai_compatible_server_of_your_own_needs_no_key():
    check(model="openai/llama3", openai_base_url="http://127.0.0.1:11434/v1")
