"""
audio_service.py
================
Local speech-to-text for meeting recordings, using **faster-whisper**.

What it does
------------
* Validates the uploaded audio (extension, empty file, duration).
* Transcribes it 100% locally (CPU, int8) - no cloud service is involved.
* Returns {"text", "language", "seconds"} for the normal analyze flow.

The dependency is OPTIONAL, so the rest of the app keeps working without it:

    pip install -r requirements-audio.txt     # once

Without the package every function raises AudioValidationError with that
exact instruction, and the UI shows it - nothing ever crashes.

The Whisper model is loaded once and cached (module-level `_MODEL`), because
loading takes several seconds and transcribing a minute of audio only takes
a few seconds more.
"""

from __future__ import annotations

import os
import tempfile

# Audio types the app accepts (lowercase, with dot)
ALLOWED_EXTENSIONS = (".mp3", ".wav", ".m4a", ".webm", ".ogg", ".flac", ".mp4")

# Model sizes: tiny (~75 MB, fastest) ... medium (~1.5 GB, best quality).
# "base" is the default: decent quality on a normal CPU.
MODEL_SIZES = ("tiny", "base", "small", "medium")
DEFAULT_MODEL_SIZE = "base"


def default_model_size() -> str:
    """The WHISPER_MODEL value from .env (read at call time, so the .env
    file - loaded after this module is imported - is taken into account)."""
    value = (os.getenv("WHISPER_MODEL") or DEFAULT_MODEL_SIZE).strip()
    return value if value in MODEL_SIZES else DEFAULT_MODEL_SIZE

MAX_AUDIO_SECONDS = 2 * 60 * 60     # refuse recordings longer than 2 hours

# The loaded model is reused between requests (see _get_model)
_MODEL = None
_MODEL_SIZE_LOADED = ""


class AudioValidationError(Exception):
    """An audio problem that is safe to show to the user."""


def is_available() -> bool:
    """True when the optional faster-whisper package is installed."""
    try:
        import faster_whisper          # noqa: F401
        return True
    except Exception:
        return False


def validate_audio(filename: str) -> str:
    """Check the file name. Returns the lowercase extension (e.g. '.mp3')."""
    ext = os.path.splitext(filename or "")[1].lower()
    if not ext:
        raise AudioValidationError("Please choose an audio file "
                                   "(mp3, wav, m4a, webm, ogg or flac).")
    if ext not in ALLOWED_EXTENSIONS:
        raise AudioValidationError(
            f"Audio files only: {', '.join(e.lstrip('.') for e in ALLOWED_EXTENSIONS)}.")
    return ext


def _get_model(size: str):
    """Load the Whisper model once, then reuse it (much faster)."""
    global _MODEL, _MODEL_SIZE_LOADED
    if _MODEL is None or _MODEL_SIZE_LOADED != size:
        from faster_whisper import WhisperModel
        # device="cpu" + int8 works on every machine, no GPU needed
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8")
        _MODEL_SIZE_LOADED = size
    return _MODEL


def transcribe(data: bytes, filename: str, model_size: str | None = None) -> dict:
    """
    Turn an audio file into text.

    Returns {"text": str, "language": str, "seconds": float}.
    Raises AudioValidationError with a friendly, safe message.
    """
    ext = validate_audio(filename)
    if not data:
        raise AudioValidationError("The audio file is empty.")

    size = (model_size or default_model_size()).strip()
    if size not in MODEL_SIZES:
        size = default_model_size()

    if not is_available():
        raise AudioValidationError(
            "Audio transcription needs the optional 'faster-whisper' package. "
            "Install it once with:  pip install -r requirements-audio.txt "
            "then restart the app.")

    # Write the upload to a real file - Whisper reads from disk
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            temp_path = tmp.name

        # 1) decode-check first (fails fast on broken/non-audio files)
        #    and read the real duration
        import av                                   # faster-whisper dependency
        try:
            with av.open(temp_path) as container:
                duration = float(container.duration or 0) / 1_000_000
        except Exception:
            raise AudioValidationError(
                "This file could not be read as audio. Please upload a real "
                "recording (mp3, wav, m4a, webm, ogg or flac).") from None

        if duration and duration > MAX_AUDIO_SECONDS:
            hours = MAX_AUDIO_SECONDS // 3600
            raise AudioValidationError(
                f"This recording is longer than {hours} hour(s). "
                "Please split it into shorter parts.")

        # 2) transcribe (model is cached between requests)
        model = _get_model(size)
        segments, info = model.transcribe(temp_path, beam_size=1, vad_filter=True)
        texts = [seg.text.strip() for seg in segments]
        text = "\n".join(t for t in texts if t)

        return {
            "text": text,
            "language": str(info.language or ""),
            "seconds": round(duration or float(info.duration or 0), 1),
        }

    except AudioValidationError:
        raise
    except Exception as exc:                       # never leak a stack trace
        raise AudioValidationError(
            f"The audio could not be transcribed ({type(exc).__name__}). "
            "Please try another file (wav/mp3 work best).") from None
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
