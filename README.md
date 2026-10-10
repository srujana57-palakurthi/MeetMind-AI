<<<<<<< HEAD
# Meeting Analyzer

**Convert meeting notes or transcripts into summaries, action items, owners, deadlines, and follow-up emails.**

A complete beginner-friendly web app built with **Python (Flask)**, **HTML/CSS/Bootstrap/JavaScript**, **SQLite**, and a **local Ollama model** (free, private, no API key).

---

## 1. What the app does

| Page | What you can do |
|------|-----------------|
| **Dashboard** | See totals (meetings, action items, pending/done tasks), recent meetings and pending tasks. |
| **New Meeting** | Paste notes **or** upload a `.txt`, `.pdf`, `.docx` file **or upload an audio recording** (`.mp3`, `.wav`, `.m4a`, ...) that is transcribed locally, optionally set the meeting date, and click **Analyze meeting**. |
| **Meeting page** | Read the title, summary, key points and decisions (marked **confirmed** vs **suggested**). Edit the action-item table (Task / Owner / Deadline / Status), add or remove rows, edit, copy or download the follow-up email, and **chat with the meeting** (*"What did Alice promise, and by when?"*). |
| **Meeting History** | Search and reopen any previous meeting; download its report or delete it. |
| **Ask** | Ask **one question across all your meetings** — the answer names the meetings it used (with links). |
| **Settings** | Set the Ollama endpoint and model, the speech-to-text model, test the connection, and see storage/limit information. |

**Generated for every meeting**

1. Meeting title
2. Short summary
3. Key discussion points
4. Decisions made (confirmed vs suggested vs not specified)
5. Action items with **task, owner, deadline and status**
6. A professional **follow-up email draft** — editable, copyable, downloadable, **never sent automatically**
7. Downloadable **HTML report** and **CSV** of the action items

**Honesty rules built into the AI**

* Missing owner or deadline → `null` → displayed as **Not specified** (never invented).
* A real date (`2026-10-20`, `Oct 20`) is marked **absolute**; a vague date (`next Monday`) is marked **relative**.
* Relative dates are resolved to a real date **only when the meeting date was provided**.
* Suggestions are never upgraded to confirmed decisions.
* Malformed AI answers (broken JSON, markdown fences, extra text) are caught safely with a friendly error instead of crashing the app.
* **Chat and Ask answers only use your notes** — if something is not in the notes, the model must say *"That is not mentioned in these notes"* instead of inventing it, and every answer links the meetings it used.

---

## 2. Project structure

```
meeting-summarizer\
├── app.py                 # Flask application (routes)
├── database.py            # all SQLite operations (incl. saved chat history)
├── ai_service.py          # talks to the local Ollama server (/api/chat)
├── ai_processor.py        # prompt result cleanup, deadline logic, Q&A, offline mode
├── audio_service.py       # local speech-to-text (optional faster-whisper)
├── file_extractor.py      # reads TXT / PDF / DOCX safely
├── requirements.txt       # Python packages
├── requirements-audio.txt # OPTIONAL: adds audio transcription
├── .env.example           # template for your secret settings (copy to .env)
├── .gitignore             # keeps .env, venv\ and *.db out of source control
├── run.bat                # one-click setup + start on Windows
├── selftest.py            # optional: unit tests of the core logic
├── smoke_test.py          # optional: HTTP tests of every route
├── mock_ollama.py         # optional: fake Ollama server for testing without it
├── meeting_app.db         # SQLite database (created automatically)
├── samples\               # ready-made demo files (2026 dates)
│   ├── sample_meeting.txt          # pasted notes
│   ├── sample_meeting_minutes.docx # Word meeting minutes
│   ├── sample_meeting_notes.pdf    # PDF client-call notes
│   └── sample_meeting_audio.wav    # a spoken meeting recording
├── project_docs\          # presentation + written documentation
│   ├── generate_project_docs.py    # regenerates both documents
│   ├── Meeting_Analyzer_Presentation.pptx
│   └── Meeting_Analyzer_Documentation.docx
├── static\
│   ├── css\style.css
│   └── js\main.js, meeting.js, chat.js
└── templates\
    ├── base.html          # navbar, flash messages, loading overlay
    ├── dashboard.html
    ├── new_meeting.html   # notes + file upload + audio transcription
    ├── meeting.html       # results + editable table + email + chat panel
    ├── history.html
    ├── ask.html           # question across all meetings (with sources)
    ├── settings.html
    └── errors.html        # 404 / 500 pages
```

