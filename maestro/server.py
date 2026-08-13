"""Zero-dependency web 'control room' for Maestro.

`maestro serve` opens a local dashboard with two modes:
  - Manuel : you write a task + check; several agents race in parallel.
  - Auto   : you write a GOAL; an orchestrator model decomposes it into checkable
             sub-tasks and delegates each to the free agents automatically.
Plus a filesystem folder picker, per-racer Stop, and live watchdog reasons.
"""

from __future__ import annotations

import json
import os
import string
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import store
from .race import delegate

_RUNS: dict = {}
_STOPS: dict = {}


def _list_dir(path: str) -> dict:
    p = Path(path) if path else Path.home()
    try:
        p = p.resolve()
    except Exception:
        p = Path.home()
    drives = []
    if os.name == "nt":
        try:
            drives = list(os.listdrives())
        except Exception:
            drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    try:
        dirs = sorted((c.name for c in p.iterdir() if c.is_dir() and not c.name.startswith(".")),
                      key=str.lower)
    except Exception as exc:
        return {"path": str(p), "parent": str(p.parent), "dirs": [], "drives": drives, "error": str(exc)}
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "dirs": dirs, "drives": drives}


def _run_race(rid: str, payload: dict, models: list) -> None:
    state = _RUNS[rid]
    stops = _STOPS[rid]

    def cancel(spec: str) -> bool:
        ev = stops.get(spec)
        return bool(ev and ev.is_set())

    def on_event(spec: str, info: dict) -> None:
        racer = state["racers"].get(spec)
        if racer:
            racer.update(info)

    def on_result(r) -> None:
        state["racers"][r.spec] = {
            "spec": r.spec, "model": r.model, "status": "done",
            "reason": r.reason or ("passed" if r.passed else "failed"),
            "passed": r.passed, "attempts": r.attempts, "tokens": r.tokens,
            "cost": round(r.cost, 5), "note": (r.error or r.summary or "")[:160],
            "workdir": r.workdir, "tier": r.tier,
        }

    try:
        # Manuel mode goes through delegate() so it gets the baseline check, the
        # cache, the safe apply and the undo snapshot — same engine as the CLI.
        res = delegate(
            models, payload["repo"], payload["task"], payload["check"],
            int(payload.get("max_attempts", 3) or 3),
            apply=bool(payload.get("apply", True)),
            mode=payload.get("strategy") or "race",
            use_cache=bool(payload.get("cache", True)),
            on_result=on_result, cancel=cancel, on_event=on_event,
        )
        state.update({
            "winner": res["winner"], "applied_files": res["applied_files"],
            "undo": res.get("undo"), "skipped": res.get("skipped"),
            "detail": res.get("detail", ""), "cached": res.get("cached", False),
            "warning": res.get("warning", ""), "unused_tiers": res.get("unused_tiers", []),
            "tokens": res.get("tokens", 0), "ok": res["ok"],
        })
        # Baseline/cache can end the job before any racer starts; without this
        # their cards would spin on "running" forever next to a finished banner.
        if res.get("skipped") or res.get("cached"):
            for racer in state["racers"].values():
                if racer.get("status") != "done":
                    racer.update({"status": "done", "reason": "skipped",
                                  "note": res.get("detail", "")})
    except (Exception, SystemExit) as exc:
        state["error"] = str(exc)[:300]
    finally:
        state["done"] = True
        store.record_run(state)


