from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class YandexCompletionLLM:
    api_key: str
    model_uri: str
    base_url: str = "https://llm.api.cloud.yandex.net"
    timeout_s: int = 60
    max_tokens: int = 2048

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2) -> str:
        url = f"{self.base_url.rstrip('/')}/foundationModels/v1/completion"
        headers = {
            # Yandex AI Studio API key auth
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }

        yc_messages: list[dict[str, str]] = []
        for m in messages:
            role = str(m.get("role", "user"))
            content = m.get("content")
            if content is None:
                content = m.get("text", "")
            yc_messages.append({"role": role, "text": str(content)})

        payload = {
            "modelUri": self.model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": float(temperature),
                "maxTokens": str(self.max_tokens),
            },
            "messages": yc_messages,
        }

        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=self.timeout_s)
        if resp.status_code >= 400:
            raise LLMError(f"YandexGPT API error {resp.status_code}: {resp.text}")

        data = resp.json()
        try:
            result = data.get("result", data)  
            return result["alternatives"][0]["message"]["text"]
        except Exception as e: 
            raise LLMError(f"Unexpected YandexGPT response format: {data}") from e


    def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        return self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
