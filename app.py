"""
app.py
======
Flask entry point for the "Meeting Analyzer" application.

Run it with:      python app.py
Then open:        http://127.0.0.1:5000

The routes in this file only coordinate things:
    request validation  ->  file_extractor / ai_processor  ->  database
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, url_for)

import ai_processor as ai
import audio_service as au
import database as db
import file_extractor as fx

# Load OLLAMA_URL / OLLAMA_MODEL / ... from the .env file (never from code)
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# Limits: documents are kept small (the AI reads them whole); audio
# recordings are bigger, so the hard request cap is the larger of the two.
TEXT_MAX_MB = int(os.getenv("MAX_FILE_SIZE_MB", "5"))
AUDIO_MAX_MB = int(os.getenv("MAX_AUDIO_MB", "50"))
app.config["MAX_CONTENT_LENGTH"] = max(TEXT_MAX_MB, AUDIO_MAX_MB) * 1024 * 1024

# Create the SQLite tables as soon as the app starts (safe to call repeatedly)
db.init_db()

MAX_TEXT_CHARS = ai.MAX_API_CHARS
MIN_TEXT_CHARS = 30

# Valid task statuses (also used by the front-end dropdowns)
STATUS_CHOICES = list(db.STATUSES)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _max_upload_bytes() -> int:
    """Hard limit for TXT/PDF/DOCX documents."""
    return TEXT_MAX_MB * 1024 * 1024


def _max_audio_bytes() -> int:
    """Hard limit for uploaded recordings."""
    return AUDIO_MAX_MB * 1024 * 1024


def _friendly_too_large() -> str:
    return (f"The upload is too large - documents may be up to "
            f"{TEXT_MAX_MB} MB and audio recordings up to {AUDIO_MAX_MB} MB.")


def _parse_date(value: str) -> str | None:
    """Validate the optional meeting date (yyyy-mm-dd). Returns None if empty."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError("Meeting date must use the YYYY-MM-DD format.")


def _clean(value, limit: int) -> str:
    return (value or "").strip()[:limit]


@app.template_filter("datefmt")
def datefmt(value: str | None) -> str:
    """2026-10-20 -> 20 Oct 2026 (safe for anything unparsable)."""
    if not value:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value)[:19], fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return str(value)


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    data = db.dashboard_stats()
    return render_template("dashboard.html", **data)


# ---------------------------------------------------------------------------
# create a new analysis
# ---------------------------------------------------------------------------
@app.route("/new")
def new_meeting():
    return render_template("new_meeting.html",
                           max_mb=TEXT_MAX_MB,
                           audio_max_mb=AUDIO_MAX_MB,
                           audio_available=au.is_available(),
                           configured=ai.is_configured())