def _run_auto(rid: str, payload: dict, models: list) -> None:
    state = _RUNS[rid]

    def on_plan(steps):
        state["steps"] = [
            {"title": s["title"], "task": s["task"], "check": s["check"],
             "status": "pending", "winner": None, "applied_files": [], "results": []}
            for s in steps
        ]

    def on_step(i, info):
        if 0 <= i < len(state.get("steps", [])):
            state["steps"][i].update(info)

    try:
        from .auto import auto_run

        res = auto_run(
            payload["goal"], payload["repo"],
            payload.get("orchestrator") or "ollama:gpt-oss:120b-cloud",
            models, int(payload.get("max_attempts", 2) or 2),
            on_plan=on_plan, on_step=on_step,
            mode=payload.get("strategy") or "race",
            use_cache=bool(payload.get("cache", True)),
        )
        state["ok"] = res["ok"]
        state["tokens"] = res.get("tokens", 0)
        state["free_steps"] = res.get("free_steps", 0)
    except (Exception, SystemExit) as exc:
        state["error"] = str(exc)[:300]
    finally:
        state["done"] = True
        store.record_run(state)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
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
        q = parse_qs(route.query)
        if route.path == "/":
            # The catalogue is static metadata, so the model list can be drawn
            # immediately; /api/doctor then annotates it with what is actually
            # installed. Waiting on a probe to show the list at all is how a
            # user ends up believing a provider isn't offered.
            from . import registry

            catalog = json.dumps([
                {"kind": b.kind, "label": b.label, "tier": b.tier, "spec": b.spec,
                 "can_plan": b.can_plan, "available": None, "detail": "vérification…",
                 "warn": ""}
                for b in registry.BACKENDS.values()
            ])
            self._send(200, _PAGE.replace("/*CATALOG*/[]", catalog),
                       "text/html; charset=utf-8")
        elif route.path == "/api/ls":
            self._send(200, json.dumps(_list_dir((q.get("path") or [""])[0])))
        elif route.path == "/api/status":
            rid = (q.get("id") or [""])[0]
            self._send(200, json.dumps(_RUNS.get(rid) or store.get_run(rid)
                                       or {"error": "unknown id"}))
        elif route.path == "/api/doctor":
            from .preflight import doctor

            self._send(200, json.dumps({"backends": doctor(), "disk": store.disk_usage()}))
        elif route.path == "/api/runs":
            self._send(200, json.dumps({"runs": store.list_runs(30)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        route = urlparse(self.path)
        q = parse_qs(route.query)

        if route.path == "/api/stop":
            rid = (q.get("id") or [""])[0]
            model = (q.get("model") or [""])[0]
            stops = _STOPS.get(rid, {})
            targets = [model] if model else list(stops.keys())
            for spec in targets:
                if spec in stops:
                    stops[spec].set()
            self._send(200, json.dumps({"ok": True, "stopped": targets}))
            return

        if route.path == "/api/undo":
            sid = (q.get("sid") or [""])[0]
            self._send(200, json.dumps(store.restore(sid or None)))
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            self._send(400, json.dumps({"error": "bad json"}))
            return
        models = [m.strip() for m in str(payload.get("models", "")).split(",") if m.strip()]
        repo = str(payload.get("repo", "")).strip()

        if route.path == "/api/race":
            if not models or not repo or not str(payload.get("check", "")).strip():
                self._send(400, json.dumps({"error": "need a folder, a check and at least one model"}))
                return
            if not Path(repo).is_dir():
                self._send(400, json.dumps({"error": f"not a folder: {repo}"}))
                return
            rid = uuid.uuid4().hex[:8]
            _STOPS[rid] = {m: threading.Event() for m in models}
            _RUNS[rid] = {"id": rid, "mode": "race", "done": False, "winner": None,
                          "models": models, "task": payload.get("task", ""),
                          "strategy": payload.get("strategy") or "race",
                          "applied_files": [], "undo": None,
                          "racers": {m: {"spec": m, "model": m, "status": "running", "reason": "",
                                         "attempts": 0, "tokens": 0, "cost": 0.0, "note": ""} for m in models}}
            threading.Thread(target=_run_race, args=(rid, payload, models), daemon=True).start()
            self._send(200, json.dumps({"id": rid}))
            return

        if route.path == "/api/auto":
            if not models or not repo or not str(payload.get("goal", "")).strip():
                self._send(400, json.dumps({"error": "need a folder, a goal and at least one model"}))
                return
            if not Path(repo).is_dir():
                self._send(400, json.dumps({"error": f"not a folder: {repo}"}))
                return
            rid = uuid.uuid4().hex[:8]
            _RUNS[rid] = {"id": rid, "mode": "auto", "done": False, "ok": False,
                          "goal": payload["goal"], "steps": [],
                          "strategy": payload.get("strategy") or "race"}
            threading.Thread(target=_run_auto, args=(rid, payload, models), daemon=True).start()
            self._send(200, json.dumps({"id": rid}))
            return

        self._send(404, json.dumps({"error": "not found"}))


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
          --accent:#7c5cff; --pass:#2ea043; --fail:#d29922; --warn:#db6d28; --err:#f85149;
          --run:#388bfd; --stop:#6e7681; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:16px 24px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; } header h1 span { color:var(--accent); }
  header p { margin:4px 0 0; color:var(--mut); font-size:12px; }
  main { display:grid; grid-template-columns:380px 1fr; min-height:calc(100vh - 66px); }
  form { padding:18px 22px; border-right:1px solid var(--line); }
  label { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.6px;
          color:var(--mut); margin:14px 0 5px; }
  input, textarea { width:100%; background:#0b0f14; color:var(--txt);
          border:1px solid var(--line); border-radius:7px; padding:8px 10px; font:inherit; }
  textarea { resize:vertical; min-height:56px; }
  .row { display:flex; gap:8px; } .row input { flex:1; }
  button { background:var(--accent); color:#fff; border:0; border-radius:7px;
           padding:9px 12px; font:inherit; font-weight:600; cursor:pointer; }
  button.ghost { background:#21262d; color:var(--txt); border:1px solid var(--line); }
  button:disabled { opacity:.5; cursor:default; }
  #go { width:100%; margin-top:18px; padding:11px; }
  .modes { display:flex; gap:8px; margin-bottom:6px; }
  .modebtn { flex:1; background:#21262d; color:var(--mut); border:1px solid var(--line); }
  .modebtn.active { background:var(--accent); color:#fff; border-color:var(--accent); }
  section { padding:18px 22px; }
  .winner { background:linear-gradient(90deg,#15301c,#161b22); border:1px solid var(--pass);
            border-radius:9px; padding:12px 16px; margin-bottom:16px; display:none; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .card .top { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .name { font-weight:600; word-break:break-all; }
  .badge { font-size:11px; padding:3px 9px; border-radius:20px; white-space:nowrap; }
  .b-running{background:rgba(56,139,253,.15);color:var(--run)}
  .b-pass{background:rgba(46,160,67,.16);color:var(--pass)}
  .b-fail{background:rgba(210,153,34,.16);color:var(--fail)}
  .b-warn{background:rgba(219,109,40,.18);color:var(--warn)}
  .b-err{background:rgba(248,81,73,.16);color:var(--err)}
  .b-stop{background:rgba(110,118,129,.22);color:var(--stop)}
  .b-pending{background:rgba(110,118,129,.18);color:var(--mut)}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;background:currentColor}
  .b-running .dot{animation:pulse 1s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
  .stats{display:flex;gap:16px;margin-top:12px;color:var(--mut);font-size:12px}
  .stats b{color:var(--txt)}
  .note{margin-top:9px;color:var(--mut);font-size:12px;min-height:15px;word-break:break-word}
  .hint{margin:6px 0 0;color:var(--mut);font-size:11px;line-height:1.5}
  .hint code{background:#0b0f14;border:1px solid var(--line);border-radius:4px;padding:1px 4px;color:var(--txt)}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .chip{background:#21262d;color:var(--txt);border:1px solid var(--line);border-radius:20px;
        padding:4px 10px;font-size:11px;cursor:pointer;user-select:none}
  .chip:hover{border-color:var(--accent)}
  .chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  .chip.off{opacity:.45}
  .chip .tick{font-size:10px;margin-right:4px}
  .tierhead{width:100%;margin:8px 0 2px;color:var(--mut);font-size:10px;
            text-transform:uppercase;letter-spacing:.7px}
  .tierhead b{color:var(--txt);font-weight:600}
  .check{display:flex;align-items:center;gap:7px;margin-top:10px;color:var(--mut);font-size:12px}
  .check input{width:auto}
  .undo{background:#3d2a14;color:#ffcf8f;border:1px solid #6b4a1d;font-size:12px;
        padding:6px 11px;margin-top:10px}
  .savebar{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
           border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:12px;color:var(--mut)}
  .savebar b{color:var(--txt)}
  .free{background:linear-gradient(90deg,#13251c,#161b22);border:1px solid var(--pass)}
  .b-queued{background:rgba(110,118,129,.18);color:var(--mut)}
  .b-skip{background:rgba(46,160,67,.10);color:var(--mut)}
  .b-cache{background:rgba(124,92,255,.18);color:#b9a6ff}
  .stopbtn{margin-top:10px;background:#3d1418;color:#ff9a90;border:1px solid #5b1a1f;font-size:12px;padding:5px 10px}
  .empty{color:var(--mut)}
  #fb{display:none;border:1px solid var(--line);border-radius:8px;margin-top:6px;background:#0b0f14}
  #fbpath{padding:8px 10px;border-bottom:1px solid var(--line);color:var(--mut);font-size:12px;word-break:break-all}
  #fblist{max-height:230px;overflow:auto}
  .fbitem{padding:6px 10px;cursor:pointer;border-bottom:1px solid #1b2230}
  .fbitem:hover{background:#161b22}
  .fbbar{display:flex;gap:6px;flex-wrap:wrap;padding:8px 10px;border-top:1px solid var(--line)}
  .fbbar button{font-size:12px;padding:5px 9px}
  .drive{background:#21262d;color:var(--txt);border:1px solid var(--line);border-radius:6px;padding:3px 8px;font-size:11px;cursor:pointer;margin:0 4px 4px 0}
</style>
</head>
<body>
<header>
  <h1>🎼 Maestro <span>Control Room</span></h1>
  <p>Manuel : une tâche + un check, plusieurs agents en parallèle. Auto : un objectif, l'IA découpe et délègue.</p>
</header>
<main>
  <form id="f">
    <label>Working folder</label>
    <div class="row">
      <input id="repo" placeholder="(none selected)" readonly>
      <button type="button" class="ghost" id="browse">Browse…</button>
    </div>
    <div id="fb">
      <div id="fbpath"></div>
      <div id="fblist"></div>
      <div class="fbbar">
        <button type="button" class="ghost" id="fbup">⬆ Up</button>
        <button type="button" id="fbuse">✓ Use this folder</button>
        <button type="button" class="ghost" id="fbclose">Cancel</button>
        <span id="fbdrives"></span>
      </div>
    </div>

    <label>Mode</label>
    <div class="modes">
      <button type="button" id="mRace" class="modebtn active">Manuel (race)</button>
      <button type="button" id="mAuto" class="modebtn">Auto (objectif)</button>
    </div>

    <div id="manuelFields">
      <label>Task</label>
      <textarea id="task">Fix the failing tests by correcting the bug in the source.</textarea>
      <label>Check command (exit 0 = done)</label>
      <input id="check" value="python -m pytest -q">
    </div>

    <div id="autoFields" style="display:none">
      <label>Goal (objectif global)</label>
      <textarea id="goal">Fix every TypeScript error reported by tsc.</textarea>
      <label>Orchestrator (modèle qui planifie)</label>
      <input id="orchestrator" value="ollama:gpt-oss:120b-cloud">
      <div class="chips" id="orchChips"></div>
    </div>

    <label>Stratégie</label>
    <div class="modes">
      <button type="button" id="sRace" class="modebtn active">Race (tous en même temps)</button>
      <button type="button" id="sCasc" class="modebtn">Cascade (gratuit d'abord)</button>
    </div>
    <p class="hint" id="stratHint">Race : le plus rapide, mais chaque modèle est dépensé.</p>

    <label>Models (agents — cliquez pour ajouter/retirer)</label>
    <input id="models" value="opencode:opencode/deepseek-v4-flash-free,ollama:gpt-oss:120b-cloud">
    <div class="chips" id="modelChips"><span class="hint">détection en cours…</span></div>

    <label>Max attempts</label>
    <input id="max" type="number" value="3" min="1" max="8">
    <label class="check"><input type="checkbox" id="apply" checked>
      Appliquer le gagnant dans mon dossier (annulable)</label>
    <label class="check"><input type="checkbox" id="cache" checked>
      Réutiliser une réponse déjà validée (0 token)</label>
    <button id="go" type="submit">▶ Lancer</button>
  </form>

  <section>
    <div class="winner" id="winner"></div>
    <div class="grid" id="grid"><p class="empty">Pick a folder, choose a mode, and launch.</p></div>
  </section>
</main>
<script>
  const $ = id => document.getElementById(id);
  const esc = s => (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  let timer=null, runId=null, browsePath="", browseParent=null, mode="race";

  function setMode(m){
    mode=m;
    $("mRace").classList.toggle("active", m==="race");
    $("mAuto").classList.toggle("active", m==="auto");
    $("manuelFields").style.display = m==="race" ? "block" : "none";
    $("autoFields").style.display   = m==="auto" ? "block" : "none";
    $("go").textContent = m==="race" ? "▶ Lancer la course" : "▶ Lancer en auto";
  }
  $("mRace").onclick=()=>setMode("race");
  $("mAuto").onclick=()=>setMode("auto");

  // Strategy: race everyone at once, or climb the price ladder.
  let strategy="race";
  function setStrategy(s){
    strategy=s;
    $("sRace").classList.toggle("active", s==="race");
    $("sCasc").classList.toggle("active", s==="cascade");
    $("stratHint").textContent = s==="race"
      ? "Race : le plus rapide, mais chaque modèle listé est dépensé."
      : "Cascade : les modèles gratuits d'abord. Votre abonnement Claude/Codex n'est utilisé que si le gratuit échoue.";
  }
  $("sRace").onclick=()=>setStrategy("race");
  $("sCasc").onclick=()=>setStrategy("cascade");

  // Provider chips are built from /api/doctor, so the list shows what is
  // actually installed here — with a reason attached to anything that isn't.
  const TIERS=[["free","Gratuit"],["subscription","Abonnement — sans clé API"],["api-key","Clé API requise"]];
  let DOCTOR=/*CATALOG*/[];
  function syncChips(rowId, value, multi){
    const parts = multi ? value.split(",").map(s=>s.trim()).filter(Boolean) : [value.trim()];
    $(rowId).querySelectorAll(".chip").forEach(ch=>{
      ch.classList.toggle("on", parts.includes(ch.getAttribute("data-v")));
    });
  }
  function paintChips(rowId, list, inputId, multi){
    let html="";
    TIERS.forEach(([tier,label])=>{
      const items=list.filter(b=>b.tier===tier);
      if(!items.length) return;
      html += `<div class="tierhead"><b>${esc(label)}</b></div>`;
      html += items.map(b=>{
        const why = b.detail + (b.warn ? "  ⚠ " + b.warn : "");
        // null = not probed yet; the chip is listed but not yet judged.
        const tick = b.available===true ? "✓" : (b.available===false ? "✗" : "·");
        return `<span class="chip${b.available===false?" off":""}" data-v="${esc(b.spec)}" title="${esc(why)}">`+
               `<span class="tick">${tick}</span>${esc(b.label)}</span>`;
      }).join("");
    });
    $(rowId).innerHTML = html || '<span class="hint">aucun backend détecté</span>';
    $(rowId).querySelectorAll(".chip").forEach(ch=>{
      ch.onclick = () => {
        const v = ch.getAttribute("data-v"), inp = $(inputId);
        if(multi){
          let parts = inp.value.split(",").map(s=>s.trim()).filter(Boolean);
          const i = parts.indexOf(v);
          if(i>=0) parts.splice(i,1); else parts.push(v);
          inp.value = parts.join(", ");
        } else { inp.value = v; }
        syncChips(rowId, inp.value, multi);
      };
    });
    syncChips(rowId, $(inputId).value, multi);
  }
  function paintAll(){
    paintChips("modelChips", DOCTOR, "models", true);
    paintChips("orchChips", DOCTOR.filter(b=>b.can_plan), "orchestrator", false);
  }
  paintAll();  // instantly, from the catalogue baked into the page
  async function loadDoctor(){
    try { DOCTOR = (await (await fetch("/api/doctor")).json()).backends || DOCTOR; }
    catch(e){ return; }
    paintAll();
    const down = DOCTOR.filter(b=>b.available===false && ($("models").value||"").includes(b.kind));
    if(down.length){
      $("grid").innerHTML = down.map(b=>
        `<div class="card"><div class="top"><span class="name">${esc(b.label)}</span>
         <span class="badge b-err"><span class="dot"></span>indisponible</span></div>
         <div class="note">${esc(b.detail)}</div></div>`).join("");
    }
  }
  loadDoctor();
  $("models").addEventListener("input", ()=>syncChips("modelChips", $("models").value, true));
  $("orchestrator").addEventListener("input", ()=>syncChips("orchChips", $("orchestrator").value, false));

  // folder browser
  $("browse").onclick = () => { $("fb").style.display="block"; loadDir($("repo").value||""); };
  $("fbclose").onclick = () => { $("fb").style.display="none"; };
  $("fbup").onclick = () => { if (browseParent!==null) loadDir(browseParent); };
  $("fbuse").onclick = () => { $("repo").value=browsePath; $("fb").style.display="none"; };
  async function loadDir(path){
    const d = await (await fetch("/api/ls?path="+encodeURIComponent(path||""))).json();
    browsePath=d.path; browseParent=d.parent;
    $("fbpath").textContent = d.error ? (d.path+"  ("+d.error+")") : d.path;
    $("fblist").innerHTML = (d.dirs||[]).map(n=>`<div class="fbitem" data-n="${esc(n)}">📁 ${esc(n)}</div>`).join("")
      || '<div class="fbitem empty">(no sub-folders)</div>';
    $("fblist").querySelectorAll(".fbitem[data-n]").forEach(el=>{
      el.onclick=()=>loadDir(joinPath(d.path, el.getAttribute("data-n")));
    });
    $("fbdrives").innerHTML=(d.drives||[]).map(dr=>`<span class="drive" data-d="${esc(dr)}">${esc(dr)}</span>`).join("");
    $("fbdrives").querySelectorAll(".drive").forEach(el=>{ el.onclick=()=>loadDir(el.getAttribute("data-d")); });
  }
  function joinPath(base,name){ const sep=base.includes("\\")?"\\":"/"; return base.endsWith(sep)?base+name:base+sep+name; }

  $("f").addEventListener("submit", async e => {
    e.preventDefault();
    if(!$("repo").value){ alert("Pick a working folder first."); return; }
    $("go").disabled=true; $("winner").style.display="none";
    let url, body={ repo:$("repo").value, models:$("models").value,
                    max_attempts:parseInt($("max").value||"3"),
                    strategy:strategy, apply:$("apply").checked, cache:$("cache").checked };
    if(mode==="race"){ url="/api/race"; body.task=$("task").value; body.check=$("check").value; }
    else { url="/api/auto"; body.goal=$("goal").value; body.orchestrator=$("orchestrator").value; }
    const j = await (await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json();
    if(j.error){ alert(j.error); $("go").disabled=false; return; }
    runId=j.id;
    if(timer) clearInterval(timer);
    timer=setInterval(()=>poll(runId),900); poll(runId);
  });

  async function poll(id){
    const s = await (await fetch("/api/status?id="+id)).json();
    render(s);
    if(s.done){ clearInterval(timer); $("go").disabled=false; }
  }

  function raceBadge(c){
    if(c.status==="queued") return ["b-queued","en attente"];
    if(c.status!=="done") return ["b-running","running · try "+(c.attempts||0)];
    const r=c.reason||(c.passed?"passed":"failed");
    if(r==="passed") return ["b-pass","PASS"];
    if(r==="skipped") return ["b-skip","non dépensé"];
    if(r==="stopped") return ["b-stop","stopped"];
    if(r==="error") return ["b-err","error"];
    if(["runaway","stalled","timeout"].includes(r)) return ["b-warn","⚠ "+r];
    return ["b-fail","failed"];
  }
  function stepBadge(st){
    return {pending:["b-pending","pending"],running:["b-running","running"],
            done:["b-pass","done"],failed:["b-fail","failed"]}[st]||["b-pending",st];
  }
  function undoBtn(sid){
    return sid ? `<button class="undo" id="undoBtn" data-sid="${esc(sid)}">↺ Annuler ces modifications</button>` : "";
  }
  function wireUndo(){
    const b=$("undoBtn"); if(!b) return;
    b.onclick=async()=>{ b.disabled=true; b.textContent="annulation…";
      const r=await (await fetch("/api/undo?sid="+encodeURIComponent(b.getAttribute("data-sid")),{method:"POST"})).json();
      b.textContent = r.ok ? ("↺ annulé — "+r.restored.length+" fichier(s) restauré(s)") : ("⚠ "+(r.error||"échec"));
    };
  }

  function render(s){
    const w=$("winner");
    if(s.steps!==undefined){ // AUTO
      const steps=s.steps||[];
      $("grid").innerHTML = steps.length ? steps.map((st,i)=>{
        let [cls,label]=stepBadge(st.status);
        if(st.skipped==="already-green"){ cls="b-skip"; label="déjà verte · 0 token"; }
        else if(st.cached){ cls="b-cache"; label="cache · 0 token"; }
        const files = (st.applied_files&&st.applied_files.length)?("applied: "+st.applied_files.join(", ")):"";
        const breakdown = (st.results||[]).map(r=>
          esc(r.spec||r.model)+" → "+esc(r.reason||(r.passed?"passed":"failed"))+
          (r.error?" ("+esc(r.error.slice(0,80))+")":"")
        ).join("<br>");
        return `<div class="card"><div class="top"><span class="name">${i+1}. ${esc(st.title)}</span>
          <span class="badge ${cls}"><span class="dot"></span>${label}</span></div>
          <div class="note">${esc(st.task||"")}</div>
          <div class="stats"><span>check <b>${esc(st.check||"")}</b></span></div>
          <div class="note">${esc(st.winner?("✓ "+st.winner+"  "):"")}${esc(files)}</div>
          ${breakdown?`<div class="note">${breakdown}</div>`:""}</div>`;
      }).join("") : (s.done ? '<p class="empty">No checkable sub-task was planned — try a more specific goal, or pick a different orchestrator.</p>' : '<p class="empty">planning…</p>');
      if(s.done){ w.style.display="block";
        const free = s.free_steps ? ` — ${s.free_steps} sous-tâche(s) à 0 token (déjà verte, ou en cache)` : "";
        w.innerHTML = s.error ? ("⚠ "+esc(s.error))
          : (s.ok ? "🏆 <b>SUCCESS</b> — toutes les sous-tâches sont passées et appliquées."+esc(free)
                  : "Terminé — certaines sous-tâches n'ont pas passé (rien d'appliqué pour celles-là)."+esc(free)); }
      return;
    }
    // RACE
    const racers=Object.values(s.racers||{});
    $("grid").innerHTML = racers.map(c=>{
      const [cls,label]=raceBadge(c);
      const stop = c.status!=="done" ? `<button class="stopbtn" data-s="${esc(c.spec)}">■ Stop</button>` : "";
      return `<div class="card"><div class="top"><span class="name">${esc(c.model||c.spec)}</span>
        <span class="badge ${cls}"><span class="dot"></span>${label}</span></div>
        <div class="stats"><span>attempts <b>${c.attempts||0}</b></span>
          <span>tokens <b>${c.tokens||0}</b></span><span>cost <b>$${(c.cost||0).toFixed(4)}</b></span></div>
        <div class="note">${esc(c.note||"")}</div>${stop}</div>`;
    }).join("") || '<p class="empty">starting…</p>';
    $("grid").querySelectorAll(".stopbtn").forEach(b=>{
      b.onclick=()=>{ b.textContent="stopping…"; b.disabled=true;
        fetch("/api/stop?id="+runId+"&model="+encodeURIComponent(b.getAttribute("data-s")),{method:"POST"}); };
    });
    if(!s.done) return;
    w.style.display="block"; w.classList.remove("free");
    const files=(s.applied_files&&s.applied_files.length)
      ? `<div class="note">appliqué : ${esc(s.applied_files.join(", "))}</div>` : "";
    const unused=(s.unused_tiers&&s.unused_tiers.length)
      ? `<div class="note">jamais dépensé : ${esc(s.unused_tiers.join(", "))}</div>` : "";

    if(s.error){ w.innerHTML="⚠ "+esc(s.error); }
    else if(s.skipped==="already-green"){ w.classList.add("free");
      w.innerHTML="✅ <b>Rien à faire</b> — le check passait déjà. Aucun modèle lancé, 0 token."; }
    else if(s.skipped==="invalid-check"){
      w.innerHTML="⚠ <b>Check invalide</b> — "+esc(s.detail||"")+
        '<div class="note">Corrigez la commande : tous les agents auraient échoué dessus de la même façon.</div>'; }
    else if(s.cached){ w.classList.add("free");
      w.innerHTML="⚡ <b>Rejoué depuis le cache</b> — 0 token, aucun modèle lancé."+files+undoBtn(s.undo); }
    else if(s.warning){ w.innerHTML="⚠ "+esc(s.warning); }
    else if(s.winner && s.racers[s.winner]){ const win=s.racers[s.winner];
      w.innerHTML=`🏆 <b>WINNER:</b> ${esc(win.model)} — $${(win.cost||0).toFixed(4)}`+
        ` · ${s.tokens||0} tokens`+files+unused+undoBtn(s.undo); }
    else { w.innerHTML="Aucun modèle n'a passé le check."+unused; }
    wireUndo();
  }
</script>
</body>
</html>
"""
