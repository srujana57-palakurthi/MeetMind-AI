"""
ai_processor.py
===============
Turns raw meeting notes into structured data:

    title, summary, key_points, decisions, action_items (task / owner /
    deadline), follow_up_email  ->  all as clean Python objects (JSON).

Design rules required by the project:

  * The AI must return ONLY JSON.  The response is parsed defensively so a
    broken / chatty answer can never crash the app (see parse_json).
  * Nothing is invented.  Missing owner  -> None (displayed "Not specified").
    Missing deadline -> None.  A decision without a clear status is marked
    "not_specified" instead of being guessed.
  * Deadlines keep the exact wording from the notes.  We only classify them
    (absolute vs relative) and resolve relative dates when - and only when -
    the meeting date is known.
  * The AI runs on a LOCAL Ollama server (see ai_service.py): the request
    is sent with plain HTTP to OLLAMA_URL and no API key exists anywhere.
    If no model is configured (OLLAMA_MODEL empty) a fully offline
    heuristic analyzer runs so the app still works without Ollama either.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import ai_service
import database as db

# ---------------------------------------------------------------------------
# errors & constants
# ---------------------------------------------------------------------------
class AIError(Exception):
    """A problem with the AI service that is safe to show to the user."""


EMPTY_MARKERS = {
    "", "-", "--", "n/a", "na", "none", "null", "unknown",
    "not specified", "not mentioned", "not assigned", "unassigned",
    "tbd", "?",
}

MAX_API_CHARS = 60_000        # refuse longer input before calling the model


# ---------------------------------------------------------------------------
# configuration (Ollama comes from .env, no API key exists)
# ---------------------------------------------------------------------------
def get_ai_config() -> tuple[str, str]:
    """(ollama_url, model) - Settings page overrides the .env defaults."""
    url = (db.get_setting("ollama_url") or ai_service.get_url()).strip()
    model = (db.get_setting("ollama_model") or ai_service.get_model()).strip()
    return url.rstrip("/"), model


def is_configured() -> bool:
    """True when a local Ollama model is set (False -> offline mode)."""
    return bool(get_ai_config()[1])


# ---------------------------------------------------------------------------
# deadline classification / resolution
# ---------------------------------------------------------------------------
_MONTH_NAMES = ("january february march april may june july august september "
                "october november december").split()
_MONTH_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
               "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_LOOKUP = {name: index + 1 for index, name in enumerate(_MONTH_NAMES)}
_MONTH_LOOKUP.update(_MONTH_ABBR)
# longest alternative first so "october" is preferred over "oct"
_MONTH_ALT = "|".join(sorted(_MONTH_LOOKUP, key=len, reverse=True))
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}
_RELATIVE_WORDS = re.compile(
    r"\b(today|tomorrow|asap|end of (the )?week|eow|end of (the )?month|"
    r"next (week|month|year|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday)|this (week|month)|in \d+ (day|days|week|weeks)|"
    r"coming (week|weeks)|following (week|weeks)|soon|after the holidays)\b",
    re.I,
)


def _fmt(date_obj) -> str:
    return date_obj.strftime("%a, %d %b %Y")


def _parse_meeting_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def classify_deadline(text: str, meeting_date=None) -> tuple[str | None, str | None]:
    """
    Returns (deadline_type, resolved_date_string).

    deadline_type is None when there is no deadline at all.
    resolved_date_string is only produced for RELATIVE dates and only when
    the meeting date is known - otherwise it stays None (never guessed).
    """
    if text is None:
        return None, None
    raw = str(text).strip()
    if raw.lower() in EMPTY_MARKERS:
        return None, None

    meeting_dt = _parse_meeting_date(meeting_date)

    # --- explicit calendar dates -> absolute -----------------------------
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", raw)
    if iso:
        return "absolute", iso.group(1)

    month_first = re.search(
        r"\b(?P<mon>" + _MONTH_ALT + r")\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
        r"(?:,?\s+(?P<year>\d{4}))?\b", raw, re.I)
    if month_first:
        month_number = _MONTH_LOOKUP[month_first.group("mon").lower()]
        if month_first.group("year"):
            try:
                dt = datetime(int(month_first.group("year")), month_number,
                              int(month_first.group("day")))
                return "absolute", dt.strftime("%Y-%m-%d")
            except ValueError:
                return "absolute", None
        return "absolute", None                      # "Oct 20", year unknown

    day_first = re.search(
        r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>" + _MONTH_ALT + r")"
        r"(?:,?\s+(?P<year>\d{4}))?\b", raw, re.I)
    if day_first:
        month_number = _MONTH_LOOKUP[day_first.group("mon").lower()]
        if day_first.group("year"):
            try:
                dt = datetime(int(day_first.group("year")), month_number,
                              int(day_first.group("day")))
                return "absolute", dt.strftime("%Y-%m-%d")
            except ValueError:
                return "absolute", None
        return "absolute", None

    slash = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", raw)
    if slash:
        return "absolute", None                      # date exists, format ambiguous

    # --- relative wording -> relative (+ resolution only with meeting date)
    rel = _RELATIVE_WORDS.search(raw)
    weekday = re.search(r"\b(" + "|".join(_WEEKDAYS) + r")\b", raw, re.I)

    if rel or weekday:
        if meeting_dt is None:
            return "relative", None                  # cannot resolve -> no guess

        low = raw.lower()
        resolved = None

        if re.search(r"\btomorrow\b", low):
            resolved = meeting_dt + timedelta(days=1)
        elif re.search(r"\btoday\b", low):
            resolved = meeting_dt
        elif re.search(r"\bin \d+ (days?|weeks?)\b", low):
            num = re.search(r"\bin (\d+) (days?|weeks?)\b", low)
            amount = int(num.group(1))
            resolved = meeting_dt + timedelta(
                days=amount * 7 if num.group(2).startswith("week") else amount)
        elif re.search(r"\b(end of\b|eow\b)", low):
            # "end of week / end of next week / end of month"
            base = meeting_dt + timedelta(days=7) if re.search(r"\bnext\b", low) \
                else meeting_dt
            if re.search(r"\bmonth\b", low):
                following = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
                resolved = following - timedelta(days=1)
            else:
                resolved = base + timedelta(days=(4 - base.weekday()) % 7)
        elif re.search(r"\bnext (week|month|year)\b", low):
            if "month" in low:
                resolved = meeting_dt + timedelta(days=30)
            elif "year" in low:
                resolved = meeting_dt + timedelta(days=365)
            else:
                resolved = meeting_dt + timedelta(days=7)
        elif weekday:
            # the soonest occurrence of that weekday after the meeting date
            # (if the meeting was on that weekday itself, use +7 days)
            target = _WEEKDAYS[weekday.group(1).lower()]
            ahead = (target - meeting_dt.weekday()) % 7 or 7
            resolved = meeting_dt + timedelta(days=ahead)

        return "relative", resolved.strftime("%Y-%m-%d") if resolved else None

    # --- text that we cannot classify ------------------------------------
    return "unclassified", None


# ---------------------------------------------------------------------------
# a deadline that the model buried inside the task sentence
#
# Small models often answer "task": "Send the notes by next Monday" with
# "deadline": null. We COPY that wording into the deadline field (the task
# text itself is never rewritten) - and only when classify_deadline()
# understands the wording, so nothing is ever guessed.
# ---------------------------------------------------------------------------
TASK_DEADLINE = re.compile(
    r"\s*[-,;:(]*\s*\b(?:by|before|due(?:\s+by)?|until|no later than)\s+"
    r"(?P<dl>[^;.|\n]+?)\s*\)?\.?\s*$", re.I)


def extract_task_deadline(task: str) -> str | None:
    """'Prepare the slides - due end of next week' -> 'end of next week'."""
    match = TASK_DEADLINE.search(task or "")
    if not match:
        return None
    candidate = match.group("dl").strip()
    if not candidate or candidate.lower() in EMPTY_MARKERS:
        return None
    dtype, _resolved = classify_deadline(candidate, None)
    if dtype not in ("relative", "absolute"):
        return None              # wording we do not understand -> leave it
    return candidate


# ---------------------------------------------------------------------------
# AI answer normalization (protects us from malformed model output)
# ---------------------------------------------------------------------------
def _clean_text(value, limit: int = 4000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def _clean_optional(value) -> str | None:
    """Return a non-empty string, or None - never a fake placeholder."""
    text = _clean_text(value, 200)
    if text.lower() in EMPTY_MARKERS:
        return None
    return text or None


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [line for line in value.splitlines() if line.strip()]
    if value is None:
        return []
    return [value]


def build_email_from_parts(summary: str, decisions: list, items: list,
                           title: str) -> tuple[str, str]:
    """Template e-mail used only when the model forgot to return one."""
    lines = ["Hi all,", "", "Thank you for the meeting." ]
    if summary:
        lines += ["", summary]
    if decisions:
        lines += ["", "Decisions:"]
        for d in decisions:
            flag = "(confirmed)" if d.get("status") == "confirmed" else "(suggested)"
            lines.append(f"- {d.get('decision')} {flag}")
    if items:
        lines += ["", "Action items:"]
        for it in items:
            owner = it.get("owner") or "Not specified"
            deadline = it.get("deadline") or "no deadline given"
            lines.append(f"- {it.get('task')} (Owner: {owner} | Due: {deadline})")
    lines += ["", "Please let me know if anything is missing.", "", "[Your name]"]
    subject = f"Follow-up: {title}" if title else "Meeting follow-up"
    return subject, "\n".join(lines)


def normalize(result: dict, source_text: str, meeting_date, model_label: str) -> dict:
    """Force the model's answer into our safe structure."""
    if not isinstance(result, dict):
        raise AIError("The AI response was not a JSON object.")

    # --- title ------------------------------------------------------------
    title = _clean_text(result.get("title"), 200)
    if not title:
        for line in source_text.splitlines():
            if line.strip():
                title = _clean_text(line.strip(), 80)
                break
        title = title or (f"Meeting on {meeting_date}" if meeting_date
                          else "Untitled meeting")

    # --- summary ----------------------------------------------------------
    summary = _clean_text(result.get("summary"), 4000) or "No summary provided."

    # --- key points -------------------------------------------------------
    key_points = []
    for point in _as_list(result.get("key_points")):
        point = _clean_text(point, 400)
        if point and point not in key_points:
            key_points.append(point)

    # --- decisions (confirmed vs suggested) -------------------------------
    decisions = []
    for raw in _as_list(result.get("decisions")):
        if isinstance(raw, dict):
            text = _clean_text(raw.get("decision") or raw.get("text"), 600)
            status = _clean_text(raw.get("status"), 40).lower()
        else:
            text = _clean_text(raw, 600)
            status = ""
        if not text:
            continue
        if status.startswith("confirm") or status.startswith("agree") \
                or status.startswith("approv"):
            status = "confirmed"
        elif status.startswith("suggest") or status.startswith("propos") \
                or status.startswith("discuss") or status.startswith("tentative"):
            status = "suggested"
        else:
            status = "not_specified"
        decisions.append({"decision": text, "status": status})

    # --- action items -----------------------------------------------------
    action_items = []
    for raw in _as_list(result.get("action_items")):
        if isinstance(raw, dict):
            task = _clean_text(raw.get("task") or raw.get("action") or raw.get("text"), 500)
            owner = _clean_optional(raw.get("owner"))
            deadline = _clean_optional(raw.get("deadline"))
            declared_type = _clean_text(raw.get("deadline_type"), 30).lower()
            status = _clean_text(raw.get("status"), 30)
        else:
            task = _clean_text(raw, 500)
            owner = None
            deadline = None
            declared_type = ""
            status = ""
        if not task:
            continue

        dtype, resolved = classify_deadline(deadline, meeting_date)
        if deadline and declared_type in db.DEADLINE_TYPES and dtype == "unclassified":
            dtype = declared_type                # trust the model if we failed
        if status not in db.STATUSES:          # only the three allowed values
            status = "Not started"
        action_items.append({
            "task": task,
            "owner": owner,
            "deadline": deadline,
            "deadline_type": dtype,
            "deadline_resolved": resolved,
            "status": status,
        })

    # --- follow-up email --------------------------------------------------
    email = result.get("follow_up_email") or result.get("email") or {}
    if isinstance(email, str):
        email = {"subject": "", "body": email}
    if not isinstance(email, dict):
        email = {}
    subject = _clean_text(email.get("subject"), 300)
    body = str(email.get("body") or "").strip()
    if not body:
        subject, body = build_email_from_parts(summary, decisions,
                                               action_items, title)
    elif not subject:
        subject = f"Follow-up: {title}"

    return {
        "title": title,
        "summary": summary,
        "key_points": key_points,
        "decisions": decisions,
        "action_items": action_items,
        "email_subject": subject,
        "email_body": body,
        "ai_model": model_label,
    }


