"""OpenAI Speech API adapter and bounded spoken-answer projection.

The dashboard keeps the complete assistant answer as text.  Voice playback
uses a separate, deliberately short projection so a user gets the overview
and core point, then has room to respond before hearing more detail.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterator, Optional


SPOKEN_CONTENT_MAX_CHARS = 180
SPOKEN_MAX_SENTENCES = 3
DETAIL_OFFER = "続けて詳しく説明しましょうか？"
DEFAULT_SPEECH_INSTRUCTIONS = (
    "自然で落ち着いた日本語で話してください。短い文を使い、"
    "文と文の間にわずかな間を置いてください。説明調になりすぎず、"
    "重要な点の後はユーザーが考えられる余韻を残してください。"
)

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_BARE_URL_RE = re.compile(r"https?://\S+")
_LINE_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|>\s*)")
_SENTENCE_RE = re.compile(r".*?(?:[。！？!?]+|$)", re.DOTALL)


def _plain_spoken_text(answer: str) -> str:
    """Remove visual-only markup before sending text to speech."""
    lines = []
    for raw_line in answer.splitlines():
        line = _LINE_PREFIX_RE.sub("", raw_line).strip()
        if line:
            lines.append(line)
    text = " ".join(lines)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _BARE_URL_RE.sub("", text)
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def spoken_summary(answer: str) -> Optional[str]:
    """Return at most three short opening sentences plus a detail offer.

    Voice-mode prompting puts the overview first.  This deterministic bound
    remains the final safety net for fallbacks and unexpectedly verbose model
    output, preventing the player from launching into a long monologue.
    """
    text = _plain_spoken_text(answer)
    if not text:
        return None

    selected = []
    used = 0
    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        remaining = SPOKEN_CONTENT_MAX_CHARS - used
        if remaining <= 0 or len(selected) >= SPOKEN_MAX_SENTENCES:
            break
        if len(sentence) > remaining:
            if not selected:
                cut = sentence[: max(1, remaining - 1)].rstrip("、, ") + "…"
                selected.append(cut)
                used += len(cut)
            break
        selected.append(sentence)
        used += len(sentence)

    summary = "".join(selected).strip()
    if not summary:
        summary = text[: SPOKEN_CONTENT_MAX_CHARS - 1].rstrip() + "…"

    consumed_all = summary == text
    if not consumed_all:
        summary = f"{summary} {DETAIL_OFFER}"
    return summary


class SpeechGenerationError(RuntimeError):
    """A safe, non-secret-bearing Speech API failure."""


@dataclass(frozen=True)
class SpeechConfig:
    api_key: Optional[str]
    model: str
    voice: str
    instructions: str
    base_url: str
    timeout: float

    @classmethod
    def from_env(cls) -> "SpeechConfig":
        # A generic key is usable only when it is explicitly an OpenAI key.
        # This avoids sending an Anthropic/Gemini credential to OpenAI when
        # the main reasoning provider differs from the speech provider.
        provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
        generic_key = (
            (os.getenv("LLM_API_KEY") or "").strip() if provider == "openai" else ""
        )
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip() or generic_key or None
        try:
            timeout = float(os.getenv("OPENAI_TTS_TIMEOUT", "30"))
        except ValueError:
            timeout = 30.0
        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip()
            or "gpt-4o-mini-tts",
            voice=os.getenv("OPENAI_TTS_VOICE", "marin").strip() or "marin",
            instructions=(
                os.getenv("OPENAI_TTS_INSTRUCTIONS") or DEFAULT_SPEECH_INSTRUCTIONS
            ).strip(),
            base_url=os.getenv(
                "OPENAI_TTS_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            timeout=max(1.0, timeout),
        )


@dataclass(frozen=True)
class SpokenProjection:
    text: str
    has_more: bool


def project_spoken_answer(answer: str) -> Optional[SpokenProjection]:
    """Build the speech text and expose whether a reply is being requested."""
    summary = spoken_summary(answer)
    if summary is None:
        return None
    return SpokenProjection(text=summary, has_more=summary.endswith(DETAIL_OFFER))


def stream_speech(text: str, config: Optional[SpeechConfig] = None) -> Iterator[bytes]:
    """Stream an MP3 response from OpenAI without exposing its API key."""
    effective = config or SpeechConfig.from_env()
    if not effective.api_key:
        raise SpeechGenerationError(
            "OpenAI speech is not configured. Set OPENAI_API_KEY or use "
            "LLM_PROVIDER=openai with LLM_API_KEY."
        )
    payload = {
        "model": effective.model,
        "voice": effective.voice,
        "input": text,
        "instructions": effective.instructions,
        "response_format": "mp3",
    }
    request = urllib.request.Request(
        f"{effective.base_url}/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {effective.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=effective.timeout)
    except urllib.error.HTTPError as exc:
        raise SpeechGenerationError(
            f"OpenAI speech request failed with HTTP {exc.code}."
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SpeechGenerationError("OpenAI speech request failed.") from exc

    try:
        while True:
            chunk = response.read(16 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        response.close()
