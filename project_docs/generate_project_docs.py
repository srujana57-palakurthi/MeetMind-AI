"""
generate_project_docs.py
========================
Creates the two project documents for presentations / submission:

  1. Meeting_Analyzer_Presentation.pptx  (12 slides)
  2. Meeting_Analyzer_Documentation.docx  (full written documentation)

Run it from this folder:

    cd project_docs
    ..\\venv\\Scripts\\python.exe generate_project_docs.py

Both files are regenerated from scratch each time, so you can edit the
text below (for example, add your own name on the title slide) and run
it again. Needs the optional packages:

    pip install python-pptx          (the .pptx; python-docx is already there)
"""

import os

from docx import Document
from docx.shared import Pt
from pptx import Presentation
from pptx.util import Pt as PptPt

HERE = os.path.dirname(os.path.abspath(__file__))
YOUR_NAME = "Your Name"          # <-- change this to your own name
COURSE = "Your Course / College"  # <-- change this too

# =========================================================================
# 1. POWERPOINT PRESENTATION
# =========================================================================

# Each slide is (title, [bullet, bullet, ...]) - sub-bullets start with "  "
SLIDES = [
    ("Meeting Analyzer", [
        "AI-powered meeting notes analyzer",
        "Summaries - decisions - action items - follow-up emails - chat - audio",
        "100% local: Flask + SQLite + Ollama (qwen2.5:3b) + Whisper",
        f"{YOUR_NAME} - {COURSE}",
        "October 2026",
    ]),
    ("The problem", [
        "Meeting notes are long, messy and rarely re-read",
        "Tasks get lost because owners and deadlines are not written down",
        "Writing the summary and the follow-up email takes extra time",
        "Cloud AI tools are paid, and company notes should not leave the machine",
    ]),
    ("Our solution", [
        "Paste notes OR upload .txt / .pdf / .docx OR upload a recording (.mp3/.wav/...)",
        "One click produces: title, summary, key points, decisions,",
        "action items (task + owner + deadline + status) and an e-mail draft",
        "Everything runs on your own computer - no API key, no cloud",
    ]),
    ("How it works", [
        "1. Upload / paste  ->  file_extractor.py reads TXT, PDF, DOCX",
        "2. ai_service.py sends the text to the local Ollama server (/api/chat)",
        "3. Strict JSON validation - broken answers never crash the app",
        "4. database.py saves meeting + items in SQLite (placeholder SQL)",
        "5. Bootstrap web pages: dashboard, meeting page, history, Ask",
    ]),
    ("Tech stack", [
        "Backend:  Python 3.11, Flask (app factory-free, small and readable)",
        "Database: SQLite (meetings, action_items, meeting_chats, app_settings)",
        "Frontend: HTML + Bootstrap 5 + vanilla JavaScript (AJAX)",
        "AI:       Ollama + qwen2.5:3b (3.2 GB, runs offline on CPU)",
        "Audio:    faster-whisper (local speech-to-text, optional)",
    ]),
    ("AI integration", [
        "requests.post(OLLAMA_URL, ...) with stream=false and format=json",
        "System prompt = INSTRUCTIONS in ai_service.py (rules + output schema)",
        "The model must return strict JSON; markdown fences and prose are removed",
        "Validation: every field checked BEFORE it is written to SQLite",
        "Friendly errors for: server down, timeout, model missing, bad JSON",
    ]),
    ("Honesty rules (the interesting part)", [
        "Owners / deadlines not in the notes -> null -> 'Not specified'",
        "'20 Oct 2026' = absolute date;  'next Monday' = relative date",
        "Relative dates are resolved only when the meeting date is known",
        "'agreed...' = confirmed;  'discussed...' = suggested - never merged",
        "Chat and Ask must answer ONLY from the notes, or say 'not mentioned'",
    ]),
    ("New AI features", [
        "Chat with a meeting: 'What did Alice promise, and by when?'",
        "  - conversation saved in SQLite, deleted with the meeting",
        "Ask across ALL meetings: keywords pick the best 5 meetings,",
        "  the answer links every meeting it used (retrieval + citation)",
        "Audio: record a meeting -> Whisper transcribes locally -> analyze",
    ]),
    ("Security", [
        "No API key exists; nothing is sent to the cloud",
        "Uploads: extension AND file signature checked, size limited, in-memory",
        "SQL: only ? placeholders (no injection); ON DELETE CASCADE",
        "AI output is rendered as plain text (no HTML injection)",
        ".env is git-ignored; its values are never sent to the browser",
    ]),
    ("Testing", [
        "selftest.py  - 120+ unit checks (mock Ollama server in 9 failure modes)",
        "smoke_test.py - 60+ HTTP checks of every route with the real model",
        "mock_ollama.py - run the whole app without Ollama installed",
        "Real-speech test: WAV recording -> transcript -> analysis -> chat",
        "All tests green: ALL TESTS PASSED / ALL HTTP TESTS PASSED",
    ]),
    ("Live demo", [
        "1. New Meeting -> upload samples\\sample_meeting_minutes.docx -> Analyze",
        "2. Edit an action item, save, download the HTML report / CSV",
        "3. Ask about this meeting: 'Who must do what, and by when?'",
        "4. Ask page: 'What deadlines do we have?' - answer cites meetings",
        "5. New Meeting -> upload samples\\sample_meeting_audio.wav -> Transcribe",
    ]),
    ("Future scope", [
        "User accounts (Flask-Login) - one workspace per person",
        "Deadline calendar (.ics export) and overdue notifications",
        "PDF export of the report; SMTP send button (always opt-in)",
        "Stronger retrieval (embeddings) for the Ask page",
        "Streaming answers while the model writes them",
        "Thank you - questions?",
    ]),
]

