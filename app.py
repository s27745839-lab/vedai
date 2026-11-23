"""VedAI — chat assistant for Vedang Cellular Services.
Run: python app.py   then open http://localhost:8000
Needs GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)."""
import json, os, re, sqlite3, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).parent
DB_PATH = ROOT / "vedai.db"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
_lock = threading.Lock()


def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY, owner TEXT, title TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, chat_id INTEGER REFERENCES chats(id) ON DELETE CASCADE,
            role TEXT, content TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);""")
    con.execute("PRAGMA foreign_keys=ON")
    return con


CON = db()


def q(sql, *args):
    with _lock:
        cur = CON.execute(sql, args)
        CON.commit()
        return cur


def system_prompt():
    return ("You are VedAI, the AI assistant of Vedang Cellular Services (a Bluspring enterprise). "
            "Answer questions about the company, its services, telecom infrastructure, DCRA, careers and contact. "
            "Use the knowledge below as the source of truth; if something is not in it, say you don't know and point to "
            "https://www.vedangcellular.com/contact/. Never invent numbers, prices or people. Be concise and friendly.\n\n"
            + (ROOT / "knowledge.md").read_text(encoding="utf-8"))


def ask_ai(history):
    """Yields text chunks. Replaced in tests."""
    client = genai.Client()  # reads GEMINI_API_KEY
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in history]
    for chunk in client.models.generate_content_stream(
        model=MODEL, contents=contents,
        config=types.GenerateContentConfig(system_instruction=system_prompt()),
    ):
        if chunk.text:
            yield chunk.text


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def owner(self):
        """Random per-browser id from the page. shortcut: not real auth (no login), add accounts before storing private data."""
        o = self.headers.get("X-Client-Id", "")
        return o if re.fullmatch(r"[\w-]{8,64}", o) else None

    def own_chat(self, cid):
        return q("SELECT 1 FROM chats WHERE id=? AND owner=?", cid, self.owner()).fetchone()

    def do_GET(self):
        if self.path == "/":
            data = (ROOT / "static" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if self.path == "/api/chats":
            rows = q("SELECT id, title FROM chats WHERE owner=? ORDER BY id DESC", self.owner())
            return self.send_json([dict(r) for r in rows])
        if (m := re.fullmatch(r"/api/chats/(\d+)", self.path)) and self.own_chat(int(m[1])):
            rows = q("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id", int(m[1]))
            return self.send_json([dict(r) for r in rows])
        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if m := re.fullmatch(r"/api/chats/(\d+)", self.path):
            q("DELETE FROM chats WHERE id=? AND owner=?", int(m[1]), self.owner())
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.owner():
            return self.send_json({"error": "missing client id"}, 400)
        if self.path == "/api/chats":
            cid = q("INSERT INTO chats(owner, title) VALUES(?, 'New chat')", self.owner()).lastrowid
            return self.send_json({"id": cid, "title": "New chat"})
        m = re.fullmatch(r"/api/chats/(\d+)/messages", self.path)
        if not m:
            return self.send_json({"error": "not found"}, 404)
        cid = int(m[1])
        if not self.own_chat(cid):
            return self.send_json({"error": "chat not found"}, 404)
        text = str(self.body().get("content", "")).strip()[:8000]
        if not text:
            return self.send_json({"error": "empty message"}, 400)
        q("INSERT INTO messages(chat_id, role, content) VALUES(?,?,?)", cid, "user", text)
        q("UPDATE chats SET title=? WHERE id=? AND title='New chat'", text[:40], cid)
        history = [dict(r) for r in q("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id", cid)]

        # Stream plain text; connection close marks the end.
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        reply = ""
        try:
            for chunk in ask_ai(history):
                reply += chunk
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # user pressed Stop / closed the tab: keep the partial answer
        except Exception as e:  # API errors, missing/invalid key, network
            print("ask_ai failed:", repr(e))
            err = f"\n\n⚠️ AI service error: {e.__class__.__name__}. Check the server log / API key."
            reply += err
            self.wfile.write(err.encode())
        finally:
            if reply:  # keep partial answers so history stays consistent
                q("INSERT INTO messages(chat_id, role, content) VALUES(?,?,?)", cid, "assistant", reply)


def serve():
    port = int(os.environ.get("PORT", 8000))  # Render sets PORT
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"VedAI running at http://localhost:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    serve()
