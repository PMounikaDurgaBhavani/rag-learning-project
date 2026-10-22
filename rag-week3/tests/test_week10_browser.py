"""Week 10 support chat, driven in headless Chrome over the DevTools protocol.

    python tests/test_week10_browser.py

The page and the server are the real ones. Only POST /api/week10/chat and the
status poll are replaced inside the page by a scripted fetch that returns
saved backend payloads (week10/runs/live/*.json and a benchmark row) with
delays, so every interactive flow runs without loading a model:

  mode switch and explanation · example chips fill but never submit · Enter
  sends, Shift+Enter does not · duplicate submits blocked · elapsed clock and
  the "loading the model" phase from /api/week10/status · answer bubble with
  sources and collapsed execution details · ask again in the other mode with
  the comparison table · refund example opens the ticket context and the flow
  shows the ticket specialist only when dispatched · not found · 503 degraded
  shown as an answer with the failure · 409 busy with Retry/Edit · lost
  connection · Clear chat and the stale-reply guard · Benchmark & Evaluation
  tabs · the User role.

Needs Google Chrome and the websocket-client package (in requirements.txt).
Skips, with exit code 0, when Chrome is not installed. Writes nothing to week10/.
"""
import glob, json, os, socket, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT); sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("RAG_LANGFUSE", "0"); os.environ.setdefault("RAG_TRACE", "0")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if not os.path.exists(CHROME):
    print("SKIP  Google Chrome not found at", CHROME); sys.exit(0)
try:
    import websocket
except ImportError:
    print("SKIP  websocket-client not installed"); sys.exit(0)
import server as srv

fails = []
def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'' if ok else '  ' + str(detail)[:300]}")
    if not ok: fails.append(name)

# ---- saved backend payloads, chosen by their recorded flags -------------------
def pick(pred):
    for f in sorted(glob.glob("week10/runs/live/*.json")):
        d = json.load(open(f))
        if pred(d): return d
    raise SystemExit("no saved payload matches")
single_ok = pick(lambda d: d["mode"] == "single" and not d["refused"])
multi_nf = pick(lambda d: d["mode"] == "multi" and d["refused"] and d.get("not_found") and not d["degraded"])
multi_deg = pick(lambda d: d["mode"] == "multi" and d["degraded"])

def row_payload(case_id, ticket):
    """A recorded multi-agent execution from the committed benchmark, reshaped to the chat contract."""
    row = next(r for r in json.load(open("week10/results_multi.json"))["rows"] if r["case_id"] == case_id)
    return {"mode": "multi", "ticket": {"ticket_id": row["ticket_id"], "message": row["question"], **ticket},
            "answer": row["answer"], "refused": row["refused"], "refusal_reason": row["refusal_reason"], "not_found": row["not_found"],
            "degraded": row["degraded"], "task_status": row["status"], "errors": row["errors"], "retries": row["retries"],
            "citations": row["citations"], "retrieved": row["retrieved"], "tasks": row["tasks"], "handoffs": row["handoff_records"],
            "routing": row["routing"], "specialists": row["specialists"], "resynthesis": row.get("resynthesis_detail", []),
            "meter": {k: row[k] for k in ("model_calls", "input_tokens", "output_tokens", "tokens", "cost_usd", "handoffs",
                                          "specialist_calls", "tool_calls", "resynthesis", "context_resend_multiplier", "retries")},
            "latency_ms": row["latency_ms"], "trace_id": row["trace_id"], "transport": row["transport"],
            "saved_to": f"week10/results_multi.json (row {case_id})"}
multi_ok = row_payload("T03", {"tier": "Standard", "charge_amount": 49.0, "days_since_charge": 12, "duplicate_charge": False, "refund_requested": True})
multi_kb = row_payload("T01", {"tier": "Standard", "charge_amount": None, "days_since_charge": None, "duplicate_charge": False, "refund_requested": False})
print("payloads:", {k: v.get("saved_to") for k, v in dict(single_ok=single_ok, multi_ok=multi_ok, multi_kb=multi_kb, multi_nf=multi_nf, multi_deg=multi_deg).items()})