---

## 3. What you need

1. **Python 3.10 or newer** — download from <https://www.python.org/downloads/>
   *During installation tick **“Add python.exe to PATH”**.*
   Check it worked: open **PowerShell** and type `python --version`.
2. **Ollama** (free, runs on your own machine) — download from
   <https://ollama.com/download> or run `winget install Ollama.Ollama`.
   Then pull the model once: `ollama pull qwen2.5:3b`.
   *No API key and no account are required. Without Ollama the app runs in
   **offline mode** with a built-in rule-based analyzer, so you can try
   everything first.*

---

## 4. How to run it on Windows (step by step)

Open **PowerShell** (Start menu → type `PowerShell`), then:

```powershell
cd "C:\Users\Admin\OneDrive\Documents\Default Project\meeting-summarizer"
```

**Step 1 — create a virtual environment**

```powershell
py -m venv venv
```

**Step 2 — activate it** (the prompt starts with `(venv)`)

```powershell
.\venv\Scripts\Activate.ps1
```

> If Windows says *“running scripts is disabled”*, run this once, then retry:
> ```powershell
> Set-ExecutionPolicy -Scope Process RemoteSigned
> ```
> **cmd users:** `venv\Scripts\activate.bat`

**Step 3 — install the packages**

```powershell
pip install -r requirements.txt
```

**Step 4 — create your settings file**

```powershell
Copy-Item .env.example .env
```
> **cmd users:** `copy .env.example .env`

Open `.env` in Notepad and check the two AI lines (the defaults are right
for a standard Ollama install):

```
OLLAMA_URL=http://localhost:11434/api/chat
OLLAMA_MODEL=qwen2.5:3b
```

Leave `OLLAMA_MODEL` empty to stay in offline mode. **No API key exists** —
everything runs locally.

**Step 5 — make sure Ollama is running**

```powershell
ollama pull qwen2.5:3b     # once per machine
ollama list                # should list qwen2.5:3b
```

**Step 6 — start the app**

```powershell
python app.py
```

**Step 7 — open the browser**

<http://127.0.0.1:5000>

Stop the server with `Ctrl + C` in PowerShell.

### Shortcut

Double-click **`run.bat`** — it creates `venv`, installs everything, copies
`.env.example` to `.env` if needed, and starts the app.

---

## 5. Try it in 30 seconds

1. Go to **New Meeting**.
2. Click **Browse…** and choose `samples\sample_meeting.txt`
   (or paste your own notes).
3. Set **Meeting date** to the date of the meeting — this lets
   *“next Monday”* and *“end of next week”* be resolved to real dates.
4. Click **Analyze meeting**.
5. On the results page: edit a task, owner or deadline, change a status, press
   **Save changes**, then **Copy** the follow-up email or **Download report**.
6. Scroll down to **Ask about this meeting** and type a question, e.g.
   *“What did Alice promise, and by when?”* — the conversation is saved.
7. Open the **Ask** page and ask the same question across **all** your meetings;
   the answer links every meeting it used.
8. *(optional, needs `requirements-audio.txt`)* On **New Meeting**, pick an
   audio recording instead of a file, press **Transcribe audio**, and the
   transcript is placed in the notes box ready to analyze.

**Ready-made demo files** (current 2026 dates) live in `samples\`:

| File | Type | Good for showing |
|---|---|---|
| `sample_meeting.txt` | text notes | the fastest start |
| `sample_meeting_minutes.docx` | Word minutes | DOCX upload + relative deadlines |
| `sample_meeting_notes.pdf` | PDF notes | PDF with selectable text |
| `sample_meeting_audio.wav` | 26 s recording | local audio transcription |

**Presentation documents** (12-slide PowerPoint + written documentation)
are in `project_docs\`. Put your own name at the top of
`project_docs\generate_project_docs.py` and run it again to regenerate
both files:

```powershell
cd project_docs
..\venv\Scripts\python.exe generate_project_docs.py
```

---

## 6. AI configuration (local Ollama)

The app sends your notes to **your own Ollama server** — nothing leaves your
machine and no API key exists anywhere in the project.

| Setting | Where | Default |
|----------|-------|---------|
| `OLLAMA_URL` | `.env` | `http://localhost:11434/api/chat` |
| `OLLAMA_MODEL` | `.env` | `qwen2.5:3b` |

