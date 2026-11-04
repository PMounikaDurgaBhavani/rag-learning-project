"""One MCP host per process, discovered at runtime and re-listed per request.

The help-centre server loads the embedder and the cross-encoder when it
starts (about 20 s cold), so a connection per request would pay that every
time. The process keeps one connected Host and asks it tools/list again on
each request: discovery stays live (a tool added to the server shows up on
the next request), and the connection is reused.

Which config: CAPSTONE_MCP_CONFIG if set; otherwise the HTTP config when the
HTTP server answers (python main.py capstone up starts it), else stdio.
A failed connection is reported, not raised: the agent escalates instead.
"""

import os
import threading
import time
import urllib.request

from capstone.config import MCP_CONFIG, MCP_HTTP_URL, ROOT

HTTP_CONFIG = ROOT / "week12" / "mcp_config_http.json"
_LOCK = threading.RLock()
_HOST = None
_HOST_CONFIG = None
_LAST_ERROR = None


def http_server_up(url=None, timeout=1.5):
    """A GET on the streamable-HTTP endpoint is answered (4xx/405 counts): the process is there."""
    url = url or MCP_HTTP_URL
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def choose_config():
    forced = os.environ.get("CAPSTONE_MCP_CONFIG")
    if forced:
        return str(ROOT / forced) if not os.path.isabs(forced) else forced
    if http_server_up():
        return str(HTTP_CONFIG)
    return str(MCP_CONFIG)


def _connect(config_path, wire=None):
    from mcp_agent import Host
    host = Host(config_path, wire=wire)
    host.connect()
    return host


def get_host(refresh_tools=True, wire=None):
    """A connected host, or None (see last_error()). Re-lists tools when asked."""
    global _HOST, _HOST_CONFIG, _LAST_ERROR
    with _LOCK:
        config = choose_config()
        if _HOST is not None and _HOST_CONFIG != config:
            close()
        if _HOST is None:
            try:
                _HOST = _connect(config, wire=wire)
                _HOST_CONFIG = config
                _LAST_ERROR = None
            except Exception as error:
                _LAST_ERROR = f"{type(error).__name__}: {error}"
                _HOST = None
                return None
        if refresh_tools:
            try:
                rediscover(_HOST)
            except Exception as error:
                # The connection died (server restarted): reconnect once.
                _LAST_ERROR = f"{type(error).__name__}: {error}"
                close()
                try:
                    _HOST = _connect(config, wire=wire)
                    _HOST_CONFIG = config
                    _LAST_ERROR = None
                except Exception as error2:
                    _LAST_ERROR = f"{type(error2).__name__}: {error2}"
                    _HOST = None
                    return None
        return _HOST


def rediscover(host):
    """tools/list again on every connection; the host's tool table is rebuilt from the answers."""
    host.tools = {}
    for name, connection in host.connections.items():
        listed = connection.list_tools()
        host.inventory[name]["tools"] = [t["name"] for t in listed]
        for tool in listed:
            key = tool["name"]
            if key in host.tools:
                key = f"{name}__{tool['name']}"
            host.tools[key] = (name, tool)
    return host.tools


def call(host, key, arguments, meta=None, timeout_s=90.0):
    """tools/call with the connection's timeout bounded for this call."""
    server, _ = host.tools[key]
    connection = host.connections[server]
    previous = connection.timeout
    connection.timeout = timeout_s
    try:
        return host.call(key, arguments, meta=meta)
    finally:
        connection.timeout = previous


def last_error():
    return _LAST_ERROR


def status():
    with _LOCK:
        config = choose_config()
        return {"config": os.path.relpath(config, ROOT), "http_url": MCP_HTTP_URL,
                "http_server_up": http_server_up(), "connected": _HOST is not None,
                "tools": sorted(_HOST.tools) if _HOST else [],
                "inventory": _HOST.inventory if _HOST else {}, "last_error": _LAST_ERROR,
                "checked_at": time.time()}


def close():
    global _HOST, _HOST_CONFIG
    with _LOCK:
        if _HOST is not None:
            try:
                _HOST.close()
            except Exception:
                pass
        _HOST = None
        _HOST_CONFIG = None
