from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


Transport = Callable[[str, bytes, int], dict]


@dataclass
class GeminiClient:
    api_key: str
    model: str
    timeout_seconds: int = 30
    max_retries: int = 2
    transport: Transport | None = None

    def generate(self, prompt: str) -> dict:
        if not self.api_key:
            return {"success": False, "text": "", "error": "missing_gemini_api_key"}
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt,
                        }
                    ]
                }
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                response = self.transport(url, body, self.timeout_seconds) if self.transport else self._post(url, body)
                text = (
                    response.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                if text:
                    return {"success": True, "text": text, "error": None}
                last_error = "empty_gemini_response"
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
                last_error = str(exc)
            if attempt < self.max_retries:
                time.sleep(min(2**attempt, 5))
        return {"success": False, "text": "", "error": last_error or "gemini_request_failed"}

    def _post(self, url: str, body: bytes) -> dict:
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

