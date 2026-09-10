from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, List, Optional


STREAM_HEARTBEAT_CHUNK = "\x00ai_stream_heartbeat\x00"


class BaseModelProvider(ABC):
    @abstractmethod
    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        pass

    @abstractmethod
    def generate_stream(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: Optional[int] = None,
        request_timeout_seconds: Optional[float] = None,
        heartbeat_interval_seconds: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
        disable_thinking: bool = False,
    ) -> Generator[str, None, None]:
        pass

    @abstractmethod
    def multimodal_generate(self, messages: List[Dict[str, Any]], model: str) -> str:
        pass

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        pass

    def get_balance(self) -> Dict[str, Any]:
        return {"supported": False, "message": "Not supported by this provider"}
