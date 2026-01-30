from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAIChatLLM:
    api_key: str
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com"
    timeout_s: int = 60

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.2) -> str:
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=self.timeout_s)
        if resp.status_code >= 400:
            raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise LLMError(f"Unexpected OpenAI response format: {data}") from e

    def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        return self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