prs = Presentation()
for title, bullets in SLIDES:
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.placeholders[1].text_frame
    body.clear()
    for index, bullet in enumerate(bullets):
        paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
        indented = bullet.startswith("  ")
        paragraph.text = bullet.strip()
        paragraph.level = 1 if indented else 0
        paragraph.font.size = PptPt(18 if indented else 20)

pptx_path = os.path.join(HERE, "Meeting_Analyzer_Presentation.pptx")
prs.save(pptx_path)
print(f"created {os.path.basename(pptx_path)} ({len(SLIDES)} slides)")

# =========================================================================
# 2. WORD DOCUMENTATION
# =========================================================================
doc = Document()

style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)

doc.add_heading("Meeting Analyzer - Project Documentation", 0)
doc.add_paragraph(f"Student: {YOUR_NAME}")
doc.add_paragraph(f"Course: {COURSE}")
doc.add_paragraph("Date: October 2026")
doc.add_paragraph("")

doc.add_heading("1. Introduction", 1)
doc.add_paragraph(
    "Meeting Analyzer is a local web application that turns raw meeting notes "
    "into structured results: a short summary, key discussion points, decisions "
    "(confirmed vs suggested), action items with owners, deadlines and status, "
    "and a professional follow-up e-mail draft. It also answers questions about "
    "the meetings (chat) and transcribes audio recordings - all on the user's "
    "own computer, with no cloud service and no API key.")
doc.add_paragraph(
    "Goal: never invent information. Anything the notes do not contain is shown "
    "as 'Not specified' instead of being guessed by the AI.")

doc.add_heading("2. Features", 1)
for text in [
    "Paste notes or upload .txt / .pdf / .docx (extension + signature checked).",
    "Optional meeting date enables resolution of relative deadlines "
    "('next Monday' -> 2026-10-12).",
    "Editable action-item table (task / owner / deadline / status) with save.",
    "Editable follow-up e-mail: copy to clipboard or download - never auto-sent.",
    "HTML report and CSV download per meeting.",
    "Meeting history with search; dashboard with totals and pending tasks.",
    "Chat with a meeting - conversation saved in SQLite.",
    "Ask across all meetings - the answer lists the meetings it used.",
    "Audio transcription (optional faster-whisper): mp3/wav/m4a/webm/ogg/flac.",
    "Settings page: Ollama URL/model, Whisper model, connection test.",
]:
    doc.add_paragraph(text, style="List Bullet")

doc.add_heading("3. Architecture", 1)
doc.add_paragraph(
    "One small file per responsibility, so every part can be read alone:")
table = doc.add_table(rows=1, cols=2)
table.rows[0].cells[0].text = "File"
table.rows[0].cells[1].text = "Responsibility"
for name, what in [
    ("app.py", "Flask routes: validation -> processor -> database -> template"),
    ("database.py", "All SQLite operations (? placeholders, cascade deletes)"),
    ("ai_service.py", "Talks to Ollama /api/chat; prompt, JSON parse, errors"),
    ("ai_processor.py", "Validation, deadline logic, Q&A context, offline mode"),
    ("audio_service.py", "Local speech-to-text with faster-whisper (optional)"),
    ("file_extractor.py", "Reads TXT / PDF / DOCX safely (in memory)"),
    ("selftest.py", "120+ unit checks incl. a mock Ollama server"),
    ("smoke_test.py", "60+ HTTP checks of every route"),
]:
    cells = table.add_row().cells
    cells[0].text = name
    cells[1].text = what

doc.add_heading("4. Database (SQLite)", 1)
doc.add_paragraph("meetings: id, title, summary, key_points, decisions, "
                  "email_subject, email_body, meeting_date, source_filename, "
                  "source_text, ai_model, created_at")
