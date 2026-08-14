"""Tests for GUI-managed API settings persistence."""

from pathlib import Path

import pytest

from src.config import get_user_settings, save_user_settings
from src.errors import ConfigError


def test_save_and_load_user_settings(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    save_user_settings(
        tushare_token="token-value",
        llm_api_key="key-value",
        llm_base_url="https://example.com/v1",
        llm_model="normal-model",
        llm_model_deep="deep-model",
        path=env_path,
    )

    assert get_user_settings(env_path) == {
        "TUSHARE_TOKEN": "token-value",
        "LLM_API_KEY": "key-value",
        "LLM_BASE_URL": "https://example.com/v1",
        "LLM_MODEL": "normal-model",
        "LLM_MODEL_DEEP": "deep-model",
    }


def test_save_user_settings_rejects_newlines(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="换行符"):
        save_user_settings(
            tushare_token="bad\nvalue",
            llm_api_key="",
            llm_base_url="https://example.com/v1",
            llm_model="model",
            llm_model_deep="deep-model",
            path=tmp_path / ".env",
        )
