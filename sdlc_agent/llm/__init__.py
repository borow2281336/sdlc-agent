from __future__ import annotations

from ..settings import Settings
from .openai_chat import OpenAIChatLLM
from .yandex_completion import YandexCompletionLLM


def get_llm(settings: Settings):
    provider = (settings.llm_provider or "openai").lower().strip()

    if provider == "yandex":
        if not settings.yandex_api_key:
            raise RuntimeError("YANDEX_API_KEY is required when LLM_PROVIDER=yandex")
        if not settings.yandex_model_uri:
            raise RuntimeError("YANDEX_MODEL_URI (or YANDEX_FOLDER_ID) is required when LLM_PROVIDER=yandex")

        return YandexCompletionLLM(
            api_key=settings.yandex_api_key,
            model_uri=settings.yandex_model_uri,
            base_url=settings.yandex_api_base,
        )

    # default: openai
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")

    return OpenAIChatLLM(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )


__all__ = ["OpenAIChatLLM", "YandexCompletionLLM", "get_llm"]

