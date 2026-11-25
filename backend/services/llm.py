"""Gemini API üzerinden Gemma / Gemini istemcisi. Her çağrı Ticket'tan hak harcar."""
import asyncio
import json
import random
import re
from typing import AsyncIterator, Optional

from config import settings
from services.rate_limiter import RateLimitError, Ticket

TRANSIENT_CODES = {500, 502, 503, 504}
RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 12.0)


async def _retry_sleep(attempt: int) -> None:
    """Exponential backoff + %0-25 jitter."""
    await asyncio.sleep(RETRY_DELAYS[attempt] * random.uniform(1.0, 1.25))


class LLMError(Exception):
    pass


def parse_json(text: Optional[str]) -> Optional[dict]:
    """Model çıktısından JSON'u toleranslı ayıkla (kod çiti, öncesi/sonrası metin)."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


class GemmaClient:
    def __init__(self):
        from google import genai

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY tanımlı değil (.env dosyasına ekle)")
        self._client = genai.Client(api_key=settings.gemini_api_key)

    @staticmethod
    def _merge(prompt: str, system: Optional[str]) -> str:
        # Gemma'da system_instruction desteği garanti değil: talimatı prompt'a göm.
        return f"{system}\n\n---\n\n{prompt}" if system else prompt

    def _config(self, temperature: Optional[float], json_mode: bool = False, model: str = ""):
        from google.genai import types

        kw = {"temperature": settings.llm_temperature if temperature is None else temperature}
        if json_mode and not model.lower().startswith("gemma"):
            kw["response_mime_type"] = "application/json"
        return types.GenerateContentConfig(**kw)

    @staticmethod
    def _models() -> list[str]:
        models = [settings.llm_model]
        fb = settings.llm_fallback_model.strip()
        if fb and fb != settings.llm_model:
            models.append(fb)
        return models

    @staticmethod
    def _is_transient(e: Exception) -> bool:
        from google.genai import errors

        return isinstance(e, errors.APIError) and getattr(e, "code", None) in TRANSIENT_CODES

    @staticmethod
    def _map_error(e: Exception) -> Exception:
        from google.genai import errors

        if isinstance(e, errors.APIError):
            if getattr(e, "code", None) == 429:
                return RateLimitError("provider", 60, "Google API 429 döndü; biraz bekleyip tekrar dene.")
            return LLMError(f"Gemini API hatası: {getattr(e, 'message', str(e))}")
        return LLMError(str(e))

    async def generate(self, prompt: str, ticket: Ticket, system: Optional[str] = None,
                       temperature: Optional[float] = None, json_mode: bool = False) -> str:
        ticket.use()
        merged = self._merge(prompt, system)
        last_err: Optional[Exception] = None
        for model in self._models():
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    r = await self._client.aio.models.generate_content(
                        model=model, contents=merged,
                        config=self._config(temperature, json_mode=json_mode, model=model))
                    return r.text or ""
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if self._is_transient(e) and attempt < len(RETRY_DELAYS):
                        await _retry_sleep(attempt)
                        continue
                    break
            if not (last_err and (self._is_transient(last_err) or getattr(last_err, "code", None) == 429)):
                break
        raise self._map_error(last_err) from last_err

    async def generate_json(self, prompt: str, ticket: Ticket, system: Optional[str] = None,
                            temperature: Optional[float] = 0.0) -> Optional[dict]:
        text = await self.generate(
            prompt + "\n\nReturn ONLY a valid JSON object, no markdown, no explanation.",
            ticket, system, temperature, json_mode=True)
        return parse_json(text)

    async def stream(self, prompt: str, ticket: Ticket, system: Optional[str] = None,
                     temperature: Optional[float] = None) -> AsyncIterator[str]:
        ticket.use()
        merged = self._merge(prompt, system)
        last_err: Optional[Exception] = None
        for model in self._models():
            for attempt in range(len(RETRY_DELAYS) + 1):
                yielded = False
                try:
                    s = await self._client.aio.models.generate_content_stream(
                        model=model, contents=merged,
                        config=self._config(temperature, model=model))
                    async for chunk in s:
                        if chunk.text:
                            yielded = True
                            yield chunk.text
                    return
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if yielded:
                        raise self._map_error(e) from e
                    if self._is_transient(e) and attempt < len(RETRY_DELAYS):
                        await _retry_sleep(attempt)
                        continue
                    break
            if not (last_err and (self._is_transient(last_err) or getattr(last_err, "code", None) == 429)):
                break
        raise self._map_error(last_err) from last_err
