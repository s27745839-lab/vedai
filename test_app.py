"""Self-check: python test_app.py (no API key needed; Claude is faked)."""
import json, tempfile, threading, urllib.request, urllib.error
from pathlib import Path
import app

app.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"
app.CON = app.db()
app.ask_ai = lambda history: iter(["Hello ", f"({len(history)} msgs)"])
srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"


def call(path, method="GET", data=None, who="browser-aaaa"):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json", "X-Client-Id": who})
    try:
        r = urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:  # 4xx still has a JSON body
        r = e
    with r:
        body = r.read().decode()
        return json.loads(body) if r.headers["Content-Type"].startswith("application/json") else body


cid = call("/api/chats", "POST")["id"]
assert call(f"/api/chats/{cid}/messages", "POST", {"content": "What is DCRA?"}) == "Hello (1 msgs)"
assert call(f"/api/chats/{cid}/messages", "POST", {"content": "Thanks"}) == "Hello (3 msgs)"
assert [m["role"] for m in call(f"/api/chats/{cid}")] == ["user", "assistant", "user", "assistant"]
assert call("/api/chats")[0]["title"] == "What is DCRA?"
assert "VedAI" in call("/")
# another browser can't see, read or delete this chat
assert call("/api/chats", who="browser-bbbb") == []
assert call(f"/api/chats/{cid}", who="browser-bbbb") == {"error": "not found"}
call(f"/api/chats/{cid}", "DELETE", who="browser-bbbb")
assert len(call(f"/api/chats/{cid}")) == 4
call(f"/api/chats/{cid}", "DELETE")
assert call(f"/api/chats/{cid}") == {"error": "not found"}
assert app.q("SELECT COUNT(*) FROM messages").fetchone()[0] == 0  # cascade removed messages
assert call("/api/chats", "POST", who="") == {"error": "missing client id"}
assert "Vedang" in app.system_prompt()
print("all tests passed")