doc.add_paragraph("action_items: id, meeting_id (FK, cascade), task, owner, "
                  "deadline, deadline_type, deadline_resolved, status, position")
doc.add_paragraph("meeting_chats: id, meeting_id (FK, cascade), role "
                  "(user/assistant), content, created_at")
doc.add_paragraph("app_settings: key, value (non-secret overrides of .env)")

doc.add_heading("5. AI integration (local Ollama)", 1)
doc.add_paragraph(
    "ai_service.py sends one POST to OLLAMA_URL (default "
    "http://localhost:11434/api/chat) with the system prompt INSTRUCTIONS and "
    "the transcript, stream=false and format=JSON, temperature 0.2 and a "
    "(5s, 240s) timeout. The answer must be strict JSON; the code removes "
    "markdown fences / surrounding prose and validates every field before "
    "anything is stored. Failure modes (server down, model missing, timeout, "
    "empty or broken JSON) each produce a friendly, specific message.")

doc.add_heading("6. Honesty rules", 1)
for text in [
    "Missing owner or deadline -> null -> displayed as 'Not specified'.",
    "deadline_type is computed by the app, never trusted from the model.",
    "Relative dates resolve only when the meeting date is provided.",
    "'It was agreed ...' -> confirmed; 'It was discussed ...' -> suggested.",
    "Chat/Ask answer only from the notes; otherwise they must say "
    "'That is not mentioned in these notes.'",
]:
    doc.add_paragraph(text, style="List Bullet")

doc.add_heading("7. Question answering and audio", 1)
doc.add_paragraph(
    "Chat builds a context (summary, decisions, items, original notes and the "
    "last 6 messages) and asks the model a plain-text question. Ask splits the "
    "question into keywords, ranks meetings whose title/notes match (title "
    "counts double), sends the best 5 with their summaries and requires the "
    "answer to name the meetings used; with no match no AI call is made. "
    "Audio uploads are validated (extension, real decoding, <= 2 h), written "
    "to a temp file that is always deleted, and transcribed locally by "
    "Whisper (CPU int8, model cached after first use).")

doc.add_heading("8. Security", 1)
for text in [
    "No API key anywhere; .env is git-ignored and never sent to the browser.",
    "Uploads: extension + file signature, size limits (5 MB docs / 50 MB "
    "audio), read in memory.",
    "SQL uses only placeholders; foreign keys with ON DELETE CASCADE.",
    "AI output rendered as plain text (Jinja escaping / textContent).",
    "Questions limited to 500 characters; AI context to ~24k characters.",
]:
    doc.add_paragraph(text, style="List Bullet")

doc.add_heading("9. Testing", 1)
doc.add_paragraph(
    "selftest.py starts a mock Ollama server in-process and checks the happy "
    "path plus 9 failure modes, deadline classification, retrieval, chat "
    "persistence and the file readers - 120+ checks, all green. "
    "smoke_test.py runs against the live app with the real qwen2.5:3b model "
    "and covers every route - 60+ checks, all green. A real-speech test "
    "recorded a WAV, transcribed it, analyzed the transcript and chatted "
    "with the resulting meeting.")

doc.add_heading("10. How to run", 1)
doc.add_paragraph("1. Install Ollama (https://ollama.com) and run: "
                  "ollama pull qwen2.5:3b")
doc.add_paragraph("2. cd meeting-summarizer")
doc.add_paragraph("3. python -m venv venv   (already done on this machine)")
doc.add_paragraph("4. pip install -r requirements.txt")
doc.add_paragraph("5. (optional audio) pip install -r requirements-audio.txt")
doc.add_paragraph("6. python app.py  ->  http://127.0.0.1:5000")

doc.add_heading("11. Sample files (for the demo)", 1)
for text in [
    "samples\\sample_meeting.txt - pasted notes (TXT)",
    "samples\\sample_meeting_minutes.docx - Word meeting minutes",
    "samples\\sample_meeting_notes.pdf - PDF client-call notes",
    "samples\\sample_meeting_audio.wav - a spoken meeting recording",
]:
    doc.add_paragraph(text, style="List Bullet")

doc.add_heading("12. Conclusion and future scope", 1)
doc.add_paragraph(
    "Meeting Analyzer shows how a small, readable Flask codebase can combine "
    "file processing, a local LLM and careful validation into a trustworthy "
    "tool. The next steps are user accounts, a deadline calendar, PDF export "
    "and stronger retrieval for the Ask page.")

docx_path = os.path.join(HERE, "Meeting_Analyzer_Documentation.docx")
doc.save(docx_path)
print(f"created {os.path.basename(docx_path)}")
print("done")
