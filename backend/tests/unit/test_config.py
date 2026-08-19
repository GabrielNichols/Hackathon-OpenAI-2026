import traceback
from pathlib import Path

import pytest

from app.config import (
    ApplicationConfigurationError,
    OpenAIInterpreterSettings,
    load_interpreter_provider,
    load_openai_api_key,
)
from app.modules.procurement_agent.demo import create_runtime_container
from app.modules.procurement_requests import OpenAIProcurementInterpreter


def test_openai_key_prefers_environment_and_is_hidden_from_repr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret_file = tmp_path / "openai_api_key.txt"
    secret_file.write_text("file-secret", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")

    assert load_openai_api_key(secret_file) == "environment-secret"
    settings = OpenAIInterpreterSettings.from_environment(secret_file=secret_file)
    assert settings.api_key == "environment-secret"
    assert "environment-secret" not in repr(settings)


def test_openai_key_uses_ignored_file_without_exporting_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CANAL_AGENTE_OPENAI_API_KEY", raising=False)
    secret_file = tmp_path / "openai_api_key.txt"
    secret_file.write_text("file-secret\n", encoding="utf-8")

    assert load_openai_api_key(secret_file) == "file-secret"


def test_configuration_errors_never_echo_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinel = "secret-value-that-must-not-leak"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", sentinel)

    with pytest.raises(ApplicationConfigurationError) as caught:
        OpenAIInterpreterSettings.from_environment(secret_file=tmp_path / "missing")

    assert sentinel not in str(caught.value)
    assert sentinel not in "".join(traceback.format_exception(caught.type, caught.value, caught.tb))


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "61"])
def test_openai_timeout_must_be_finite_and_at_most_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", timeout)

    with pytest.raises(ApplicationConfigurationError) as caught:
        OpenAIInterpreterSettings.from_environment(secret_file=tmp_path / "missing")

    assert str(caught.value) == "INVALID_OPENAI_TIMEOUT_SECONDS"
    assert caught.value.__cause__ is None
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert repr(timeout) not in rendered


@pytest.mark.parametrize("provider", ["local", "auto", "openai"])
def test_interpreter_provider_allowlist(provider: str) -> None:
    assert load_interpreter_provider(provider) == provider


def test_interpreter_provider_rejects_unknown_value() -> None:
    with pytest.raises(ApplicationConfigurationError, match="INVALID_PROCUREMENT_INTERPRETER"):
        load_interpreter_provider("surprise-provider")


def test_runtime_container_uses_openai_only_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_INTERPRETER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-sent")

    container = create_runtime_container()

    assert container.mode == "demo_openai_interpreter"
    assert isinstance(container.interpreter, OpenAIProcurementInterpreter)
