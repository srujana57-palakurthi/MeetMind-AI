"""
ai_service.py
=============
Local Ollama integration for the Meeting Analyzer.

What this module does
---------------------
* Loads the root ``.env`` file with python-dotenv and reads ``OLLAMA_URL``
  and ``OLLAMA_MODEL`` - no API key exists and none is ever needed.
* Sends the meeting transcript to Ollama's ``/api/chat`` endpoint with the
  ``requests`` library (streaming off, JSON mode on, retried once without
  JSON mode in case the server rejects it).
* Asks the model for ONE JSON object and validates it with
  ``validate_ai_response()`` BEFORE anything is written to SQLite.
* Turns connection problems, timeouts, unknown models and broken answers
  into short, friendly messages (see ``_friendly_error``) - a stack trace
  never reaches the browser.

Returned structure (exactly what the app stores):

    {
      "title":            "short meeting title",
      "meeting_summary":  "2-4 sentences",
      "key_points":       ["bullet", ...],
      "decisions":        [{"decision": "...", "status": "confirmed|suggested"}, ...],
      "action_items":     [{"task": "...", "owner": "Name|null",
                            "deadline": "exact wording|null",
                            "status": "Not started|In progress|Done"}, ...],
      "follow_up_email":  {"subject": "...", "body": "..."}
    }

Honesty rules enforced here (and in the prompt):
    * missing owner / deadline  -> null (never guessed, displayed "Not specified")
    * decisions that were only discussed -> "suggested", never "confirmed"
    * deadlines keep the wording used in the notes
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# configuration - the .env file in the project root (never hardcoded)
# ---------------------------------------------------------------------------
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(ROOT_DIR, ".env")

load_dotenv(ENV_FILE)                     # reads .env into os.environ

DEFAULT_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:3b"

CONNECT_TIMEOUT = 5        # seconds - fail fast when Ollama is not running
READ_TIMEOUT = 240         # seconds - a small model can be slow on long notes
PING_TIMEOUT = (CONNECT_TIMEOUT, 30)
MAX_TRANSCRIPT_CHARS = 60_000

# Question answering ("ask about this meeting" / "ask across meetings")
ASK_MAX_QUESTION = 500         # characters a user may type
ASK_CONTEXT_CHARS = 24_000     # keeps the answer inside the model's context
CROSS_MAX_MEETINGS = 5         # meetings sent for one cross-meeting question
CROSS_SNIPPET_CHARS = 3_000    # notes excerpt per meeting

# Must match database.STATUSES (checked by selftest.py).
TASK_STATUSES = ("Not started", "In progress", "Done")

# Words that mean "no value" - never saved as a real owner/deadline.
EMPTY_MARKERS = {
    "", "-", "--", "n/a", "na", "none", "null", "unknown",
    "not specified", "not mentioned", "not assigned", "unassigned", "tbd", "?",
}


class AIServiceError(Exception):
    """An AI problem that is safe to show to the user."""


def get_url() -> str:
    """OLLAMA_URL from .env, e.g. http://localhost:11434/api/chat"""
    return (os.getenv("OLLAMA_URL") or DEFAULT_URL).strip().rstrip("/")


def get_model() -> str:
    """OLLAMA_MODEL from .env - e.g. qwen2.5:3b"""
    return (os.getenv("OLLAMA_MODEL") or "").strip()


def is_configured() -> bool:
    """True when a local model is configured (False -> offline mode)."""
    return bool(get_model())


