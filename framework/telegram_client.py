"""
Telegram Client

Asynchronous Telegram Bot API client for sending formatted messages.
Uses standard library urllib wrapped in asyncio.to_thread for non-blocking I/O.
"""

import asyncio
import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


def escape_html(text: Any) -> str:
    """Escape special characters for Telegram HTML parse mode."""
    if text is None:
        return ""
    s = str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramClient:
    """Lightweight, asynchronous Telegram Bot API client."""
    
    BASE_URL = "https://api.telegram.org"
    MAX_MESSAGE_LENGTH = 4096
    
    def __init__(
        self,
        token: str,
        chat_id: str,
        logger: Optional[logging.Logger] = None,
        timeout: float = 10.0,
        ssl_verify: bool = True
    ):
        self.token = token.strip()
        self.chat_id = str(chat_id).strip()
        self.logger = logger or logging.getLogger("telegram_client")
        self.timeout = timeout
        self.ssl_verify = ssl_verify
        self._ssl_context = self._build_ssl_context()
        
    def _build_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context using certifi CA bundle or unverified if disabled."""
        if not self.ssl_verify:
            return ssl._create_unverified_context()
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()
        
    def _post_sync(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous POST request to Telegram API."""
        url = f"{self.BASE_URL}/bot{self.token}/{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            self.logger.error(f"Telegram API HTTP error {e.code} for '{endpoint}': {err}")
            raise RuntimeError(f"Telegram HTTP {e.code}: {err}") from e
        except urllib.error.URLError as e:
            self.logger.error(f"Telegram network error for '{endpoint}': {e.reason}")
            raise RuntimeError(f"Telegram network error: {e.reason}") from e

    async def get_me(self) -> Dict[str, Any]:
        """Verify bot credentials by calling getMe."""
        url = f"{self.BASE_URL}/bot{self.token}/getMe"
        def _get():
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as resp:
                return json.loads(resp.read().decode("utf-8"))
        return await asyncio.to_thread(_get)

    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        disable_notification: bool = False
    ) -> bool:
        """Send a message asynchronously, splitting if exceeding 4096 characters."""
        if not text:
            return False
            
        chunks = self._chunk_message(text, self.MAX_MESSAGE_LENGTH)
        success = True
        
        for chunk in chunks:
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
                "disable_notification": disable_notification
            }
            retries = 2
            sent = False
            for attempt in range(retries + 1):
                try:
                    result = await asyncio.to_thread(self._post_sync, "sendMessage", payload)
                    if result.get("ok"):
                        sent = True
                        break
                    self.logger.warning(f"Telegram returned not ok: {result}")
                except Exception as e:
                    self.logger.error(f"Attempt {attempt + 1} failed sending Telegram message: {e}")
                    if attempt < retries:
                        await asyncio.sleep(1.0 * (attempt + 1))
            if not sent:
                success = False
        return success

    @staticmethod
    def _chunk_message(text: str, max_length: int) -> List[str]:
        """Split text into chunks at newline boundaries if possible."""
        if len(text) <= max_length:
            return [text]
        chunks: List[str] = []
        remaining = text
        while len(remaining) > max_length:
            split_idx = remaining.rfind("\n", 0, max_length)
            if split_idx == -1 or split_idx < max_length // 2:
                split_idx = max_length
            chunks.append(remaining[:split_idx].strip())
            remaining = remaining[split_idx:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks
