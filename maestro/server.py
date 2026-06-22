"""A tiny zero-dependency web 'control room' for `race`.

`maestro serve` starts a local HTTP server (standard library only) that lets you
launch a parallel race from the browser and watch each model work in real time —
status, attempts, tokens and cost updating live, with the winner highlighted.
"""

from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .race import pick_winner, race

# run_id -> live state dict (read by the browser via /api/status)
_RUNS: dict = {}


def _run_race(rid: str, payload: dict, models: list) -> None:
    state = _RUNS[rid]

    def on_result(r) -> None:
        status = "pass" if r.passed else ("err" if r.error else "fail")
        state["racers"][r.spec] = {
            "spec": r.spec,
            "model": r.model,
            "status": status,
            "passed": r.passed,
            "attempts": r.attempts,
            "tokens": r.tokens,
            "cost": round(r.cost, 5),
            "note": (r.error or r.summary or "")[:140],
            "workdir": r.workdir,
        }

    try:
        results = race(
            models,
            payload["repo"],
            payload["task"],
            payload["check"],
            int(payload.get("max_attempts", 3) or 3),
            logger=None,
            on_result=on_result,
        )
        winner = pick_winner(results)
        state["winner"] = winner.spec if winner else None
    except Exception as exc:  # surface setup errors to the UI
        state["error"] = str(exc)[:300]
    finally:
        state["done"] = True


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/":
            self._send(200, _PAGE, "text/html; charset=utf-8")
        elif route.path == "/api/status":
            rid = (parse_qs(route.query).get("id") or [""])[0]
            self._send(200, json.dumps(_RUNS.get(rid, {"error": "unknown id"})))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if urlparse(self.path).path != "/api/race":
            self._send(404, json.dumps({"error": "not found"}))
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, json.dumps({"error": "bad json"}))
            return

        models = [m.strip() for m in str(payload.get("models", "")).split(",") if m.strip()]
        if not models or not payload.get("repo") or not payload.get("check"):
            self._send(400, json.dumps({"error": "need repo, check and at least one model"}))
            return

        rid = uuid.uuid4().hex[:8]
        _RUNS[rid] = {
            "id": rid,
            "done": False,
            "winner": None,
            "models": models,
            "racers": {
                m: {"spec": m, "model": m, "status": "running", "attempts": 0,
                    "tokens": 0, "cost": 0.0, "note": ""}
                for m in models
            },
        }
        threading.Thread(target=_run_race, args=(rid, payload, models), daemon=True).start()
        self._send(200, json.dumps({"id": rid}))


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"\n  Maestro control room running at  {url}")
    print("  (Ctrl+C to stop)\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
        httpd.shutdown()


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maestro — Control Room</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#272e3a; --txt:#e6edf3; --mut:#8b949e;
          --accent:#7c5cff; --pass:#2ea043; --fail:#d29922; --err:#6e7681; --run:#388bfd; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:18px 24px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; letter-spacing:.5px; }
  header h1 span { color:var(--accent); }
  header p { margin:4px 0 0; color:var(--mut); font-size:12px; }
  main { display:grid; grid-template-columns:340px 1fr; gap:0; min-height:calc(100vh - 70px); }
  form { padding:20px 24px; border-right:1px solid var(--line); }
  label { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.6px;
          color:var(--mut); margin:14px 0 5px; }
  input, textarea { width:100%; background:#0b0f14; color:var(--txt);
          border:1px solid var(--line); border-radius:7px; padding:8px 10px; font:inherit; }
  textarea { resize:vertical; min-height:60px; }
  button { margin-top:18px; width:100%; background:var(--accent); color:#fff; border:0;
           border-radius:7px; padding:11px; font:inherit; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  section { padding:20px 24px; }
  .winner { background:linear-gradient(90deg,#15301c,#161b22); border:1px solid var(--pass);
            border-radius:9px; padding:12px 16px; margin-bottom:16px; display:none; }
  .winner b { color:var(--pass); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:14px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .card .top { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .name { font-weight:600; word-break:break-all; }
  .badge { font-size:11px; padding:3px 9px; border-radius:20px; white-space:nowrap; }
  .b-running { background:rgba(56,139,253,.15); color:var(--run); }
  .b-pass { background:rgba(46,160,67,.15); color:var(--pass); }
  .b-fail { background:rgba(210,153,34,.15); color:var(--fail); }
  .b-err { background:rgba(110,118,129,.2); color:var(--err); }
  .dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:6px; }
  .b-running .dot { background:var(--run); animation:pulse 1s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.25} }
  .stats { display:flex; gap:18px; margin-top:12px; color:var(--mut); font-size:12px; }
  .stats b { color:var(--txt); font-weight:600; }
  .note { margin-top:10px; color:var(--mut); font-size:12px; min-height:16px; }
  .empty { color:var(--mut); }
</style>
</head>
<body>
<header>
  <h1>🎼 Maestro <span>Control Room</span></h1>
  <p>Launch several models in parallel on the same task — cheapest one that passes wins.</p>
</header>
<main>
  <form id="f">
    <label>Task</label>
    <textarea id="task">Fix the failing tests by correcting the bug in the source.</textarea>
    <label>Repo (absolute path)</label>
    <input id="repo" placeholder="C:\path\to\project">
    <label>Check command (exit 0 = done)</label>
    <input id="check" value="python -m pytest -q">
    <label>Models (comma-separated)</label>
    <input id="models" value="ollama:gpt-oss:120b-cloud">
    <label>Max attempts</label>
    <input id="max" type="number" value="3" min="1" max="8">
    <button id="go" type="submit">▶ Lancer la course</button>
  </form>
  <section>
    <div class="winner" id="winner"></div>
    <div class="grid" id="grid"><p class="empty">No race yet. Configure on the left and launch.</p></div>
  </section>
</main>
<script>
  const $ = id => document.getElementById(id);
  let timer = null;

  $("f").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("go").disabled = true;
    $("winner").style.display = "none";
    const body = {
      task: $("task").value, repo: $("repo").value, check: $("check").value,
      models: $("models").value, max_attempts: parseInt($("max").value || "3"),
    };
    const r = await fetch("/api/race", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
    const j = await r.json();
    if (j.error) { alert(j.error); $("go").disabled = false; return; }
    if (timer) clearInterval(timer);
    timer = setInterval(() => poll(j.id), 900);
    poll(j.id);
  });

  async function poll(id) {
    const r = await fetch("/api/status?id=" + id);
    const s = await r.json();
    render(s);
    if (s.done) { clearInterval(timer); $("go").disabled = false; }
  }

  function render(s) {
    const racers = Object.values(s.racers || {});
    $("grid").innerHTML = racers.map(card).join("") || '<p class="empty">starting…</p>';
    const w = $("winner");
    if (s.done && s.winner) {
      const win = s.racers[s.winner];
      w.style.display = "block";
      w.innerHTML = `🏆 <b>WINNER:</b> ${esc(win.model)} — cheapest passing, $${(win.cost||0).toFixed(4)}`;
    } else if (s.done && s.error) {
      w.style.display = "block"; w.innerHTML = "⚠ " + esc(s.error);
    } else if (s.done) {
      w.style.display = "block"; w.innerHTML = "No model passed the check.";
    }
  }

  function card(c) {
    const map = {running:"b-running",pass:"b-pass",fail:"b-fail",err:"b-err"};
    const label = {running:"running",pass:"PASS",fail:"failed",err:"error"};
    return `<div class="card">
      <div class="top"><span class="name">${esc(c.model||c.spec)}</span>
        <span class="badge ${map[c.status]||'b-err'}"><span class="dot"></span>${label[c.status]||c.status}</span></div>
      <div class="stats"><span>attempts <b>${c.attempts||0}</b></span>
        <span>tokens <b>${c.tokens||0}</b></span><span>cost <b>$${(c.cost||0).toFixed(4)}</b></span></div>
      <div class="note">${esc(c.note||"")}</div>
    </div>`;
  }
  const esc = s => (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
</script>
</body>
</html>
"""