@app.route("/analyze", methods=["POST"])
def analyze():
    """Validate the input, run the AI and store the result."""
    back = url_for("new_meeting")

    pasted = (request.form.get("notes") or "").strip()
    uploaded = request.files.get("file")

    # ---- meeting date ----------------------------------------------------
    try:
        meeting_date = _parse_date(request.form.get("meeting_date"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(back)

    # ---- uploaded file ---------------------------------------------------
    file_text, filename = "", None
    if uploaded and uploaded.filename:
        filename = uploaded.filename
        try:
            fx.validate_filename(filename)
        except fx.FileValidationError as exc:
            flash(str(exc), "danger")
            return redirect(back)

        try:
            data = uploaded.stream.read(_max_upload_bytes() + 1)
        except Exception:
            flash("The uploaded file could not be read.", "danger")
            return redirect(back)

        if len(data) > _max_upload_bytes():
            flash(_friendly_too_large(), "danger")
            return redirect(back)

        try:
            file_text = fx.extract_text(data, filename)
        except fx.FileValidationError as exc:
            flash(str(exc), "danger")
            return redirect(back)

    # ---- combined content ------------------------------------------------
    content = "\n\n".join(part for part in (pasted, file_text) if part).strip()

    if not content:
        flash("Please paste some meeting notes or upload a TXT, PDF or DOCX "
              "file before analyzing.", "warning")
        return redirect(back)
    if len(content) < MIN_TEXT_CHARS:
        flash(f"The meeting content is too short ({len(content)} characters). "
              f"Please provide at least {MIN_TEXT_CHARS} characters so there "
              "is something to analyze.", "warning")
        return redirect(back)
    if len(content) > MAX_TEXT_CHARS:
        flash(f"The meeting content is too long ({len(content):,} characters). "
              f"Please shorten it to under {MAX_TEXT_CHARS:,} characters.",
              "warning")
        return redirect(back)

    # ---- AI --------------------------------------------------------------
    try:
        result = ai.analyze(content, meeting_date)
    except ai.AIError as exc:
        flash(str(exc), "danger")
        return redirect(back)
    except Exception:                       # never leak a stack trace
        flash("Something went wrong while analyzing the meeting. "
              "Please try again.", "danger")
        return redirect(back)

    # ---- store -----------------------------------------------------------
    meeting_id = db.create_meeting({
        **result,
        "meeting_date": meeting_date,
        "source_filename": filename,
        "source_text": content,
    })
    flash("Meeting analyzed successfully. You can edit the results below.",
          "success")
    return redirect(url_for("meeting", meeting_id=meeting_id))


# ---------------------------------------------------------------------------
# audio transcription (optional, needs faster-whisper - see audio_service.py)
# ---------------------------------------------------------------------------
@app.route("/transcribe", methods=["POST"])
def transcribe():
    """Turn an uploaded recording into text (the page then fills the notes)."""
    audio = request.files.get("audio")
    if audio is None or not audio.filename:
        return jsonify(ok=False, error="Please choose an audio file first."), 400
    try:
        au.validate_audio(audio.filename)
    except au.AudioValidationError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    try:
        data = audio.stream.read(_max_audio_bytes() + 1)
    except Exception:
        return jsonify(ok=False, error="The uploaded file could not be read."), 400
    if len(data) > _max_audio_bytes():
        return jsonify(ok=False, error=_friendly_too_large()), 400

    try:
        result = au.transcribe(data, audio.filename,
                               db.get_setting("whisper_model") or None)
    except au.AudioValidationError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    text = (result.get("text") or "").strip()
    if len(text) < 10:
        return jsonify(ok=False, error="No speech could be detected in this "
                                       "recording."), 400
    return jsonify(ok=True, text=text, language=result.get("language"),
                   seconds=result.get("seconds"))


# ---------------------------------------------------------------------------
# meeting results
# ---------------------------------------------------------------------------
@app.route("/meeting/<int:meeting_id>")
def meeting(meeting_id):
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        abort(404)
    return render_template("meeting.html", meeting=meeting,
                           statuses=STATUS_CHOICES,
                           chat=db.list_chats(meeting_id),
                           chat_enabled=ai.is_configured())


@app.route("/meeting/<int:meeting_id>/chat", methods=["POST"])
def meeting_chat(meeting_id):
    """Answer one question about a meeting and save the exchange."""
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        abort(404)
    payload = request.get_json(silent=True) or {}
    question = _clean(payload.get("question"), 500)
    if len(question) < 2:
        return jsonify(ok=False, error="Please type a question first."), 400

    try:
        answer = ai.ask_meeting(meeting, db.list_chats(meeting_id), question)
    except ai.AIError as exc:
        return jsonify(ok=False, error=str(exc)), 503

    db.add_chat(meeting_id, "user", question)
    db.add_chat(meeting_id, "assistant", answer)
    return jsonify(ok=True, answer=answer)


@app.route("/meeting/<int:meeting_id>/chat/clear", methods=["POST"])
def meeting_chat_clear(meeting_id):
    if db.get_meeting(meeting_id) is None:
        abort(404)
    db.clear_chats(meeting_id)
    return jsonify(ok=True)


@app.route("/meeting/<int:meeting_id>/title", methods=["POST"])
def update_title(meeting_id):
    if db.get_meeting(meeting_id) is None:
        abort(404)
    payload = request.get_json(silent=True) or {}
    title = _clean(payload.get("title"), 200)
    if len(title) < 2:
        return jsonify(ok=False, error="The meeting title is too short."), 400
    db.rename_meeting(meeting_id, title)
    return jsonify(ok=True, title=title)


@app.route("/meeting/<int:meeting_id>/items", methods=["POST"])
def update_items(meeting_id):
    """Save every action-item row (update / insert / delete) in one call."""
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        abort(404)

    payload = request.get_json(silent=True) or {}
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return jsonify(ok=False, error="Invalid data received."), 400

    cleaned, seen_ids = [], set()
    for raw in raw_items[:100]:
        if not isinstance(raw, dict):
            continue
        task = _clean(raw.get("task"), 500)
        if not task:
            return jsonify(
                ok=False,
                error="Every action item needs a task description."), 400

        owner = _clean(raw.get("owner"), 120) or None
        if owner and owner.lower() in ("n/a", "none", "null", "-"):
            owner = None

        deadline = _clean(raw.get("deadline"), 120) or None
        # re-classify the deadline from the words the user typed
        dtype, resolved = ai.classify_deadline(deadline, meeting["meeting_date"])

        status = _clean(raw.get("status"), 30)
        if status not in STATUS_CHOICES:
            status = "Not started"

        item_id = raw.get("id")
        item_id = int(item_id) if str(item_id).isdigit() else None
        if item_id and item_id in seen_ids:
            item_id = None
        if item_id:
            seen_ids.add(item_id)

        cleaned.append({
            "id": item_id,
            "task": task,
            "owner": owner,
            "deadline": deadline,
            "deadline_type": dtype,
            "deadline_resolved": resolved,
            "status": status,
        })

    saved = db.save_action_items(meeting_id, cleaned)
    return jsonify(ok=True, items=saved)


@app.route("/meeting/<int:meeting_id>/email", methods=["POST"])
def update_email(meeting_id):
    if db.get_meeting(meeting_id) is None:
        abort(404)
    payload = request.get_json(silent=True) or {}
    subject = _clean(payload.get("subject"), 300)
    body = (payload.get("body") or "").strip()
    if len(body) > 20_000:
        return jsonify(ok=False, error="The email is too long (max 20,000 "
                                       "characters)."), 400
    if not subject and not body:
        return jsonify(ok=False, error="The follow-up email is empty."), 400
    db.save_email(meeting_id, subject, body)
    return jsonify(ok=True)


@app.route("/meeting/<int:meeting_id>/delete", methods=["POST"])
def delete_meeting(meeting_id):
    if db.get_meeting(meeting_id) is None:
        abort(404)
    db.delete_meeting(meeting_id)
    flash("Meeting deleted.", "success")
    return redirect(url_for("history"))


# ---------------------------------------------------------------------------
# downloads (report / tasks / email)
# ---------------------------------------------------------------------------
@app.route("/meeting/<int:meeting_id>/report")
def meeting_report(meeting_id):
    """Standalone HTML report of the summary + action items (download)."""
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        abort(404)

    def esc(value) -> str:
        return html.escape(str(value if value is not None else ""))

    points = "".join(f"<li>{esc(p)}</li>" for p in meeting["key_points"]) \
        or "<li>None recorded</li>"

    decision_rows = "".join(
        "<tr><td>{d}</td><td>{s}</td></tr>".format(
            d=esc(d.get("decision")),
            s=f'<span class="badge b-confirmed">confirmed</span>'
            if d.get("status") == "confirmed" else
            ('<span class="badge b-suggested">suggested</span>'
             if d.get("status") == "suggested"
             else '<span class="badge b-unknown">not specified</span>'))
        for d in meeting["decisions"]) \
        or '<tr><td colspan="2">None recorded</td></tr>'

    item_rows = "".join(
        "<tr><td>{t}</td><td>{o}</td><td>{dl}</td><td>{st}</td></tr>".format(
            t=esc(i.get("task")),
            o=esc(i.get("owner") or "Not specified"),
            dl=esc(i.get("deadline") or "Not specified"),
            st=esc(i.get("status")))
        for i in meeting["action_items"]) \
        or '<tr><td colspan="4">No action items</td></tr>'

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Report - {esc(meeting['title'])}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;color:#1f2937;margin:40px;line-height:1.5}}
 h1{{font-size:24px;margin-bottom:4px}} h2{{font-size:17px;margin-top:28px;
 color:#1d4ed8;border-bottom:2px solid #e5e7eb;padding-bottom:6px}}
 .meta{{color:#6b7280;font-size:13px}} table{{width:100%;border-collapse:collapse;
 margin-top:10px;font-size:14px}}
 th,td{{border:1px solid #e5e7eb;padding:8px 10px;text-align:left;
 vertical-align:top}} th{{background:#f3f4f6}}
 .badge{{padding:2px 8px;border-radius:10px;font-size:12px}}
 .b-confirmed{{background:#dcfce7;color:#166534}}
 .b-suggested{{background:#fef9c3;color:#854d0e}}
 .b-unknown{{background:#f3f4f6;color:#6b7280}}
 pre{{white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;
 padding:12px;border-radius:8px;font-family:inherit}}
 .foot{{margin-top:34px;font-size:12px;color:#9ca3af}}
</style></head><body>
<h1>{esc(meeting['title'])}</h1>
<div class="meta">Meeting date: {esc(datefmt(meeting['meeting_date']) or 'not specified')}
 &nbsp;|&nbsp; Generated: {esc(datefmt(meeting['created_at']))}
 &nbsp;|&nbsp; Analyzed by: {esc(meeting['ai_model'] or 'unknown')}</div>

<h2>Summary</h2><p>{esc(meeting['summary'])}</p>

<h2>Key discussion points</h2><ul>{points}</ul>

<h2>Decisions</h2>
<table><thead><tr><th>Decision</th><th>Status</th></tr></thead>
<tbody>{decision_rows}</tbody></table>

<h2>Action items</h2>
<table><thead><tr><th>Task</th><th>Owner</th><th>Deadline</th><th>Status</th></tr></thead>
<tbody>{item_rows}</tbody></table>

<h2>Follow-up email draft</h2>
<p><strong>Subject:</strong> {esc(meeting['email_subject'])}</p>
<pre>{esc(meeting['email_body'])}</pre>

<div class="foot">Generated by Meeting Analyzer. Owners and deadlines are
reproduced exactly as found in the meeting notes; "Not specified" means the
source did not state them.</div>
</body></html>"""

    safe_title = "".join(ch for ch in meeting["title"] if ch.isalnum()
                         or ch in (" ", "-", "_")).strip() or "meeting"
    return send_file(
        io.BytesIO(page.encode("utf-8")),
        mimetype="text/html",
        as_attachment=True,
        download_name=f"meeting-report-{meeting_id}-{safe_title[:40]}.html",
    )


@app.route("/meeting/<int:meeting_id>/tasks.csv")
def meeting_tasks_csv(meeting_id):
    """Action items as a CSV file that opens in Excel."""
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        abort(404)

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["Meeting", "Task", "Owner", "Deadline",
                     "Deadline type", "Resolved date", "Status"])
    for item in meeting["action_items"]:
        writer.writerow([
            meeting["title"],
            item.get("task") or "",
            item.get("owner") or "Not specified",
            item.get("deadline") or "Not specified",
            item.get("deadline_type") or "",
            item.get("deadline_resolved") or "",
            item.get("status") or "",
        ])

    # utf-8-sig = UTF-8 with BOM so Excel shows special characters correctly
    data = io.BytesIO(("﻿" + buffer.getvalue()).encode("utf-8"))
    return send_file(data, mimetype="text/csv", as_attachment=True,
                     download_name=f"action-items-{meeting_id}.csv")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------
@app.route("/history")
def history():
    search = _clean(request.args.get("q"), 100)
    meetings = db.list_meetings(search)
    return render_template("history.html", meetings=meetings, search=search)


# ---------------------------------------------------------------------------
# ask across ALL meetings (the answer names the meetings it used)
# ---------------------------------------------------------------------------
@app.route("/ask", methods=["GET", "POST"])
def ask():
    question, answer, sources, error = "", None, [], None
    if request.method == "POST":
        question = _clean(request.form.get("question"), 500)
        if len(question) < 3:
            error = "Please type a question (at least 3 characters)."
        elif not ai.is_configured():
            error = ("The Ask feature needs a local Ollama model - set one "
                     "in Settings (or OLLAMA_MODEL in .env).")
        else:
            sources = ai.find_relevant_meetings(question)
            if not sources:
                error = ("I could not find any meeting mentioning that. "
                         "Try different words - for example a person, "
                         "project or product name.")
            else:
                try:
                    answer = ai.ask_all(sources, question)
                except ai.AIError as exc:
                    error = str(exc)
    return render_template("ask.html", question=question, answer=answer,
                           sources=sources, error=error,
                           configured=ai.is_configured())


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        ollama_url = _clean(request.form.get("ollama_url"), 300)
        model = _clean(request.form.get("ollama_model"), 100)
        if ollama_url and not ollama_url.startswith(("http://", "https://")):
            flash("The Ollama URL must start with http:// or https://.",
                  "danger")
            return redirect(url_for("settings"))
        if ollama_url:
            db.set_setting("ollama_url", ollama_url.rstrip("/"))
        if model:
            db.set_setting("ollama_model", model)
        whisper = _clean(request.form.get("whisper_model"), 20)
        if whisper in au.MODEL_SIZES:
            db.set_setting("whisper_model", whisper)
        flash("Settings saved. Saved values override the .env defaults - "
              "no API key is needed with Ollama.", "success")
        return redirect(url_for("settings"))

    ollama_url, model = ai.get_ai_config()
    return render_template(
        "settings.html",
        ollama_url=ollama_url,
        model=model,
        ai_configured=ai.is_configured(),
        env_url=os.getenv("OLLAMA_URL", ""),
        env_model=os.getenv("OLLAMA_MODEL", ""),
        whisper_model=db.get_setting("whisper_model") or au.default_model_size(),
        whisper_sizes=list(au.MODEL_SIZES),
        audio_available=au.is_available(),
        db_path=db.DB_PATH,
        max_mb=TEXT_MAX_MB,
        audio_max_mb=AUDIO_MAX_MB,
        statuses=STATUS_CHOICES,
    )


@app.route("/settings/test", methods=["POST"])
def settings_test():
    ok, message = ai.test_connection()
    return jsonify(ok=ok, message=message)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_error):
    return render_template("errors.html", code=404,
                           title="Page not found",
                           message="That meeting or page does not exist."), 404


@app.errorhandler(413)
def too_large(_error):
    flash(_friendly_too_large(), "danger")
    return redirect(url_for("new_meeting")), 413


@app.errorhandler(500)
def server_error(_error):
    return render_template("errors.html", code=500,
                           title="Something went wrong",
                           message="An unexpected error occurred. Please go "
                                   "back and try again."), 500


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n  Meeting Analyzer is running -> http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=True)
