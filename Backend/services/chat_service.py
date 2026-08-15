"""AgriGuard livestock AI chat — Gemini (preferred) or OpenAI."""
from __future__ import annotations

import logging
import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

LANG_NAMES = {
    "EN": "English",
    "ZU": "isiZulu",
    "ST": "Sesotho (Southern Sotho)",
    "AF": "Afrikaans",
}

SYSTEM_PROMPT = """You are AgriGuard AI Assistant — a practical livestock advisor for South African farmers.

Rules:
- Reply entirely in {language_name}.
- Keep answers clear and useful (usually under 220 words unless the farmer asks for detail).
- Use simple markdown: **bold** for short headings, bullet lists with • or -.
- Topics you know well: vaccination schedules, SA breeds (Nguni, Bonsmara, Afrikaner, Dorper, Merino, Boer Goat, etc.), feeding, auctions on AgriGuard, GPS/geofence tracking, health warning signs.
- For serious illness signs, advise isolating the animal and contacting a veterinarian — do not invent a diagnosis.
- If the farmer's herd context is provided, you may refer to their animals by tag/breed.
- If a question is outside farming/livestock/AgriGuard, politely steer back to livestock help.
"""


class ChatService:
    def __init__(self) -> None:
        self.gemini_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
        self.openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        # gemini-2.0-flash was retired; prefer 2.5 Flash (override with GEMINI_MODEL)
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
        self._gemini_fallbacks = [
            m for m in [
                self.gemini_model,
                "gemini-2.5-flash",
                "gemini-flash-latest",
                "gemini-2.5-flash-lite",
            ]
            if m
        ]
        # de-dupe while preserving order
        seen = set()
        self._gemini_fallbacks = [
            m for m in self._gemini_fallbacks if not (m in seen or seen.add(m))
        ]

    def is_configured(self) -> bool:
        return bool(self.gemini_key or self.openai_key)

    def provider(self) -> str | None:
        if self.gemini_key:
            return "gemini"
        if self.openai_key:
            return "openai"
        return None

    def status(self) -> dict[str, Any]:
        provider = self.provider()
        return {
            "configured": bool(provider),
            "provider": provider,
            "languages": list(LANG_NAMES.keys()),
            "hint": None
            if provider
            else "Add GEMINI_API_KEY (or OPENAI_API_KEY) to Backend/.env and restart Flask.",
        }

    def _system_text(self, language: str, animals: list[dict] | None) -> str:
        lang = LANG_NAMES.get((language or "EN").upper(), "English")
        text = SYSTEM_PROMPT.format(language_name=lang)
        if animals:
            lines = []
            for a in animals[:25]:
                tag = a.get("animal_tag") or a.get("tag") or "?"
                species = a.get("species") or ""
                breed = a.get("breed") or ""
                status = a.get("status") or ""
                lines.append(f"- {tag}: {species} {breed} ({status})".strip())
            text += "\n\nFarmer herd context:\n" + "\n".join(lines)
        return text

    def chat(
        self,
        message: str,
        language: str = "EN",
        animals: list[dict] | None = None,
        history: list[dict] | None = None,
    ) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            return {"success": False, "error": "Message is required"}
        if len(message) > 4000:
            return {"success": False, "error": "Message too long (max 4000 characters)"}
        if not self.is_configured():
            return {
                "success": False,
                "error": "AI not configured. Set GEMINI_API_KEY or OPENAI_API_KEY in Backend/.env",
                "code": "AI_NOT_CONFIGURED",
            }

        system = self._system_text(language, animals)
        history = history or []

        try:
            if self.gemini_key:
                reply = self._chat_gemini(system, message, history)
                provider = "gemini"
            else:
                reply = self._chat_openai(system, message, history)
                provider = "openai"
            return {
                "success": True,
                "reply": reply.strip(),
                "provider": provider,
                "language": (language or "EN").upper(),
            }
        except Exception as e:
            logger.exception("Chat provider error")
            return {"success": False, "error": str(e)}

    def _chat_gemini(self, system: str, message: str, history: list[dict]) -> str:
        contents: list[dict] = []
        for turn in history[-12:]:
            role = turn.get("role")
            text = (turn.get("text") or turn.get("content") or "").strip()
            if not text:
                continue
            # Gemini: user / model
            g_role = "user" if role == "user" else "model"
            contents.append({"role": g_role, "parts": [{"text": text}]})
        contents.append({"role": "user", "parts": [{"text": message}]})

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.5,
                "maxOutputTokens": 900,
            },
        }

        last_error = None
        for model in self._gemini_fallbacks:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent"
            )
            r = requests.post(
                url,
                params={"key": self.gemini_key},
                json=payload,
                timeout=45,
            )
            if r.status_code == 404:
                last_error = f"Gemini error 404 for {model}: {r.text[:300]}"
                logger.warning(last_error)
                continue
            if r.status_code >= 400:
                detail = r.text[:400]
                raise RuntimeError(f"Gemini error {r.status_code}: {detail}")

            data = r.json()
            parts = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise RuntimeError("Gemini returned an empty reply")
            self.gemini_model = model
            return text

        raise RuntimeError(last_error or "No Gemini model available")

    def _chat_openai(self, system: str, message: str, history: list[dict]) -> str:
        messages = [{"role": "system", "content": system}]
        for turn in history[-12:]:
            role = turn.get("role")
            text = (turn.get("text") or turn.get("content") or "").strip()
            if not text:
                continue
            messages.append({
                "role": "assistant" if role == "bot" else "user",
                "content": text,
            })
        messages.append({"role": "user", "content": message})

        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.openai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.openai_model,
                "messages": messages,
                "temperature": 0.5,
                "max_tokens": 900,
            },
            timeout=45,
        )
        if r.status_code >= 400:
            detail = r.text[:400]
            raise RuntimeError(f"OpenAI error {r.status_code}: {detail}")
        data = r.json()
        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not text:
            raise RuntimeError("OpenAI returned an empty reply")
        return text


chat_service = ChatService()
