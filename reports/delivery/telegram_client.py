from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


TelegramTransport = Callable[[str, bytes, dict[str, str], int], dict]


@dataclass
class TelegramClient:
    bot_token: str
    chat_id: str
    timeout_seconds: int = 30
    transport: TelegramTransport | None = None

    def send_message(self, text: str) -> dict:
        payload = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode("utf-8")
        return self._request("sendMessage", payload, {"Content-Type": "application/x-www-form-urlencoded"})

    def send_document(self, path: Path, caption: str = "") -> dict:
        boundary = "----apexweeklyreport"
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_bytes = path.read_bytes()
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{self.chat_id}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode(),
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: {mime}\r\n\r\n"
            ).encode(),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        return self._request("sendDocument", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def _request(self, method: str, body: bytes, headers: dict[str, str]) -> dict:
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        try:
            if self.transport:
                return self.transport(url, body, headers, self.timeout_seconds)
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return {"success": bool(payload.get("ok")), "response": payload, "error": None if payload.get("ok") else payload}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return {"success": False, "response": None, "error": str(exc)}

