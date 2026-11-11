"""Demo only: a tiny OpenAI-compatible endpoint that plays a fixed script, so the
onbrief demo runs the real Hermes agent loop without an API key.

Turn 1: post a draft that breaks the campaign rules.
Turn 2: after the tool result, re-post using onbrief's suggested text (or the
         FIXED text below when no suggestion is offered).
Turn 3: reply with a short summary.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

DRAFT = sys.argv[2] if len(sys.argv) > 2 else "draft"
FIXED = sys.argv[3] if len(sys.argv) > 3 else "fixed"
LOG = sys.argv[4] if len(sys.argv) > 4 else ""


def _suggested(tool_text: str) -> str | None:
    marker = "suggested text that passes every rule:"
    if marker not in tool_text:
        return None
    lines = tool_text.split(marker, 1)[1].splitlines()[1:]
    keep = []
    for ln in lines:
        if ln and not ln.startswith("    "):
            break
        keep.append(ln[4:])
    return "\n".join(keep).strip() or None


def plan(messages):
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    if not tool_msgs:
        return ("tool", DRAFT)
    last = tool_msgs[-1].get("content") or ""
    if isinstance(last, list):
        last = " ".join(p.get("text", "") for p in last if isinstance(p, dict))
    if "onbrief" in last and "blocked" in last:
        return ("tool", _suggested(last) or FIXED)
    if '"posted": true' in last:
        reasons = []
        for m in tool_msgs:
            body = json.loads(m.get("content") or "{}").get("error", "") if (m.get("content") or "").startswith("{") else ""
            reasons += [ln.strip()[2:] for ln in body.splitlines() if ln.strip().startswith("x ")]
        if reasons:
            return ("text", "onbrief blocked my first draft:\n" + "\n".join("- " + r for r in reasons)
                    + "\nI rewrote it and the fixed post went out.")
        return ("text", "posted.")
    return ("text", "the post did not go out: " + last[:200])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"object": "list", "data": [{"id": "scripted", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if LOG:
            with open(LOG, "a") as fh:
                fh.write(json.dumps({"tools": [t.get("function", {}).get("name") for t in req.get("tools") or []],
                                     "messages": req.get("messages", [])[1:]}) + "\n")
        kind, payload = plan(req.get("messages", []))
        created = int(time.time())
        if kind == "tool":
            call = {"index": 0, "id": f"call_{created}_{len(req.get('messages', []))}", "type": "function",
                    "function": {"name": "outbox_create_post", "arguments": json.dumps({"text": payload})}}
            msg = {"role": "assistant", "content": None, "tool_calls": [call]}
            finish = "tool_calls"
        else:
            msg = {"role": "assistant", "content": payload}
            finish = "stop"
        usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = dict(msg)
            chunks = [
                {"id": "c1", "object": "chat.completion.chunk", "created": created, "model": "scripted",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "created": created, "model": "scripted",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
            ]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        if kind == "tool":
            for c in msg["tool_calls"]:
                c.pop("index", None)
        body = json.dumps({"id": "c1", "object": "chat.completion", "created": created, "model": "scripted",
                           "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                           "usage": usage}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
