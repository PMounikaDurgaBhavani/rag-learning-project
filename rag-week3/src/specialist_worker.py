"""The specialist worker: the two Week 10 specialists behind one HTTP boundary.

    POST /specialist/ticket_specialist     {"ticket": {...}, "case_id": "..."}
    POST /specialist/knowledge_specialist  {"ticket": {...}, "case_id": "..."}
    GET  /health

A good answer is 200 {"status": "ok", "result": {...}}. A specialist that
raises answers 500 {"status": "error", "error": "<name> specialist failed"}:
the message names the specialist and nothing else, because the body goes
back to a model and, through the UI, to a customer.

The fault switch is the point of this file. With

    WEEK10_FAULT=knowledge_specialist:CD-10407        (or fault=... in code)

the worker answers HTTP 500 to that one specialist for that one ticket id,
before the specialist runs, and serves every other request normally. That
is how the failure experiment and the failure test break the dependency
without breaking the service.

    venv/bin/python src/specialist_worker.py --port 8941
    WEEK10_FAULT=ticket_specialist:CD-10407 venv/bin/python src/specialist_worker.py --port 8941
"""

import argparse
import http.server
import json
import os
import sys
import threading
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def parse_fault(text):
    """'knowledge_specialist:CD-10407' -> {"specialist": ..., "ticket_id": ...}."""
    if not text:
        return None
    name, _, ticket_id = str(text).partition(":")
    if not name or not ticket_id:
        raise ValueError("WEEK10_FAULT must look like specialist_name:TICKET-ID")
    return {"specialist": name.strip(), "ticket_id": ticket_id.strip().upper()}


class SpecialistWorker:
    """One worker, N specialists, an optional fault, an access log."""

    def __init__(self, specialists=None, host="127.0.0.1", port=0, fault=None):
        self.host = host
        self.port = port
        self.fault = fault if isinstance(fault, dict) or fault is None else parse_fault(fault)
        self._specialists = specialists
        self.requests = []              # one entry per request, for the tests and the report
        self.httpd = None
        self.thread = None

    @property
    def specialists(self):
        if self._specialists is None:
            from multi_agent import build_specialists
            self._specialists = build_specialists()
        return self._specialists

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def faulted(self, name, ticket):
        return bool(self.fault and self.fault["specialist"] == name
                    and str((ticket or {}).get("ticket_id") or "").upper() == self.fault["ticket_id"])

    def handle(self, name, payload):
        """(status, body) for one specialist request."""
        ticket = payload.get("ticket") or {}
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "specialist": name, "ticket_id": ticket.get("ticket_id"),
                 "case_id": payload.get("case_id")}
        if name not in self.specialists:
            entry["status"] = 404
            self.requests.append(entry)
            return 404, {"status": "error", "error": f"no specialist named {name}"}
        if self.faulted(name, ticket):
            entry["status"] = 500
            entry["fault"] = True
            self.requests.append(entry)
            return 500, {"status": "error", "fault": True,
                         "error": f"injected fault: {name} unavailable for {ticket.get('ticket_id')}"}
        try:
            result = self.specialists[name].run(payload)
        except Exception:                                   # noqa: BLE001
            traceback.print_exc(file=sys.stderr)              # the detail stays in the worker's log
            entry["status"] = 500
            self.requests.append(entry)
            return 500, {"status": "error", "error": f"{name} failed"}
        entry["status"] = 200
        self.requests.append(entry)
        return 200, {"status": "ok", "result": result}

    def start(self):
        worker = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                print(f"[worker] {fmt % args}", file=sys.stderr)

            def _json(self, status, body):
                data = json.dumps(body, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/health":
                    self._json(200, {"status": "ok", "fault": worker.fault,
                                     "specialists": sorted(worker.specialists)})
                else:
                    self._json(404, {"status": "error", "error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError:
                    self._json(400, {"status": "error", "error": "body must be JSON"})
                    return
                if not self.path.startswith("/specialist/"):
                    self._json(404, {"status": "error", "error": "not found"})
                    return
                name = self.path[len("/specialist/"):].strip("/")
                status, body = worker.handle(name, payload)
                self._json(status, body)

        class Server(http.server.ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.httpd = Server((self.host, self.port), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Week 10 specialist worker (HTTP).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8941)
    parser.add_argument("--fault", default=os.environ.get("WEEK10_FAULT"),
                        help="specialist_name:TICKET-ID -> HTTP 500 for that one request")
    args = parser.parse_args(argv)
    worker = SpecialistWorker(host=args.host, port=args.port, fault=parse_fault(args.fault))
    worker.start()
    print(f"[worker] serving {sorted(worker.specialists)} on {worker.url}"
          f"{' with fault ' + json.dumps(worker.fault) if worker.fault else ''}", file=sys.stderr)
    try:
        worker.thread.join()
    except KeyboardInterrupt:
        worker.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
