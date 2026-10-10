"""
mock_ollama.py
==============
A tiny fake Ollama server, used to test the app WITHOUT needing Ollama.

It speaks the two endpoints the project uses:

    GET  /api/tags      -> list of "downloaded" models
    POST /api/chat      -> {"message": {"content": "..."}}

The reply is built from the transcript it receives (same honesty rules as
the real app: owners/deadlines only when they appear in the text), so the
results page, report and CSV all render realistic data.

Standalone (to run the whole app against it):

    python mock_ollama.py --port 11435
    # then in another terminal:
    $env:OLLAMA_URL = "http://127.0.0.1:11435/api/chat"
    python app.py

In tests:

    from mock_ollama import MockOllama
    mock = MockOllama(mode="good").start()
    ...
    mock.stop()

Modes (selftest.py uses them to check the error handling):
    good | plain | fenced | badjson | empty | missing_summary | notjsonbody
    | http404 | http500 | no_deadlines
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_BULLET = re.compile(r"^\s*(?:[-*•]\s+|\d{1,2}[.)]\s+)")
_OWNER_TASK = re.compile(
    r"^(?P<owner>[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+)?)\s+"
    r"(?:will|to|owns|needs to)\s+(?P<task>.+)$")
_DEADLINE = re.compile(
    r"\b(?:by|before|due(?:\s+by)?|until|no later than)\s+(?P<dl>[^.;|\n]+)", re.I)
_DECIDED = re.compile(
    r"\b(agreed|decided|approved|confirmed|finalized|resolved)\b", re.I)
_SUGGESTED = re.compile(
    r"\b(suggested|proposed|discussed|consider|noted)\b", re.I)


def _analyze(transcript: str) -> dict:
    """Build the documented JSON answer from the transcript (never invents)."""
    lines = [ln.strip() for ln in transcript.splitlines() if ln.strip()]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", transcript) if p.strip()]

    title = lines[0][:110] if lines else "Meeting"

    # summary - first paragraph, always long enough to pass validation
    summary = paragraphs[0] if paragraphs else transcript
    if len(summary) > 450:
        cut = summary[:450]
        summary = cut.rsplit(".", 1)[0] + "." if "." in cut else cut + "..."
    if len(summary) < 20:
        summary = " ".join(lines[:3])[:450]
    if len(summary) < 20:
        summary = (summary + " See the meeting notes for the details.").strip()

    # key points - bullets / numbered items
    key_points: list[str] = []
    for line in lines:
        plain = _BULLET.sub("", line)
        if plain != line and 8 <= len(plain) <= 300:
            key_points.append(plain)
        if len(key_points) >= 8:
            break
    if not key_points:
        key_points = [p[:300] for p in paragraphs[:3]]

    # decisions - only sentences that clearly decided / suggested something
    decisions: list[dict] = []
    for line in lines:
        plain = _BULLET.sub("", line)
        if not 8 <= len(plain) <= 400:
            continue
        if _DECIDED.search(plain):
            decisions.append({"decision": plain, "status": "confirmed"})
        elif _SUGGESTED.search(plain):
            decisions.append({"decision": plain, "status": "suggested"})
        if len(decisions) >= 8:
            break

    # action items - "Name will ..." lines (bullet or not)
    action_items: list[dict] = []
    for line in lines:
        plain = _BULLET.sub("", line)
        match = _OWNER_TASK.match(plain)
        if not match and not _BULLET.match(line):
            continue
        owner = match.group("owner") if match else None
        deadline = None
        dl = _DEADLINE.search(plain)
        if dl:
            deadline = dl.group("dl").strip()
            if "no deadline" in deadline.lower() or deadline.lower().startswith("deadline"):
                deadline = None                   # "(no deadline given)"
        item = {"task": plain[:500], "owner": owner, "deadline": deadline,
                "status": "Not started"}
        if not any(existing["task"] == item["task"] for existing in action_items):
            action_items.append(item)
        if len(action_items) >= 12:
            break

    # follow-up email
    subject = f"Follow-up: {title[:80]}" if title else "Meeting follow-up"
    body = ["Hi all,", "", "Thank you for the meeting.", "", summary]
    if decisions:
        body += ["", "Decisions:"]
        body += [f"- {d['decision']} ({d['status']})" for d in decisions[:6]]
    if action_items:
        body += ["", "Action items:"]
        for item in action_items[:8]:
            body.append(f"- {item['task']} (Owner: {item['owner'] or 'Not specified'}"
                        f" | Due: {item['deadline'] or 'no deadline given'})")
    body += ["", "Please let me know if anything is missing.", "", "[Your name]"]

    return {
        "title": title,
        "meeting_summary": summary,
        "key_points": key_points,
        "decisions": decisions,
        "action_items": action_items,
        "follow_up_email": {"subject": subject, "body": "\n".join(body)},
    }


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded server that stays silent when a client drops the connection
    (the timeout test aborts the request on purpose)."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class MockOllama:
    """An in-process fake Ollama server (random free port)."""

    def __init__(self, model: str = "qwen2.5:3b", mode: str = "good",
                 delay: float = 0.0, tags_models=None):
        self.model = model
        self.mode = mode
        self.delay = delay                    # seconds to stall each answer
        self.tags_models = list(tags_models) if tags_models is not None else [model]
        self.requests: list[dict] = []        # every /api/chat payload seen
        self.port: int | None = None
        self._server = None
        self._thread = None

    # -- urls ---------------------------------------------------------------
    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def chat_url(self) -> str:
        return f"{self.server_url}/api/chat"

    @property
    def last_request(self) -> dict:
        return self.requests[-1] if self.requests else {}

    @property
    def transcript(self) -> str:
        """The meeting transcript from the last /api/chat request."""
        for payload in reversed(self.requests):
            for message in payload.get("messages", []):
                if message.get("role") == "user":
                    text = str(message.get("content") or "")
                    return text.split("Meeting transcript:\n", 1)[-1]
        return ""

    # -- lifecycle ----------------------------------------------------------
    def start(self, port: int = 0):
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):          # keep test output clean
                pass

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionAbortedError, BrokenPipeError, OSError):
                    pass                        # client hung up (timeout test)

            def do_GET(self):
                if self.path.startswith("/api/tags"):
                    self._send_json(200, {"models": [
                        {"name": name, "model": name} for name in state.tags_models]})
                else:
                    self._send_json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    payload = {}
                state.requests.append(payload)
                if state.delay:
                    time.sleep(state.delay)        # slow model (timeout tests)
                self._respond()

            def _respond(self):
                mode = state.mode
                if mode == "http404":
                    self._send_json(404, {"error": f"model '{state.model}' "
                                                   "not found, try pulling it first"})
                    return
                if mode == "http500":
                    self._send_json(500, {"error": "internal server error (mock)"})
                    return
                if mode == "notjsonbody":
                    body = b"<html>this is not json</html>"
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except (ConnectionAbortedError, BrokenPipeError, OSError):
                        pass
                    return
                self._send_json(200, {
                    "model": state.model,
                    "created_at": "2026-01-01T00:00:00.000Z",
                    "message": {"role": "assistant", "content": state._content()},
                    "done": True,
                    "done_reason": "stop",
                })

        self._server = _QuietThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- the model's "answer" ------------------------------------------------
    def payload(self) -> dict:
        return _analyze(self.transcript)

    def _content(self) -> str:
        if self.mode == "badjson":
            return '{"meeting_summary": "truncated'
        if self.mode == "empty":
            return ""
        if self.mode == "fenced":
            return "Here you go:\n```json\n" + json.dumps(self.payload(),
                                                          ensure_ascii=False) + "\n```"
        data = self.payload()
        if self.mode == "plain":
            return "Alice promised to send the release notes by next Monday."
        if self.mode == "missing_summary":
            data.pop("meeting_summary", None)
        if self.mode == "no_deadlines":
            # imitate a small model that buries the deadline in the task
            for item in data.get("action_items", []):
                if item.get("deadline"):
                    item["task"] = f"{item['task']} by {item['deadline']}"
                    item["deadline"] = None
        return json.dumps(data, ensure_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fake Ollama server for testing the app without Ollama.")
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--model", default="qwen2.5:3b")
    args = parser.parse_args()

    server = MockOllama(model=args.model).start(port=args.port)
    print(f"Mock Ollama running at {server.server_url} (model: {args.model})")
    print(f"Point the app at it with:  $env:OLLAMA_URL = \"{server.chat_url}\"")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("\nMock Ollama stopped.")