def _server_of(url: str) -> str:
    """http://localhost:11434/api/chat -> http://localhost:11434"""
    parts = urlparse(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return url


def _resolve_model(model: str | None) -> str:
    resolved = (model if model is not None else get_model()).strip()
    if not resolved:
        raise AIServiceError(
            "No Ollama model is configured. Set OLLAMA_MODEL=qwen2.5:3b in "
            "the .env file (or on the Settings page) and restart the app. "
            "Leave it empty to use the built-in offline analyzer.")
    return resolved


def _resolve_url(url: str | None) -> str:
    resolved = (url if url is not None else get_url()).strip().rstrip("/")
    if not resolved:
        raise AIServiceError(
            "No Ollama address is configured. Set OLLAMA_URL="
            "http://localhost:11434/api/chat in the .env file.")
    return resolved


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------
INSTRUCTIONS = """You are a precise meeting-notes analyzer for a web app.
Answer with ONE valid JSON object and with nothing else
(no markdown, no code fences, no commentary).

STRICT RULES
1. NEVER invent names, dates, deadlines, decisions or facts that are not in
   the transcript. When something is missing use null.
2. "owner" = a person actually named in the transcript, otherwise null.
3. "deadline" = the exact wording from the transcript (for example
   "next Monday", "2026-10-20", "end of month"), otherwise null.
   Do NOT calculate or convert dates yourself.
   IMPORTANT: the deadline goes in "deadline" and must NOT stay inside
   "task". Example: for the note "Alice will send the release notes by
   next Monday" return:
     {"task": "Send the release notes", "owner": "Alice",
      "deadline": "next Monday", "status": "Not started"}
4. decisions[].status is:
     "confirmed" - the meeting clearly decided / approved / agreed this
     "suggested" - only proposed or discussed, NOT decided
   Never turn a suggestion into a decision. If the transcript does not say
   that something was decided, use "suggested".
5. action_items only for tasks that are literally stated in the transcript.
   "status" must be one of: "Not started", "In progress", "Done"
   (use "Not started" unless the transcript says otherwise).
6. follow_up_email must be written ONLY from the transcript: a professional
   subject, short paragraphs, a bullet list of the action items with their
   owners and deadlines as given (write "no deadline given" when missing),
   ending with the line "[Your name]". Never invent recipients or dates.
7. "title" = a short descriptive title taken from the content.

RETURN EXACTLY THIS JSON SHAPE:
{
  "title": "short meeting title",
  "meeting_summary": "2-4 sentence summary",
  "key_points": ["bullet", "bullet"],
  "decisions": [{"decision": "text", "status": "confirmed" | "suggested"}],
  "action_items": [
    {"task": "text", "owner": "Name or null",
     "deadline": "exact wording or null",
     "status": "Not started" | "In progress" | "Done"}
  ],
  "follow_up_email": {"subject": "subject line", "body": "email body"}
}"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _safe_detail(value: Any, limit: int = 300) -> str:
    """Collapse an error detail to one short, printable line."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text[:limit]


def _unreachable(url: str) -> AIServiceError:
    server = _server_of(url)
    return AIServiceError(
        f"Could not reach Ollama at {server}. Make sure Ollama is running: "
        "run 'ollama serve' in a terminal (or start the Ollama app), then "
        "try again. The address comes from OLLAMA_URL in your .env file.")


def _clean_text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _clean_optional(value: Any, limit: int = 200) -> str | None:
    """A real value, or None - never a placeholder such as 'N/A'."""
    text = _clean_text(value, limit)
    return None if text.lower() in EMPTY_MARKERS else (text or None)


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [line for line in value.splitlines() if line.strip()]
    if value is None:
        return []
    return [value]


def parse_json(raw: Any) -> dict:
    """
    Pull a JSON object out of a model answer.

    Survives ```json fences, extra prose and truncated answers by never
    raising anything but AIServiceError.
    """
    if raw is None or not str(raw).strip():
        raise AIServiceError("The AI returned an empty response. Please try again.")

    text = str(raw).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()

    candidates = [text]
    if "{" in text and "}" in text[text.find("{"):]:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data

    raise AIServiceError(
        "The AI returned an unreadable answer (not valid JSON). Please try again.")


# ---------------------------------------------------------------------------
# validation (called BEFORE anything is saved to SQLite)
# ---------------------------------------------------------------------------
def validate_ai_response(data: Any) -> dict:
    """
    Check the model's JSON and clean it.

    Returns the documented structure, or raises AIServiceError when the
    answer is not usable (wrong type, not a JSON object, ...).
    """
    if not isinstance(data, dict):
        raise AIServiceError("The AI answer was not a JSON object.")

    # meeting_summary - required, must be text
    summary = _clean_text(data.get("meeting_summary"), 4000)
    if not summary:
        raise AIServiceError("The AI answer is missing 'meeting_summary'. "
                             "Please try again.")
    if len(summary) < 20:
        raise AIServiceError("The AI summary is too short to be useful. "
                             "Please try again.")

    # title - optional, the app derives one from the transcript if missing
    title = _clean_text(data.get("title"), 200)

    # key_points - list of short strings
    key_points: list[str] = []
    for point in _as_list(data.get("key_points")):
        point = _clean_text(point, 400)
        if point and point not in key_points:
            key_points.append(point)

    # decisions - confirmed vs suggested (never guessed -> "not_specified")
    decisions: list[dict] = []
    raw_decisions = data.get("decisions")
    if raw_decisions is not None and not isinstance(raw_decisions, list) \
            and not isinstance(raw_decisions, str):
        raise AIServiceError("'decisions' must be a list. Please try again.")
    for raw in _as_list(raw_decisions):
        if isinstance(raw, dict):
            text = _clean_text(raw.get("decision") or raw.get("text"), 600)
            status = _clean_text(raw.get("status"), 40).lower()
        else:
            text = _clean_text(raw, 600)
            status = ""
        if not text:
            continue
        if status.startswith(("confirm", "agree", "approv")):
            status = "confirmed"
        elif status.startswith(("suggest", "propos", "discuss", "tentative")):
            status = "suggested"
        else:
            status = "not_specified"          # we do not know -> do not guess
        decisions.append({"decision": text, "status": status})

    # action_items - task is required; owner/deadline may be null
    action_items: list[dict] = []
    raw_items = data.get("action_items")
    if raw_items is not None and not isinstance(raw_items, list) \
            and not isinstance(raw_items, str):
        raise AIServiceError("'action_items' must be a list. Please try again.")
    for raw in _as_list(raw_items):
        if isinstance(raw, dict):
            task = _clean_text(raw.get("task") or raw.get("action"), 500)
            owner = _clean_optional(raw.get("owner"))
            deadline = _clean_optional(raw.get("deadline"))
            status = _clean_text(raw.get("status"), 30)
        else:
            task = _clean_text(raw, 500)
            owner = deadline = None
            status = ""
        if not task:
            continue                         # a task without text is dropped
        if status not in TASK_STATUSES:
            status = "Not started"            # workflow state, not a fact
        action_items.append({"task": task, "owner": owner,
                             "deadline": deadline, "status": status})

    # follow_up_email - optional; empty values are filled in by ai_processor
    email = data.get("follow_up_email") or data.get("email") or {}
    if isinstance(email, str):
        email = {"subject": "", "body": email}
    if not isinstance(email, dict):
        email = {}
    follow_up_email = {"subject": _clean_text(email.get("subject"), 300),
                       "body": str(email.get("body") or "").strip()[:20_000]}

    return {
        "title": title,
        "meeting_summary": summary,
        "key_points": key_points,
        "decisions": decisions,
        "action_items": action_items,
        "follow_up_email": follow_up_email,
    }


# ---------------------------------------------------------------------------
# the HTTP call to Ollama (/api/chat)
# ---------------------------------------------------------------------------
def _post(url: str, payload: dict, timeout) -> requests.Response:
    """POST one request. Connection and timeout errors become AI messages."""
    try:
        return requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.ConnectTimeout:
        raise _unreachable(url) from None
    except requests.exceptions.ConnectionError:
        raise _unreachable(url) from None
    except requests.exceptions.ReadTimeout:
        seconds = timeout[1] if isinstance(timeout, (tuple, list)) else timeout
        raise AIServiceError(
            f"Ollama took too long to answer (more than {seconds}s). "
            "The model may still be loading - wait a moment and try again, "
            "or use a smaller model.") from None
    except requests.exceptions.RequestException as exc:
        raise AIServiceError(
            f"Could not talk to Ollama at {_server_of(url)} "
            f"({_safe_detail(exc)})") from None


def _http_error(response: requests.Response, url: str, model: str) -> str:
    """Friendly text for a non-200 answer from Ollama."""
    status = response.status_code
    detail = ""
    try:
        data = response.json()
        if isinstance(data, dict):
            detail = _safe_detail(data.get("error") or "")
    except ValueError:
        detail = _safe_detail(response.text)

    if status == 404:
        low = detail.lower()
        if "not found" in low or "pull" in low or "model" in low:
            return (f'The model "{model}" is not downloaded in Ollama. '
                    f"Download it once with:  ollama pull {model}")
        return (f"Ollama returned 404 for {url}. Check OLLAMA_URL in your "
                ".env file - it should look like "
                "http://localhost:11434/api/chat")
    if status == 400:
        return f"Ollama rejected the request (400): {detail or 'bad request'}"
    if status >= 500:
        return f"Ollama had an internal error ({status}): {detail or 'no details'}"
    return f"Ollama returned an unexpected HTTP {status}: {detail}"


def _response_json(response: requests.Response, url: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise AIServiceError(
            "Ollama answered with something that is not valid JSON. Check "
            f"that OLLAMA_URL points to an Ollama /api/chat endpoint ({url})."
        ) from None


def _extract_content(response: requests.Response, url: str, model: str) -> str:
    if response.status_code != 200:
        raise AIServiceError(_http_error(response, url, model))

    data = _response_json(response, url)
    content = ""
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        elif isinstance(data.get("response"), str):     # /api/generate style
            content = data["response"]
    if not content.strip():
        raise AIServiceError(
            f'The model "{model}" returned an empty answer. Please try again.')
    return content


def _chat(url: str, model: str, messages: list[dict], options: dict,
          timeout, want_json: bool = True) -> str:
    """One Ollama /api/chat call. Returns the assistant's text."""
    attempts = (True, False) if want_json else (False,)
    response = None

    for use_json in attempts:
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,                 # we want one complete answer
            "messages": messages,
            "options": options,
        }
        if use_json:
            payload["format"] = "json"       # Ollama JSON grammar mode
        response = _post(url, payload, timeout)
        if response.status_code == 200:
            break
        # some builds reject "format" -> retry once without it
        if not (use_json and response.status_code == 400):
            break

    return _extract_content(response, url, model)


def _tag_models(response: requests.Response) -> list[str]:
    """Model names from GET /api/tags."""
    try:
        data = response.json()
    except ValueError:
        return []
    names = []
    for entry in (data.get("models") or []) if isinstance(data, dict) else []:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return names


def _has_model(available: list[str], wanted: str) -> bool:
    wanted = wanted.strip()
    for name in available:
        if name == wanted or name.split(":")[0] == wanted.split(":")[0]:
            return True
    return False


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def analyze_meeting(transcript: str, meeting_date: str | None = None,
                    model: str | None = None, url: str | None = None,
                    timeout=None) -> dict:
    """
    Send a meeting transcript to Ollama and return a VALIDATED structure:

        title, meeting_summary, key_points, decisions, action_items,
        follow_up_email

    Raises AIServiceError with a message that is safe to display.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        raise AIServiceError("There is no meeting content to analyze.")
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        raise AIServiceError(
            f"The meeting text is too long ({len(transcript):,} characters). "
            f"Please shorten it to under {MAX_TRANSCRIPT_CHARS:,} characters.")

    resolved_model = _resolve_model(model)
    resolved_url = _resolve_url(url)
    request_timeout = timeout or (CONNECT_TIMEOUT, READ_TIMEOUT)

    user_input = (
        f"Meeting date: {meeting_date or 'unknown (not provided)'}\n\n"
        f"Meeting transcript:\n{transcript}"
    )
    content = _chat(
        resolved_url,
        resolved_model,
        [{"role": "system", "content": INSTRUCTIONS},
         {"role": "user", "content": user_input}],
        {"temperature": 0.2},
        request_timeout,
    )

    payload = parse_json(content)
    return validate_ai_response(payload)          # validate BEFORE saving


def test_connection(model: str | None = None, url: str | None = None,
                    timeout=None) -> tuple[bool, str]:
    """Small ping used by the Settings page. Returns (ok, safe message)."""
    try:
        resolved_model = _resolve_model(model)
        resolved_url = _resolve_url(url)
        server = _server_of(resolved_url)
        ping = timeout or PING_TIMEOUT

        # 1) is the Ollama server running at all?
        try:
            tags = requests.get(f"{server}/api/tags", timeout=ping)
        except requests.exceptions.RequestException:
            return False, str(_unreachable(resolved_url))

        # 2) is the model downloaded?
        if tags.status_code == 200:
            available = _tag_models(tags)
            if available and not _has_model(available, resolved_model):
                return False, (
                    f'The model "{resolved_model}" is not downloaded. '
                    f"Run this once in a terminal:  ollama pull {resolved_model}")

        # 3) does the model actually answer?
        answer = _chat(
            resolved_url, resolved_model,
            [{"role": "user", "content": "Reply with the single word OK."}],
            {"temperature": 0, "num_predict": 8}, ping, want_json=False)
        reply = _safe_detail(answer, 40)
        return True, (f"Connected to Ollama at {server} - model "
                      f'"{resolved_model}" answered "{reply}".')
    except AIServiceError as exc:
        return False, str(exc)
    except Exception as exc:                       # never leak a stack trace
        return False, f"The connection test failed: {_safe_detail(exc)}"


# ---------------------------------------------------------------------------
# question answering (plain text answers, no JSON)
# ---------------------------------------------------------------------------
ASK_INSTRUCTIONS = """You are a helpful assistant answering questions about ONE meeting.
Answer ONLY with the information contained in the meeting notes provided.
RULES
- If the answer is not in the notes, say exactly: "That is not mentioned in these notes."
- Never invent people, dates, tasks or decisions.
- Be short: at most 4 sentences, or a short bullet list.
- Repeat exact names and dates from the notes."""

CROSS_INSTRUCTIONS = """You are a helpful assistant answering a question using SEVERAL meeting notes.
Each block starts with a line "=== Meeting: <title> (<date>) ===".
RULES
- Answer only from the provided meeting notes.
- Name the meeting(s) you used, like: In "Weekly Sync" (2026-10-09): ...
- If the notes do not contain the answer, say exactly: "I could not find that in your meetings."
- Never invent people, dates, tasks or decisions. Be short."""


def _clean_question(question: str) -> str:
    text = (question or "").strip()
    if not text:
        raise AIServiceError("Please type a question first.")
    if len(text) > ASK_MAX_QUESTION:
        raise AIServiceError(
            f"The question is too long ({len(text):,} characters). "
            f"Please keep it under {ASK_MAX_QUESTION} characters.")
    return text


def _ask(instructions: str, context: str, question: str,
         model: str | None, url: str | None, timeout) -> str:
    """One plain-text Q&A call to Ollama (no JSON mode)."""
    resolved_model = _resolve_model(model)
    resolved_url = _resolve_url(url)
    request_timeout = timeout or (CONNECT_TIMEOUT, READ_TIMEOUT)

    user_input = (f"Question: {_clean_question(question)}\n\n"
                  f"Meeting notes:\n{(context or '')[:ASK_CONTEXT_CHARS]}")
    answer = _chat(
        resolved_url,
        resolved_model,
        [{"role": "system", "content": instructions},
         {"role": "user", "content": user_input}],
        {"temperature": 0.1},
        request_timeout,
        want_json=False,               # answers are plain text, not JSON
    )
    return answer.strip()


def ask_meeting(context: str, question: str, model: str | None = None,
                url: str | None = None, timeout=None) -> str:
    """Answer a question about ONE meeting (context built by ai_processor)."""
    return _ask(ASK_INSTRUCTIONS, context, question, model, url, timeout)


def ask_across(context: str, question: str, model: str | None = None,
               url: str | None = None, timeout=None) -> str:
    """Answer a question using several meetings (context has citations)."""
    return _ask(CROSS_INSTRUCTIONS, context, question, model, url, timeout)