# ---- server + chrome ----------------------------------------------------------
with socket.socket() as s: s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
httpd = srv.StudioServer(("127.0.0.1", port), srv.RAGRequestHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
with socket.socket() as s: s.bind(("127.0.0.1", 0)); dbg = s.getsockname()[1]
profile = tempfile.mkdtemp(prefix="chrome-cdp-")
chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu",
                           "--no-first-run", f"--user-data-dir={profile}", f"--remote-debugging-port={dbg}", "about:blank"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json")); break
    except Exception: time.sleep(0.2)
page = next(t for t in targets if t["type"] == "page")
ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True)
_id = 0
def cdp(method, **params):
    global _id
    _id += 1
    ws.send(json.dumps({"id": _id, "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id:
            if "error" in msg: raise RuntimeError(msg["error"])
            return msg.get("result", {})
def js(expr, await_promise=False):
    r = cdp("Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True)
    if "exceptionDetails" in r: raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description") or r["exceptionDetails"])
    return r.get("result", {}).get("value")
def wait(expr, timeout=20, label=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if js(expr): return True
        time.sleep(0.15)
    check(label or f"wait: {expr}", False, "timed out"); return False

cdp("Page.enable"); cdp("Runtime.enable")
cdp("Page.navigate", url=f"http://127.0.0.1:{port}/#week10")
wait("document.querySelectorAll('#w10Examples .chip').length > 0", label="page + examples loaded")

print("\nScripted backend for the chat endpoint (saved payloads, real shapes)")
js("window.__w10 = { queue: [], calls: [], status: {model_loaded: false} };"
   "window.__realFetch = window.fetch;"
   "window.fetch = async (url, opts) => {"
   "  if (typeof url === 'string' && url.endsWith('/api/week10/status')) return new Response(JSON.stringify(window.__w10.status), {status: 200, headers: {'Content-Type': 'application/json'}});"
   "  if (typeof url === 'string' && url.endsWith('/api/week10/chat')) {"
   "    const body = JSON.parse(opts.body); window.__w10.calls.push(body);"
   "    const next = window.__w10.queue.shift() || {status: 500, body: {status: 'error', error: 'no scripted response'}};"
   "    await new Promise(r => setTimeout(r, next.delay || 0));"
   "    if (next.network) throw new TypeError('Failed to fetch');"
   "    return new Response(JSON.stringify(next.body), {status: next.status, headers: {'Content-Type': 'application/json'}});"
   "  }"
   "  return window.__realFetch(url, opts); };"
   "window.confirm = () => true; true")
def enqueue(status, body, delay=0, network=False):
    js(f"window.__w10.queue.push({json.dumps({'status': status, 'body': body, 'delay': delay, 'network': network})}); true")

# 1. mode selector + blurb
js("document.getElementById('w10Mode').value = 'multi'; document.getElementById('w10Mode').dispatchEvent(new Event('change')); true")
check("switching the mode updates the explanation", "manager triages" in js("document.getElementById('w10ModeBlurb').textContent"))
js("document.getElementById('w10Mode').value = 'single'; document.getElementById('w10Mode').dispatchEvent(new Event('change')); true")
check("single-agent explanation", "One agent retrieves" in js("document.getElementById('w10ModeBlurb').textContent"))

# 2. knowledge question, single agent, with a slow response and the elapsed clock
js("document.querySelector('#w10Examples .chip[data-w10ex=\"T11\"]').click(); true")
check("clicking 'Password reset' fills the composer without submitting",
      "resetting my password" in js("document.getElementById('w10Msg').value") and js("window.__w10.calls.length") == 0)
check("a knowledge example leaves the ticket context closed", js("document.getElementById('w10TicketCtx').open") is False)
enqueue(200, {**single_ok, "status": "ok", "cold_start": True}, delay=3500)
js("document.getElementById('w10Msg').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); true")
time.sleep(0.4)
check("Enter sends: user bubble + pending bubble with spinner and clock",
      js("!!document.querySelector('#w10Thread .mcp-msg.user') && !!document.getElementById('w10Elapsed') && !!document.querySelector('#w10Thread .spinner')"))
check("the send button is disabled while a request runs", js("document.getElementById('w10Send').disabled") is True)
check("the pending bubble says the model is loading when status reports it is not loaded",
      "Loading the local model" in js("document.getElementById('w10Phase').textContent"))
js("document.getElementById('w10Msg').value = 'second question'; document.getElementById('w10Msg').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); true")
check("a second Enter during a run does not submit", js("window.__w10.calls.length") == 1)
js("window.__w10.status = {model_loaded: true}; true")
time.sleep(2.2)
check("the elapsed clock advances", js("document.getElementById('w10Elapsed')?.textContent") not in (None, "0m 00s"), js("document.getElementById('w10Elapsed')?.textContent"))
wait("!document.getElementById('w10Elapsed')", label="answer replaces the pending bubble")
check("the answer bubble shows mode, outcome, time and the answer text",
      js("(() => { const b = document.querySelectorAll('#w10Thread .mcp-msg.bot'); const t = b[b.length-1].textContent; return t.includes('Single Agent') && t.includes('answered') && t.includes('reset'); })()"))
check("sources consulted are listed from the backend's retrieved passages",
      js("document.querySelector('#w10Thread .w10-sources') !== null && document.querySelector('#w10Thread .w10-sources').textContent.includes('HC-')"))
check("execution details are collapsed by default", js("document.querySelector('#w10Thread details.w10-details').open") is False)
js("[...document.querySelectorAll('#w10Thread details.w10-details')].pop().open = true; true")
check("opening execution details shows the single agent's retrieval table and tokens",
      js("(() => { const d = [...document.querySelectorAll('#w10Thread details.w10-details')].pop(); return d.textContent.includes('Passages given to the model') && d.textContent.includes('tokens in / out'); })()"))
check("the composer was cleared on send, kept what was typed during the run, and is re-enabled", js("document.getElementById('w10Msg').value") == "second question" and js("document.getElementById('w10Send').disabled") is False, js("document.getElementById('w10Msg').value"))
js("document.getElementById('w10Msg').value = ''; true")
check("no raw JSON or task ids in the visible conversation",
      js("(() => { const open = [...document.querySelectorAll('#w10Thread .mcp-bubble')].map(b => { const clone = b.cloneNode(true); clone.querySelectorAll('details').forEach(d => d.remove()); return clone.textContent; }).join(' '); return !open.includes('task-') && !open.includes('{\"'); })()"))

# 3. ask the same question with the other mode -> compare table + flow line
enqueue(200, {**multi_kb, "status": "ok"})
js("document.querySelector('#w10Thread [data-w10other]').click(); true")
wait("document.querySelectorAll('#w10Thread .mcp-msg.bot').length >= 2 && !document.getElementById('w10Elapsed')", label="multi-agent answer arrives")
check("the other-mode request carries the same message and mode=multi",
      js("window.__w10.calls[1].mode") == "multi" and js("window.__w10.calls[1].message") == js("window.__w10.calls[0].message"))
check("the multi-agent answer shows the flow built from recorded dispatches (knowledge specialist only)",
      js("(() => { const f = [...document.querySelectorAll('#w10Thread .w10-flow')].pop(); return f && f.textContent.includes('Manager') && f.textContent.includes('Knowledge Specialist') && !f.textContent.includes('Ticket Specialist'); })()"))
check("a side-by-side comparison appears for the same question", js("[...document.querySelectorAll('#w10Thread details.w10-details summary')].some(s => s.textContent.includes('Compare with the Single Agent'))"))
js("[...document.querySelectorAll('#w10Thread details.w10-details')].pop().open = true; true")
check("multi-agent execution details show routing, specialist findings and the task table",
      js("(() => { const d = [...document.querySelectorAll('#w10Thread details.w10-details')].pop(); return d.textContent.includes('Routing') && d.textContent.includes('Specialist findings') && d.textContent.includes('Tasks and lifecycle'); })()"))

# 4. refund question: ticket context fills and opens; multi flow includes the ticket specialist
js("document.querySelector('#w10Examples details').open = true; true")
js("document.querySelector('[data-w10ex=\"T03\"]').click(); true")
check("a refund example fills the ticket fields and opens the ticket context",
      js("document.getElementById('w10Amount').value") == "49" and js("document.getElementById('w10TicketCtx').open") is True
      and "charged" in js("document.getElementById('w10TicketSummary').textContent"))
js("document.getElementById('w10Mode').value = 'multi'; true")
enqueue(200, {**multi_ok, "status": "ok"})
js("document.getElementById('w10Send').click(); true")
wait("window.__w10.calls.length === 3", label="refund question sent")
check("the request carries the entered ticket context unchanged",
      js("window.__w10.calls[2].ticket.charge_amount") == "49" and js("window.__w10.calls[2].ticket.days_since_charge") == "12" and js("window.__w10.calls[2].ticket.refund_requested") is True)
wait("!document.getElementById('w10Elapsed')", label="refund answer arrives")
check("the flow shows the ticket specialist only because it was dispatched",
      js("[...document.querySelectorAll('#w10Thread .w10-flow')].pop().textContent.includes('Ticket Specialist')"))
check("the user bubble shows the ticket context it was asked with", js("[...document.querySelectorAll('#w10Thread .w10-ticketline')].pop().textContent.includes('$49')"))

# 5. not found
js("document.querySelector('[data-w10ex=\"NF1\"]').click(); true")
enqueue(200, {**multi_nf, "status": "ok"})
js("document.getElementById('w10Send').click(); true")
wait("window.__w10.calls.length === 4 && !document.getElementById('w10Elapsed')", label="not-found answer arrives")
check("an unanswerable question shows 'Not found in the help centre' with a plain explanation and no invented sources",
      js("(() => { const b = [...document.querySelectorAll('#w10Thread .mcp-msg.bot')].pop().textContent; return b.includes('Not found in the help centre') && b.includes('matched this question closely enough') && !b.includes('Cited in the reply'); })()"))

# 6. 503 degraded
js("document.getElementById('w10Msg').value = 'refund??? charged twice $89'; true")
enqueue(503, {**multi_deg, "status": "degraded", "error": "a specialist was unavailable; the ticket was routed to a person instead of being answered"})
js("document.getElementById('w10Send').click(); true")
wait("window.__w10.calls.length === 5 && !document.getElementById('w10Elapsed')", label="degraded answer arrives")
check("a 503 degraded response is shown as an answer with the failure explained, not as an error",
      js("(() => { const b = [...document.querySelectorAll('#w10Thread .mcp-msg.bot')].pop().textContent; return b.includes('A specialist was unavailable') && b.includes('routed to a person') && b.includes('HTTP 500') && !b.includes('Could not get an answer'); })()"))
check("the degraded flow marks the failed specialist", js("[...document.querySelectorAll('#w10Thread .w10-flow')].pop().innerHTML.includes('node bad')"))

# 7. 409 busy -> error bubble with Retry; retry succeeds; question preserved
js("document.getElementById('w10Msg').value = 'How long is a reset link valid?'; true")
enqueue(409, {"status": "error", "error": "another run is in progress: week 7 agent (started 10:00). Wait for it to finish.", "running": "week 7 agent"})
js("document.getElementById('w10Send').click(); true")
wait("window.__w10.calls.length === 6 && !!document.querySelector('#w10Thread [data-w10retry]')", label="error bubble with Retry")
check("a 409 is explained as the server being busy, with Retry and Edit, and nothing auto-resubmits",
      js("(() => { const b = [...document.querySelectorAll('#w10Thread .mcp-msg.bot')].pop().textContent; return b.includes('busy with another request') && b.includes('week 7 agent'); })()") and js("window.__w10.calls.length") == 6)
js("document.querySelector('#w10Thread [data-w10edit]').click(); true")
check("Edit puts the question back into the composer", js("document.getElementById('w10Msg').value") == "How long is a reset link valid?")
enqueue(200, {**single_ok, "status": "ok"})
js("document.querySelector('#w10Thread [data-w10retry]').click(); true")
wait("window.__w10.calls.length === 7 && !document.getElementById('w10Elapsed')", label="retry answered")
check("Retry resubmits the same question in the same mode", js("window.__w10.calls[6].message") == "How long is a reset link valid?" and js("window.__w10.calls[6].mode") == js("window.__w10.calls[5].mode"))

# 8. lost connection
js("document.getElementById('w10Msg').value = 'x'; true")
enqueue(0, {}, network=True)
js("document.getElementById('w10Send').click(); true")
wait("window.__w10.calls.length === 8 && !!document.querySelector('#w10Thread [data-w10retry]')", label="network error bubble")
check("a dropped connection says the server may still be processing, and offers Retry",
      "may still be processing" in js("[...document.querySelectorAll('#w10Thread .mcp-msg.bot')].pop().textContent"))

# 9. clear chat (confirm stubbed to true) and the empty state
js("document.getElementById('w10Clear').click(); true")
check("Clear chat empties the conversation back to the suggestions", js("document.querySelector('#w10Thread .mcp-chat-empty') !== null && document.querySelectorAll('#w10Thread .mcp-msg').length === 0"))

# 10. Clear is blocked while a request runs; a reply whose request was superseded is dropped
enqueue(200, {**single_ok, "status": "ok"}, delay=1500)
js("document.getElementById('w10Msg').value = 'late'; document.getElementById('w10Send').click(); true")
time.sleep(0.3)
check("Clear chat is disabled while a request is running", js("document.getElementById('w10Clear').disabled") is True)
js("w10Seq++; true")                                   # simulate the conversation moving on under the in-flight request
time.sleep(2.0)
check("a reply for a superseded request is dropped: the pending bubble is not replaced by a stale answer",
      js("document.querySelectorAll('#w10Thread .mcp-msg.bot .w10-answer').length") == 0)
js("w10SetBusy(false); w10Messages = []; renderW10Thread(false); true")   # reset the harness state for the next steps
# 11. benchmark section and back
js("document.querySelector('#w10Tabs button[data-tab=\"bench\"]').click(); true")
check("Benchmark & Evaluation shows the stored race table and hides the chat",
      js("!document.getElementById('w10BenchPanel').classList.contains('hidden') && document.getElementById('w10ChatPanel').classList.contains('hidden') && document.getElementById('w10Body').textContent.includes('pass rate')"))
js("document.querySelector('#w10BenchTabs button[data-tab=\"failure\"]').click(); true")
check("the failure experiment tab renders its stored report and the live trigger", js("document.getElementById('w10Body').textContent.includes('HTTP 500') && !!document.getElementById('w10FaultRun')"))
js("document.querySelector('#w10Tabs button[data-tab=\"chat\"]').click(); true")
check("back to the chat", js("!document.getElementById('w10ChatPanel').classList.contains('hidden')"))

# 12. user role: chat only
js("setAppRole('user'); true")
check("in the User role the benchmark tabs and the admin heading are hidden, the chat stays",
      js("document.getElementById('w10Tabs').classList.contains('hidden') && document.getElementById('w10AdminHead').classList.contains('hidden') && !document.getElementById('w10ChatPanel').classList.contains('hidden')"))
js("setAppRole('admin'); true")

ws.close(); chrome.terminate(); httpd.shutdown(); httpd.server_close()
print()
print(f"{len(fails)} check(s) failed" if fails else "All browser checks passed.")
for f in fails: print("  -", f)
sys.exit(1 if fails else 0)