# ---------------------------------------------------------------------------
# JSON parsing that survives bad model output
# ---------------------------------------------------------------------------
def parse_json(raw: str) -> dict:
    """Extract a JSON object from the model answer, or raise AIError."""
    if raw is None or not str(raw).strip():
        raise AIError("The AI returned an empty response. Please try again.")

    text = str(raw).strip()

    # strip ```json ... ``` fences if present
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()

    for candidate in (text,
                      text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            continue

    raise AIError(
        "The AI returned an unreadable answer (not valid JSON). "
        "Please try again."
    )


# ---------------------------------------------------------------------------
# the offline fallback (works with no Ollama model at all)
# ---------------------------------------------------------------------------
_BULLET = re.compile(r"^\s*(?:[-*•o]\s+|\d{1,2}[.)]\s+)")
_DECIDED = re.compile(
    r"\b(decided|agreed|approved|confirmed|finalized|signed off|"
    r"we will go with|it was agreed|resolved)\b", re.I)
_SUGGESTED = re.compile(
    r"\b(suggested|proposed|consider|considering|maybe|might|could|"
    r"tentative|idea|recommend|recommendation|for discussion)\b", re.I)
_OWNER_TASK = re.compile(
    r"^(?:[-*•o]\s+|\d{1,2}[.)]\s+)?"
    r"(?P<owner>[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+)?)\s+"
    r"(?P<verb>will|to|is going to|owns|to handle)\s+(?P<task>.+)$")
_ACTION_HINT = re.compile(
    r"\b(action item|todo|to-do|follow[- ]?up|will send|will prepare|"
    r"will update|will share|will review|will book|needs to|must|"
    r"responsible for|assigned to|due by|deadline)\b", re.I)
_DEADLINE_PHRASE = re.compile(
    r"\b(?:by|before|due|due by|until|deadline(?: is)?|no later than)\s+"
    r"(?P<dl>[^.,;|\n]+)", re.I)


def local_analyze(text: str, meeting_date: str | None) -> dict:
    """
    Built-in, offline analysis used when no Ollama model is configured.

    It only reuses sentences that are really in the notes - it never
    invents an owner, a date or a decision.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # title: first short line, otherwise the first few words
    title = ""
    if lines:
        first = lines[0]
        title = first if len(first) <= 90 else " ".join(first.split()[:12]) + "..."
    title = title.strip("-# ") or "Untitled meeting"

    # summary: first paragraph, cut to ~450 characters at a sentence border
    summary = paragraphs[0] if paragraphs else text
    if len(summary) > 450:
        cut = summary[:450]
        summary = cut.rsplit(".", 1)[0] + "." if "." in cut else cut + "..."
    summary = summary or "No summary available."

    # key points: bulleted / numbered lines
    key_points: list[str] = []
    for line in lines:
        if _BULLET.match(line):
            point = _BULLET.sub("", line).strip()
            if 3 < len(point) <= 300 and point not in key_points:
                key_points.append(point)
        if len(key_points) >= 8:
            break

    # decisions: sentences that clearly decided / suggested something
    decisions, action_items = [], []
    for line in lines:
        plain = _BULLET.sub("", line).strip()
        if len(plain) < 8:
            continue

        if _DECIDED.search(plain) and len(plain) <= 600:
            decisions.append({"decision": plain, "status": "confirmed"})
        elif _SUGGESTED.search(plain) and len(plain) <= 600:
            decisions.append({"decision": plain, "status": "suggested"})

        if _ACTION_HINT.search(plain) or _OWNER_TASK.match(plain):
            owner = None
            task = plain
            match = _OWNER_TASK.match(plain)
            if match:
                owner = match.group("owner")
                task = f"{owner} {match.group('verb')} {match.group('task')}"
            deadline = None
            dl_match = _DEADLINE_PHRASE.search(plain)
            if dl_match:
                deadline = dl_match.group("dl").strip()
            if len(task) <= 500 and not any(task == a["task"] for a in action_items):
                dtype, resolved = classify_deadline(deadline, meeting_date)
                action_items.append({
                    "task": task, "owner": owner, "deadline": deadline,
                    "deadline_type": dtype, "deadline_resolved": resolved,
                    "status": "Not started",
                })

        if len(decisions) >= 8 and len(action_items) >= 10:
            break

    decisions = decisions[:8]
    action_items = action_items[:10]
    subject, body = build_email_from_parts(summary, decisions, action_items, title)

    return {
        "title": title,
        "summary": summary,
        "key_points": key_points,
        "decisions": decisions,
        "action_items": action_items,
        "email_subject": subject,
        "email_body": body,
        "ai_model": "Offline fallback (no AI model configured)",
    }


# ---------------------------------------------------------------------------
# the AI call (local Ollama, through ai_service)
# ---------------------------------------------------------------------------
def analyze(text: str, meeting_date: str | None = None) -> dict:
    """
    Main entry point used by the Flask route.

    Returns a normalized dict, ready to be stored in SQLite.
    Raises AIError with a user-friendly message when something goes wrong.
    """
    text = (text or "").strip()
    if not text:
        raise AIError("There is no meeting content to analyze.")
    if len(text) > MAX_API_CHARS:
        raise AIError(
            f"The meeting text is too long ({len(text):,} characters). "
            f"Please shorten it to under {MAX_API_CHARS:,} characters."
        )

    url, model = get_ai_config()
    if not model:                       # no model configured -> offline mode
        return local_analyze(text, meeting_date)

    # 1. ask the local model (AIServiceError carries a friendly message)
    try:
        payload = ai_service.analyze_meeting(text, meeting_date,
                                             model=model, url=url)
    except ai_service.AIServiceError as exc:
        raise AIError(str(exc)) from exc

    # 2. map the service field names onto the names the app/database use
    result = {
        "title": payload.get("title"),
        "summary": payload.get("meeting_summary"),
        "key_points": payload.get("key_points"),
        "decisions": payload.get("decisions"),
        "action_items": payload.get("action_items"),
        "follow_up_email": payload.get("follow_up_email"),
    }

    # 3. small models often bury the deadline inside the task sentence
    #    ("... by next Monday") - copy it into the deadline field.
    for item in result.get("action_items") or []:
        if isinstance(item, dict) and not item.get("deadline"):
            found = extract_task_deadline(str(item.get("task") or ""))
            if found:
                item["deadline"] = found

    # 4. second validation pass (deadline classification, null handling, ...)
    return normalize(result, text, meeting_date, model)


def test_connection() -> tuple[bool, str]:
    """Tiny ping used by the Settings page (delegates to ai_service)."""
    url, model = get_ai_config()
    if not model:
        return True, ("No Ollama model configured - the app is running in "
                      "offline fallback mode.")
    return ai_service.test_connection(model=model, url=url)


# ---------------------------------------------------------------------------
# question answering ("ask about this meeting" / "ask across meetings")
# ---------------------------------------------------------------------------
CHAT_HISTORY_TURNS = 6          # previous messages sent along for context

# Words that carry no meaning for the keyword search
STOPWORDS = {
    "what", "when", "where", "which", "who", "whom", "how", "why", "did",
    "does", "the", "and", "for", "are", "was", "were", "with", "that",
    "this", "these", "those", "from", "about", "tell", "give", "show",
    "list", "please", "can", "could", "would", "should", "any", "all",
    "our", "we", "you", "she", "him", "her", "their", "there", "been",
    "have", "has", "had", "meeting", "meetings", "discuss", "discussed",
}


def chat_context(meeting: dict, history: list[dict] | None = None) -> str:
    """Turn one meeting (plus its saved chat) into the text the model reads."""
    when = meeting.get("meeting_date") or "no date given"
    parts = [f"Meeting: {meeting.get('title') or 'Untitled'} ({when})",
             f"Summary: {meeting.get('summary') or ''}"]

    points = meeting.get("key_points") or []
    if points:
        parts.append("Key points:\n" + "\n".join(f"- {p}" for p in points))

    decisions = meeting.get("decisions") or []
    if decisions:
        parts.append("Decisions:\n" + "\n".join(
            f"- {d.get('decision')} [{d.get('status') or 'not specified'}]"
            for d in decisions if isinstance(d, dict)))

    items = meeting.get("action_items") or []
    if items:
        parts.append("Action items:\n" + "\n".join(
            f"- {i.get('task')} | Owner: {i.get('owner') or 'Not specified'}"
            f" | Deadline: {i.get('deadline') or 'Not specified'}"
            f" | Status: {i.get('status') or 'Not started'}"
            for i in items if isinstance(i, dict)))

    parts.append("Original notes:\n" + (meeting.get("source_text") or ""))

    if history:
        recent = "\n".join(
            f"{'User' if h.get('role') == 'user' else 'Assistant'}: "
            f"{h.get('content')}"
            for h in history[-CHAT_HISTORY_TURNS:] if isinstance(h, dict))
        if recent:
            parts.append("Recent conversation:\n" + recent)

    return "\n\n".join(part for part in parts if part).strip()


def ask_meeting(meeting: dict, history: list[dict], question: str) -> str:
    """Answer a question about ONE meeting. Raises AIError when it cannot."""
    url, model = get_ai_config()
    if not model:
        raise AIError("Chat needs a local Ollama model. Set one in "
                      "Settings (or OLLAMA_MODEL in .env).")
    try:
        return ai_service.ask_meeting(chat_context(meeting, history),
                                      question, model=model, url=url)
    except ai_service.AIServiceError as exc:
        raise AIError(str(exc)) from exc


def _relevance(meeting: dict, words: list[str]) -> int:
    """How many question words appear in this meeting (title counts double)."""
    title = str(meeting.get("title") or "").lower()
    haystack = " ".join([
        title,
        str(meeting.get("summary") or ""),
        str(meeting.get("source_text") or ""),
    ]).lower()
    score = 0
    for word in words:
        if word in haystack:
            score += 2 if word in title else 1
    return score


def find_relevant_meetings(question: str,
                           limit: int = ai_service.CROSS_MAX_MEETINGS) -> list[dict]:
    """The meetings whose notes mention the question's keywords (best first)."""
    words = [w for w in re.findall(r"[a-z0-9']{3,}",
                                   (question or "").lower())
             if w not in STOPWORDS][:6]
    if not words:
        return []

    candidates: dict[int, dict] = {}
    for word in words:
        for row in db.search_meeting_texts(word, limit=limit * 4):
            candidates.setdefault(row["id"], row)

    ranked = sorted(candidates.values(),
                    key=lambda m: _relevance(m, words), reverse=True)
    return [m for m in ranked[:limit] if _relevance(m, words) > 0]


def ask_all(meetings: list[dict], question: str) -> str:
    """Answer a question across several meetings (the answer names them)."""
    url, model = get_ai_config()
    if not model:
        raise AIError("Ask needs a local Ollama model. Set one in "
                      "Settings (or OLLAMA_MODEL in .env).")

    blocks = []
    for meeting in meetings[:ai_service.CROSS_MAX_MEETINGS]:
        when = meeting.get("meeting_date") or "no date"
        header = f"=== Meeting: {meeting.get('title') or 'Untitled'} ({when}) ==="
        body = ((meeting.get("summary") or "").strip()[:1500] + "\n" +
                (meeting.get("source_text") or "")[:ai_service.CROSS_SNIPPET_CHARS])
        blocks.append(f"{header}\n{body.strip()}")

    try:
        return ai_service.ask_across("\n\n".join(blocks), question,
                                     model=model, url=url)
    except ai_service.AIServiceError as exc:
        raise AIError(str(exc)) from exc
