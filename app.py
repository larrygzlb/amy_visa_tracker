#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "requests"]
# ///
"""
Small local web app around application_tracker.py.

    uv run app.py

Opens http://127.0.0.1:8765 in your browser. Click "Start check" to run the VFS and
Infovisa checks; results and timestamps are kept in data/checks.jsonl.
"""

import json
import threading
import traceback
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import application_tracker

HOST, PORT = "127.0.0.1", 8765
HISTORY = Path(__file__).parent / "data" / "checks.jsonl"
LOOP_MINUTES = 20

state = {"running": False, "started_at": None, "loop": False, "next_run": None, "loop_minutes": LOOP_MINUTES}
lock = threading.Lock()
loop_stop = None  # threading.Event of the active loop; each loop gets its own


def load_history(limit: int = 20) -> list:
    if not HISTORY.exists():
        return []
    lines = HISTORY.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in reversed(lines[-limit:]) if line.strip()]


def try_begin() -> bool:
    """Mark a check as running; False if one is already running"""
    with lock:
        if state["running"]:
            return False
        state["running"] = True
        state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return True


def loop_worker(stop: threading.Event):
    """Run a check now, then every LOOP_MINUTES, until `stop` is set"""
    while not stop.is_set():
        next_run = datetime.now() + timedelta(minutes=LOOP_MINUTES)  # counted from the start of this check
        if try_begin():  # skip this round if a manual check is still running
            worker()
        with lock:
            if stop.is_set():
                break
            state["next_run"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
        stop.wait(max(0, (next_run - datetime.now()).total_seconds()))


def worker():
    try:
        result = application_tracker.run_check()
    except Exception as e:  # e.g. Playwright browser not installed
        traceback.print_exc()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = {"checked_at": now, "vfs": {"ok": False, "status": str(e)}, "ibz": {"ok": False, "status": str(e)}}
    HISTORY.parent.mkdir(exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    with lock:
        state["running"] = False


class Handler(BaseHTTPRequestHandler):
    def send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code: int = 200):
        self.send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            with lock:
                status = dict(state)
            self.send_json({**status, "history": load_history()})
        else:
            self.send(404, b"Not found", "text/plain")

    def do_POST(self):
        global loop_stop
        if self.path == "/api/check":
            if not try_begin():
                return self.send_json({"error": "A check is already running"}, 409)
            threading.Thread(target=worker, daemon=True).start()
            self.send_json({"started": True})
        elif self.path == "/api/loop/start":
            with lock:
                if state["loop"]:
                    return self.send_json({"error": "Loop is already running"}, 409)
                state["loop"] = True
                loop_stop = threading.Event()
            threading.Thread(target=loop_worker, args=(loop_stop,), daemon=True).start()
            self.send_json({"loop": True})
        elif self.path == "/api/loop/stop":
            with lock:
                if loop_stop:
                    loop_stop.set()  # a check in progress finishes; no new ones start
                state["loop"] = False
                state["next_run"] = None
            self.send_json({"loop": False})
        else:
            self.send(404, b"Not found", "text/plain")

    def log_message(self, *args):
        pass  # keep the terminal for the tracker's own output


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Visa Tracker</title>
<style>
  :root {
    --bg: #f6f7f9; --card: #fff; --text: #1d2330; --muted: #6b7280; --line: #e5e7eb;
    --accent: #1f4fd1; --accent-text: #fff; --ok: #0f7a45; --err: #b42318; --err-bg: #fef3f2;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #12151b; --card: #1b1f27; --text: #e7e9ee; --muted: #9aa1ad; --line: #2b313c;
      --accent: #5b8cff; --accent-text: #0b0f17; --ok: #4ade80; --err: #f87171; --err-bg: #2a1616;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 32px 16px 48px; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 22px; margin: 0; }
  .sub { color: var(--muted); font-size: 13px; margin-top: 2px; }
  button { background: var(--accent); color: var(--accent-text); border: 0; border-radius: 8px;
           padding: 10px 20px; font-size: 15px; font-weight: 600; cursor: pointer; display: inline-flex;
           align-items: center; gap: 8px; }
  button:disabled { opacity: .6; cursor: default; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  button.secondary { background: transparent; color: var(--accent); box-shadow: inset 0 0 0 1.5px var(--accent); }
  button.secondary.on { color: var(--err); box-shadow: inset 0 0 0 1.5px var(--err); }
  .loop-status { margin-top: 12px; font-size: 13px; color: var(--ok); display: none; align-items: center; gap: 8px; }
  .loop-status.on { display: flex; }
  .loop-status::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--ok);
                         animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: .3; } }
  .spinner { width: 14px; height: 14px; border: 2px solid currentColor; border-right-color: transparent;
             border-radius: 50%; animation: spin .8s linear infinite; display: none; }
  button.busy .spinner { display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .cards { display: grid; grid-template-columns: 1fr; gap: 12px; margin: 24px 0 8px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
  .label { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }
  .status { margin-top: 6px; font-weight: 500; word-break: break-word; }
  .status.err { color: var(--err); }
  .card.wide { grid-column: 1 / -1; }
  .queries { margin-top: 8px; display: grid; gap: 2px; }
  .q { display: grid; grid-template-columns: 90px minmax(0, 1fr) auto; gap: 12px; align-items: baseline;
       padding: 6px 0; border-top: 1px solid var(--line); font-size: 13px; }
  .q:first-child { border-top: 0; }
  .q .field { color: var(--muted); }
  .q .value { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
  .q .res { font-weight: 600; text-align: right; }
  .q .res.none { color: var(--muted); font-weight: 500; }
  .q .res.hit { color: var(--ok); }
  .q .res.err { color: var(--err); }
  .q .detail { grid-column: 2 / -1; color: var(--ok); word-break: break-word; }
  @media (max-width: 600px) { .q { grid-template-columns: 62px minmax(0, 1fr) auto; gap: 8px; } }
  td .q { grid-template-columns: 62px minmax(0, 1fr) auto; gap: 8px; padding: 2px 0; border: 0; font-size: 12px; }
  td .q .field { display: inline; }
  th.ibz-col { width: 46%; }
  @media (max-width: 600px) {
    thead { display: none; }
    table, tbody, tr, td { display: block; width: 100%; }
    tr { border-bottom: 1px solid var(--line); padding: 8px 0; }
    tr:last-child { border-bottom: 0; }
    td { border: 0; padding: 4px 12px; }
    td.time { font-weight: 600; color: var(--text); }
    td .q { grid-template-columns: 62px minmax(0, 1fr) auto; }
    td .q .field { display: inline; }
  }
  .stamp { color: var(--muted); font-size: 13px; margin-bottom: 28px; }
  h2 { font-size: 15px; margin: 0 0 8px; }
  table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--line);
          border-radius: 10px; overflow: hidden; font-size: 13px; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 500; }
  tr:last-child td { border-bottom: 0; }
  td.time { white-space: nowrap; color: var(--muted); }
  td.err { color: var(--err); }
  .empty { color: var(--muted); padding: 16px; text-align: center; }
  .notice { background: var(--err-bg); color: var(--err); border-radius: 8px; padding: 8px 12px;
            margin-top: 16px; display: none; }
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Belgium visa tracker</h1>
      <div class="sub">VFS Global + Immigration Office (Infovisa)</div>
    </div>
    <div class="actions">
      <button id="loop" class="secondary">Start loop</button>
      <button id="start"><span class="spinner"></span><span id="btn-text">Start check</span></button>
    </div>
  </header>
  <div class="loop-status" id="loop-status"></div>
  <div class="notice" id="notice"></div>

  <div class="cards">
    <div class="card"><div class="label">VFS Global</div><div class="status" id="vfs">No checks yet</div></div>
    <div class="card wide"><div class="label">Immigration Office (IBZ) · one search per input</div>
      <div class="status" id="ibz">No checks yet</div><div class="queries" id="ibz-queries"></div></div>
  </div>
  <div class="stamp" id="stamp"></div>

  <h2>History</h2>
  <table>
    <thead><tr><th>Checked at</th><th>VFS</th><th class="ibz-col">IBZ</th></tr></thead>
    <tbody id="history"><tr><td colspan="3" class="empty">No checks yet</td></tr></tbody>
  </table>
</main>
<script>
const $ = id => document.getElementById(id);
let polling = null;

const FIELD_NAMES = { visumnr: "Visa no.", refnum: "Reference" };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// One row per Infovisa input: field, value, and the result for that input
function queryRows(queries) {
  return queries.map(q => {
    const row = el("div", "q");
    const hit = q.ok && q.status !== "No Result";
    const kind = !q.ok ? "err" : hit ? "hit" : "none";
    row.append(el("span", "field", FIELD_NAMES[q.field] || q.field), el("span", "value", q.value),
               el("span", "res " + kind, !q.ok ? "Error" : hit ? "Result found" : "No Result"));
    if (!q.ok || hit) row.append(el("span", "detail", q.status));
    return row;
  });
}

function cell(r) {
  const td = el("td", r && !r.ok ? "err" : "");
  if (r && r.queries) td.append(...queryRows(r.queries));
  else td.textContent = r ? r.status : "";
  return td;
}

function render(s) {
  const btn = $("start");
  btn.disabled = s.running;
  btn.classList.toggle("busy", s.running);
  $("btn-text").textContent = s.running ? "Checking… (up to a minute)" : "Start check";

  const loopBtn = $("loop");
  loopBtn.textContent = s.loop ? "Stop loop" : "Start loop";
  loopBtn.classList.toggle("on", s.loop);
  $("loop-status").classList.toggle("on", s.loop);
  $("loop-status").textContent = !s.loop ? "" : s.running && !s.next_run
    ? `Loop on · every ${s.loop_minutes} min · first check running`
    : `Loop on · every ${s.loop_minutes} min · next check at ${(s.next_run || "").slice(11, 16)}`;

  const last = s.history[0];
  if (last) {
    for (const k of ["vfs", "ibz"]) {
      $(k).textContent = last[k].status;
      $(k).className = "status" + (last[k].ok ? "" : " err");
    }
    $("ibz-queries").replaceChildren(...queryRows(last.ibz.queries || []));
    $("stamp").textContent = "Last checked: " + last.checked_at;
  }
  if (s.running) $("stamp").textContent = "Check started at " + s.started_at + "…";

  const body = $("history");
  body.replaceChildren();
  if (!s.history.length) {
    body.innerHTML = '<tr><td colspan="3" class="empty">No checks yet</td></tr>';
  }
  for (const h of s.history) {
    const tr = document.createElement("tr");
    const t = document.createElement("td");
    t.className = "time";
    t.textContent = h.checked_at;
    tr.append(t, cell(h.vfs), cell(h.ibz));
    body.append(tr);
  }

  // Poll while a check runs, and keep polling while the loop is on so new results show up
  const wanted = s.running ? 2000 : s.loop ? 10000 : 0;
  if (polling && polling.ms !== wanted) { clearInterval(polling.id); polling = null; }
  if (wanted && !polling) polling = { ms: wanted, id: setInterval(refresh, wanted) };
}

async function refresh() {
  try {
    const r = await fetch("/api/status");
    render(await r.json());
    $("notice").style.display = "none";
  } catch (e) {
    $("notice").textContent = "Can't reach the app. Is app.py still running?";
    $("notice").style.display = "block";
  }
}

$("loop").addEventListener("click", async () => {
  const on = $("loop").classList.contains("on");
  await fetch(on ? "/api/loop/stop" : "/api/loop/start", { method: "POST" });
  refresh();
});

$("start").addEventListener("click", async () => {
  await fetch("/api/check", { method: "POST" });
  refresh();
});

refresh();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:  # one check from the command line, saved to the same history
        try_begin()
        worker()
        last = load_history(1)[0]
        print(f"\nChecked at {last['checked_at']}")
        print(f"VFS: {last['vfs']['status']}")
        for q in last["ibz"].get("queries", []):
            print(f"IBZ: {q['field']}={q['value']}: {q['status']}")
        sys.exit(0)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Visa tracker running at {url}  (Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