Both are read with **python-dotenv** when `ai_service.py` loads. Values saved
on the **Settings** page override the `.env` defaults (the page shows the
`.env` value next to each field). Use **Test connection** to verify the whole
chain: server reachable → model downloaded → model answers.

**The request** (made with the `requests` library, `ai_service.py`):

```python
requests.post("http://localhost:11434/api/chat", json={
    "model": "qwen2.5:3b",
    "stream": False,                 # one complete answer
    "format": "json",                # Ollama JSON mode (retried without it)
    "messages": [
        {"role": "system", "content": "<the rules below>"},
        {"role": "user",   "content": "<meeting date + transcript>"},
    ],
    "options": {"temperature": 0.2},
}, timeout=(5, 240))                 # 5s connect / 240s answer
```

**Offline mode** (`OLLAMA_MODEL` empty): a built-in analyzer extracts bullet
points, decisions and tasks directly from your text. It never invents anything,
so results are simpler than real AI output.

**What the AI must return** (enforced by the prompt *and* by validation
before anything is written to SQLite):

```json
{
  "title": "...",
  "meeting_summary": "...",
  "key_points": ["..."],
  "decisions": [{"decision": "...", "status": "confirmed|suggested"}],
  "action_items": [
    {"task": "...", "owner": "Name or null",
     "deadline": "exact wording or null",
     "status": "Not started|In progress|Done"}
  ],
  "follow_up_email": {"subject": "...", "body": "..."}
}
```

`deadline_type` (`absolute` / `relative`) is **not** trusted from the model —
the app classifies the wording itself, and only resolves relative dates when
the meeting date was provided.

**Question answering (Chat + Ask)**

Both features call the same `/api/chat` endpoint but in **plain-text mode**
(`format` is not used — no JSON is expected):

* **Chat on a meeting page** sends: the title/date, summary, key points,
  decisions, action items, the original notes, and the last 6 chat messages
  (so follow-up questions work), then the question. The exchange is stored in
  the `meeting_chats` table and removed with the meeting (`ON DELETE CASCADE`).
  Use **Clear chat** to start over.
* **Ask across meetings**: the question is split into keywords; meetings whose
  title or notes contain them are ranked (title matches count double) and the
  best **5** are sent with their summaries. If **no meeting matches, no AI call
  is made** — you get “I could not find any meeting mentioning that”.
  Every answer links the meetings that were used.

**Audio transcription (optional)**

```powershell
pip install -r requirements-audio.txt     # once — adds faster-whisper
```

Recordings are transcribed **locally** (CPU, int8) by `audio_service.py`:
extension + real-decoding are checked first (broken files fail fast with a
friendly message), recordings over 2 hours are refused, the upload is written
to a temporary file that is always deleted, and the model (`tiny`/`base`/
`small`/`medium`, chosen on the **Settings** page) is loaded once and reused.
Without the package the app works exactly as before — the audio section shows
the install command instead. The transcript lands in the notes box, where you
can edit it before analyzing.

---

## 7. Security notes

* **No API key, no cloud**: the AI runs on the local Ollama server, so
  transcripts never leave your machine. `.env` (which holds the endpoint and
  model name) is listed in `.gitignore`, and those values are never sent to
  the browser either.
* **Uploads**: only `.txt`, `.pdf`, `.docx`; extension **and** file signature are
  checked; size limited to 5 MB (`MAX_FILE_SIZE_MB` in `.env`); files are read
  **in memory** and never saved to disk.
* **Database**: every query uses `?` placeholders (no SQL injection), foreign
  keys with `ON DELETE CASCADE`, and connections are closed after each call.
* **Input**: form values are stripped and length-limited, JSON bodies are
  validated, unexpected errors show a friendly 500 page instead of a stack trace.
* **Rendering**: chat answers and Ask answers are shown as plain text
  (`textContent` in JS / Jinja auto-escaping) — the model cannot inject HTML.
  Questions are limited to 500 characters and AI context to ~24k characters.
