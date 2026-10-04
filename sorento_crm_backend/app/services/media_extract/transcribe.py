"""Voice transcription (PLAN section 5).

No transcription code existed anywhere in this repo - zero hits for
`whisper|transcrib|speech_to_text` across `app/` - so this is the one place the
"reuse what transfers" instruction had nothing to transfer.

The language strategy is built **from settings at call time**, never baked in:

* `pinned` sends `language: <media_language_pinned>`. This is the default, and
  with `whisper-1` + `en` it reproduces today's spine behaviour exactly. The pin
  stays until the captain says otherwise; what ships is the ability to change it
  without a deploy.
* `hints` sends a `languages` list, for a model that accepts one.
* `auto` sends neither.

A `languages` array on the response is recorded and returned, so switching to a
model that reports what it detected is a settings change rather than a code
change. An **empty** array is a valid "the model could not make a reliable
prediction" signal and is propagated as such - not as an error, and not as
silence. `None` means the model said nothing about the language at all; `[]`
means it said it was unsure. They are different answers and the distinction is
kept.

Two operational traps, recorded so they are not rediscovered:

* the upload filename must carry a usable extension, because the API infers the
  audio format from it. `filename_for` derives one from the mime type, falling
  back to the URL and finally to `.ogg`.
* WhatsApp voice notes are Opus in an Ogg container, and acceptance varies by
  model. A provider rejection is surfaced with the **provider's own message**
  rather than a generic error, so a failed callback says why.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# OpenAI-compatible transcription endpoint. A module constant rather than a
# setting: the model is operator-configurable (UAC S5-04), the API shape is not.
TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"

DEFAULT_TIMEOUT_SECONDS = 60.0

# mime -> extension. The API infers the format from the filename, so an
# extensionless upload is rejected for a reason that has nothing to do with the
# audio.
_MIME_EXTENSIONS: dict[str, str] = {
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/oga": ".oga",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".mp4",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/amr": ".amr",
    "audio/3gpp": ".3gp",
}
_KNOWN_EXTENSIONS = tuple(sorted(set(_MIME_EXTENSIONS.values())))

# WhatsApp voice notes are Ogg/Opus, so that is the honest guess when nothing
# else identifies the clip.
_FALLBACK_EXTENSION = ".ogg"


class TranscriptionError(RuntimeError):
    """The provider refused or failed. Carries the provider's own message."""


@dataclass
class TranscriptionResult:
    """What the model heard, and what it says about the language it heard it in.

    `languages_detected` is tri-state on purpose: `None` (the model reported
    nothing), `[]` (the model reported that it is unsure), or a list.
    """

    text: str
    languages_detected: Optional[list[str]]
    model: str
    raw: dict[str, Any]


def filename_for(mime_type: Optional[str], media_url: Optional[str] = None) -> str:
    """An upload filename with an extension the API can infer a format from."""
    mime = (mime_type or "").split(";")[0].strip().lower()
    extension = _MIME_EXTENSIONS.get(mime)
    if extension is None and media_url:
        path = urlparse(media_url).path.lower()
        for candidate in _KNOWN_EXTENSIONS:
            if path.endswith(candidate):
                extension = candidate
                break
    return f"voice-note{extension or _FALLBACK_EXTENSION}"


def build_request_data(strategy: Optional[dict], *, model: str) -> dict[str, Any]:
    """The multipart form fields, built from the language strategy at call time.

    The strategy dict is whatever `MediaSettings.language_strategy()` resolved -
    the resolver already falls back to pinned/`en`, so an unwritten settings row
    reproduces today's behaviour without a special case here.
    """
    data: dict[str, Any] = {"model": model, "response_format": "json"}
    mode = (strategy or {}).get("mode") or "pinned"
    if mode == "hints":
        languages = [
            str(language).strip()
            for language in ((strategy or {}).get("languages") or [])
            if str(language).strip()
        ]
        if languages:
            # A list, for a model that accepts one. httpx repeats the field per
            # value, which is the multipart convention for an array.
            data["languages"] = languages
        return data
    if mode == "auto":
        return data
    language = str((strategy or {}).get("language") or "").strip()
    if language:
        data["language"] = language
    else:
        # An operator who blanked the pinned language asked for no pin at all.
        # Sending an empty `language` is a 400; sending none is auto-detect.
        logger.info("media transcribe: pinned mode with no language, sending auto")
    return data


def parse_languages(payload: dict[str, Any]) -> Optional[list[str]]:
    """Detected languages, tri-state (UAC S5-03).

    A present-but-empty `languages` array is the model saying it is unsure. That
    is an answer, so it is propagated as `[]` rather than collapsed to `None`.
    """
    if "languages" in payload:
        raw = payload.get("languages")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if raw is None:
            return []
    single = payload.get("language")
    if isinstance(single, str) and single.strip():
        return [single.strip()]
    return None


def transcribe(
    data: bytes,
    *,
    model: str,
    strategy: Optional[dict],
    api_key: str,
    mime_type: Optional[str] = None,
    media_url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> TranscriptionResult:
    """Post the clip and return what was heard."""
    if not data:
        raise TranscriptionError("The voice note downloaded as zero bytes.")
    if not api_key:
        raise TranscriptionError(
            "No transcription API key is configured. Set it in System > AI "
            "Assistant Settings."
        )
    payload = _post_transcription(
        data,
        filename=filename_for(mime_type, media_url),
        mime_type=(mime_type or "application/octet-stream").split(";")[0].strip(),
        fields=build_request_data(strategy, model=model),
        api_key=api_key,
        timeout=timeout,
    )
    text = str(payload.get("text") or "").strip()
    return TranscriptionResult(
        text=text,
        languages_detected=parse_languages(payload),
        model=model,
        raw=payload,
    )


def _post_transcription(
    data: bytes,
    *,
    filename: str,
    mime_type: str,
    fields: dict[str, Any],
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    """The single HTTP seam, so tests can record a response without a network."""
    import httpx

    from app.services import llm_cassette

    llm_cassette.refuse_uncovered("transcription")

    try:
        response = httpx.post(
            TRANSCRIPTION_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, data, mime_type)},
            data=fields,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:  # network-level failure
        raise TranscriptionError(f"Transcription request failed: {exc}") from exc

    if response.status_code >= 400:
        # The provider's own words. A rejected container ("Opus in Ogg is not
        # supported") must reach the failed callback, not be flattened into
        # "transcription failed".
        raise TranscriptionError(
            f"Transcription failed ({response.status_code}): "
            f"{_provider_message(response)}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TranscriptionError(
            "The transcription service returned a non-JSON response."
        ) from exc
    if not isinstance(payload, dict):
        raise TranscriptionError(
            "The transcription service returned an unexpected response shape."
        )
    return payload


def _provider_message(response: Any) -> str:
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - fall back to the raw text
        return (getattr(response, "text", "") or "").strip()[:500]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
    return str(body)[:500]
