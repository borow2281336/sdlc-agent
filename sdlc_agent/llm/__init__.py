
from __future__ import annotations

import os

from ..settings import Settings
from .openai_chat import OpenAIChatLLM
from .yandex_completion import YandexCompletionLLM

__all__ = ["OpenAIChatLLM", "YandexCompletionLLM", "get_llm"]


def get_llm(settings: Settings):
    """
    Returns an LLM client based on provider selection.

    Provider resolution order:
      1) settings.llm_provider (if exists)
      2) env LLM_PROVIDER
      3) default "openai"
    """
    provider = (
        getattr(settings, "llm_provider", None)
        or os.getenv("LLM_PROVIDER")
        or "openai"
    ).lower().strip()

    if provider == "yandex":
        yandex_api_key = (
            getattr(settings, "yandex_api_key", None)
            or os.getenv("YANDEX_API_KEY")
        )
        yandex_model_uri = (
            getattr(settings, "yandex_model_uri", None)
            or os.getenv("YANDEX_MODEL_URI")
        )

        if not yandex_api_key:
            raise RuntimeError("YANDEX_API_KEY is required when LLM_PROVIDER=yandex")
        if not yandex_model_uri:
            raise RuntimeError("YANDEX_MODEL_URI is required when LLM_PROVIDER=yandex")

        # NOTE: adjust argument names if your YandexCompletionLLM signature differs
        return YandexCompletionLLM(api_key=yandex_api_key, model_uri=yandex_model_uri)

    # default: openai
    openai_key = (
        getattr(settings, "openai_api_key", None)
        or os.getenv("OPENAI_API_KEY")
    )
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")

    return OpenAIChatLLM(
        api_key=openai_key,
        model=getattr(settings, "openai_model", None) or os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        base_url=getattr(settings, "openai_base_url", None) or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com",
    )