* **Audio**: uploaded recordings are read in memory, written only to a
  temporary file that is deleted afterwards, capped at 50 MB
  (`MAX_AUDIO_MB`), and transcribed locally — they never leave the machine.

---

## 8. Optional: run the tests

The project ships with three test scripts that I used while building it:

```powershell
# unit-style tests: AI logic, the Ollama service layer (with a mock server),
# deadline rules, database and file readers
python selftest.py

# (with the app already running) end-to-end HTTP tests of every route
python smoke_test.py

# optional: a fake Ollama server, to run the app without Ollama at all
python mock_ollama.py --port 11435
```

`selftest.py` prints `ALL TESTS PASSED`, `smoke_test.py` prints
`ALL HTTP TESTS PASSED`.

Covered: AI error handling (with a mock Ollama), deadline rules, **chat
persistence and cascade delete, keyword retrieval for Ask, plain-text Q&A,
audio validation**, database round trips, file readers, and every HTTP route
(pages, validation, analyze, edits, chat, ask, transcribe, downloads,
settings, delete).

**Testing the Ollama integration specifically**

1. *Unit level* — `selftest.py` starts `MockOllama` in-process and checks the
   happy path plus every failure mode (unreachable server, timeout, HTTP 404/500,
   empty answer, non-JSON body, broken JSON, missing fields) and prints
   `real Ollama reachable` when your server is up.
2. *End-to-end* — start the app, go to **Settings → Test connection**, then
   analyze `samples\sample_meeting.txt` and check the results page.
3. *Without Ollama* — run `python mock_ollama.py --port 11435`, start the app
   with `$env:OLLAMA_URL = "http://127.0.0.1:11435/api/chat"`, then run
   `python smoke_test.py`.

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `'py' is not recognized` | Reinstall Python and tick **Add python.exe to PATH**. |
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope Process RemoteSigned`. |
| `pip` is very slow | Wait — or use `python -m pip install -r requirements.txt`. |
| *“Could not reach Ollama at …”* | Ollama is not running — run `ollama serve` (or start the Ollama app), check the port in `OLLAMA_URL`. |
| *“The model … is not downloaded”* | Run `ollama pull qwen2.5:3b` once, or pick a model from `ollama list` in Settings. |
| *“Ollama took too long to answer”* | The model is still loading — wait a moment and retry, or use a smaller model. |
| *“not valid JSON”* / unreadable answer | Try again; a small model occasionally fails — `qwen2.5:3b` is the tested default. |
| *“No readable text was found in this PDF”* | The PDF is scanned (image only) — paste the text instead. |
| *“Audio transcription needs … faster-whisper”* | Run `pip install -r requirements-audio.txt` once and restart the app. |
| First transcription is slow | The model downloads once (≈145 MB for `base`) and loads on first use — pick `tiny` on the Settings page for speed. |
| *“No speech could be detected”* | The file has no recognisable speech — check it plays audio (wav/mp3 work best). |
| *“The Ask feature needs a local Ollama model”* | Set `OLLAMA_MODEL` on the Settings page. |
| Port 5000 already in use | An older copy of the app is still running — close its window (Ctrl+C) first; or edit the last line of `app.py`: `port=5001`. |
| Want a fresh start | Stop the app and delete `meeting_app.db` (it is recreated automatically). |

---

## 10. Ideas to extend it

Already built: local audio transcription, per-meeting chat, cross-meeting Ask,
saved conversations, keyword retrieval, optional speech-to-text model choice.

Next ideas:

* Add user accounts (Flask-Login) so each person has their own meetings.
* Add a “send by email” button with SMTP (keep it opt-in, like the current draft).
* Export the report directly to PDF with the `reportlab` library.
* Add a calendar view of upcoming deadlines (`.ics` export).
* Show answers **streaming** live while the model writes them.
* Better retrieval (embeddings / BM25) for the Ask page as the number of
  meetings grows.

---

**Note:** this app prepares the follow-up email but **never sends anything by
itself** — you review, edit, copy or download it and send it from your own mail
program.
=======
# MeetMind-AI
AI-powered making summarization and action item assistant.
>>>>>>> a0e3a547c91582628d35588a62d144925b339a85
