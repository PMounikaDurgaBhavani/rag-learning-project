"""The Week 9 host: an agent that learns its tools from MCP servers at runtime.

Nothing here names a tool. The host reads an MCP config, opens one client
connection per server, asks each one tools/list, and hands the union of what
comes back to the model. Add a server to the config and the model sees its
tools on the next run; this file does not change. That is the whole claim of
discovery, and week9/agent_diff.txt is its proof.

Where the model runs: here, in the host process (agent_runtime.chat, Qwen2.5-1.5B
on this machine) and nowhere else. The model reads the discovered tool list and
writes tool calls; the client turns each into a tools/call; the server runs a
plain function and returns content. No server and no part of the JSON-RPC
exchange calls a model.

Primitives, by who decides:
    tools      the model decides to call them        (tools/list, tools/call)
    resources  the app decides to attach them        (--attach URI -> resources/read)
    prompts    the user picks one                    (--prompt NAME k=v -> prompts/get)

    python src/mcp_agent.py --config week9/mcp_config.json --list
    python src/mcp_agent.py --config week9/mcp_config.json "Where is ticket TCK-4471?"
    python src/mcp_agent.py --config ... --attach tickets://TCK-4471/escalations "..."
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_runtime import Budgets, MODEL_NAME, chat, cost_usd, parse_tool_calls  # noqa: E402
from mcp_client import MCPConnection, MCPError, load_config, tool_text  # noqa: E402

SYSTEM_PROMPT = (
    "You are a CloudDesk support assistant. You can call the tools listed below. "
    "Call one tool at a time and use what it returns. Never invent ids, dates or "
    "amounts. When you have what you need, reply to the user in plain text."
)

MAX_TOOL_RESULT_CHARS = 1500
MAX_NEW_TOKENS = 160


class Host:
    """One host, N servers. Everything it knows about tools comes from tools/list."""

    def __init__(self, config_path, wire=None, log=None):
        self.config_path = os.path.abspath(config_path)
        # Relative commands and paths in the config resolve against the repo
        # root: the directory above the config's own folder.
        self.root = os.path.dirname(os.path.dirname(self.config_path))
        self.wire = wire
        self.log = log if log is not None else []
        self.connections = {}
        self.tools = {}            # tool name -> (server name, tool dict from tools/list)
        self.inventory = {}

    def record(self, line, verbose=False):
        self.log.append(line)
        if verbose:
            print(line)

    def connect(self, verbose=False):
        for name, spec in load_config(self.config_path).items():
            connection = MCPConnection(name, spec, root=self.root, wire=self.wire)
            init = connection.connect()
            listed = connection.list_tools()
            self.connections[name] = connection
            self.inventory[name] = {
                "transport": connection.transport,
                "server_info": init.get("serverInfo"),
                "protocol_version": init.get("protocolVersion"),
                "tools": [t["name"] for t in listed],
                "resources": [r.get("uri") for r in connection.list_resources()],
                "resource_templates": [r.get("uriTemplate")
                                       for r in connection.list_resource_templates()],
                "prompts": [p["name"] for p in connection.list_prompts()],
            }
            for tool in listed:
                key = tool["name"]
                if key in self.tools:          # two servers, one name: qualify both
                    key = f"{name}__{tool['name']}"
                self.tools[key] = (name, tool)
            self.record(f"[host] {name} ({connection.transport}) tools/list -> "
                        f"{[t['name'] for t in listed]}", verbose)
        self.record(f"[host] discovered {len(self.tools)} tools: {sorted(self.tools)}", verbose)
        return self.inventory

    def tool_schemas(self):
        """MCP tool definitions, reshaped into the model's function-calling format."""
        return [{"type": "function",
                 "function": {"name": key, "description": tool.get("description", ""),
                              "parameters": tool.get("inputSchema", {"type": "object"})}}
                for key, (_, tool) in self.tools.items()]

    def call(self, key, arguments, verbose=False):
        if key not in self.tools:
            return {"isError": True, "content": [{"type": "text", "text":
                    f"unknown tool {key!r}; available: {sorted(self.tools)}"}]}, None
        server, tool = self.tools[key]
        try:
            result = self.connections[server].call_tool(tool["name"], arguments)
        except MCPError as error:
            result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
        return result, server

    def attach(self, uri):
        """App-attached context: the host reads a resource; the model spends no turn on it."""
        scheme = uri.split("://", 1)[0]
        for name, connection in self.connections.items():
            shapes = self.inventory[name]["resources"] + self.inventory[name]["resource_templates"]
            if any(shape and shape.split("://", 1)[0] == scheme for shape in shapes):
                result = connection.read_resource(uri)
                text = "\n".join(c.get("text", "") for c in result.get("contents", []))
                return name, text
        raise KeyError(f"no server lists a resource for {uri}")

    def prompt(self, name, arguments):
        for server, connection in self.connections.items():
            if name in self.inventory[server]["prompts"]:
                result = connection.get_prompt(name, arguments)
                return server, "\n".join(m["content"].get("text", "")
                                         for m in result.get("messages", []))
        raise KeyError(f"no server lists a prompt named {name}")

    def run(self, query, attach=(), budgets=None, forced_call=None, verbose=False):
        """The loop. Returns a trace with every tools/call and the server it went to.

        forced_call ({"name", "arguments"}) replays a recorded first tool call
        instead of asking the model for lap 1, so two server versions can be
        shown handling the identical call.
        """
        budgets = budgets or Budgets(max_iterations=6, max_tokens=8000,
                                     max_cost_usd=0.01, max_wall_seconds=1800.0)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for uri in attach:
            server, text = self.attach(uri)
            self.record(f"[host] attached resource {uri} from {server} "
                        f"({len(text)} chars, no model turn)", verbose)
            messages.append({"role": "system",
                             "content": f"Attached context ({uri}):\n{text}"})
        messages.append({"role": "user", "content": query})

        meter = {"laps": 0, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
        calls, answer, terminated_by = [], None, None
        started = time.perf_counter()
        schemas = self.tool_schemas()
        self.record(f"[host] query: {query!r}", verbose)

        while True:
            elapsed = time.perf_counter() - started
            limit, detail = budgets.exceeded(meter["laps"], meter["input_tokens"]
                                             + meter["output_tokens"],
                                             cost_usd(meter["input_tokens"],
                                                      meter["output_tokens"]), elapsed)
            if limit:
                terminated_by = limit
                self.record(f"[host] BUDGET {limit} ({detail}) — stopping", verbose)
                break
            meter["laps"] += 1

            if forced_call and meter["laps"] == 1:
                text = f"<tool_call>\n{json.dumps(forced_call)}\n</tool_call>"
                self.record(f"[host] lap 1: replayed call {json.dumps(forced_call)}", verbose)
            else:
                response = chat(messages, tools=schemas, max_new_tokens=MAX_NEW_TOKENS,
                                deadline_seconds=budgets.max_wall_seconds - elapsed)
                meter["input_tokens"] += response["input_tokens"]
                meter["output_tokens"] += response["output_tokens"]
                text = response["text"]
                self.record(f"[host] lap {meter['laps']}: model in={response['input_tokens']} "
                            f"out={response['output_tokens']} {response['latency_ms']:.0f}ms", verbose)

            requested = parse_tool_calls(text)
            messages.append({"role": "assistant", "content": text})
            if not requested:
                answer = text.strip()
                self.record(f"[host] answer: {answer[:300]!r}", verbose)
                break

            for item in requested:
                result, server = self.call(item["name"], item["arguments"], verbose)
                meter["tool_calls"] += 1
                body = tool_text(result) or json.dumps(result.get("structuredContent", {}))
                calls.append({"lap": meter["laps"], "server": server, "tool": item["name"],
                              "arguments": item["arguments"],
                              "is_error": bool(result.get("isError")), "result": body[:600]})
                self.record(f"[host]   tools/call {item['name']} -> server {server} "
                            f"args={json.dumps(item['arguments'])} "
                            f"{'ERROR ' if result.get('isError') else ''}{body[:200]!r}", verbose)
                messages.append({"role": "tool", "name": item["name"],
                                 "content": body[:MAX_TOOL_RESULT_CHARS]})

        return {
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": MODEL_NAME, "config": os.path.relpath(self.config_path, self.root),
            "query": query, "attached": list(attach), "forced_call": forced_call,
            "discovered_tools": sorted(self.tools), "calls": calls, "answer": answer,
            "terminated_by": terminated_by, "meter": meter,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "log": list(self.log),
        }

    def close(self):
        for connection in self.connections.values():
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="MCP host: discover tools, run one query.")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--config", required=True)
    parser.add_argument("--list", action="store_true", help="discovery only: print tools/list")
    parser.add_argument("--attach", action="append", default=[], help="resource URI to attach")
    parser.add_argument("--prompt", help="prompt name, with key=value pairs after it")
    parser.add_argument("--prompt-arg", action="append", default=[])
    parser.add_argument("--out", help="write the trace / inventory JSON here")
    args = parser.parse_args(argv)

    host = Host(args.config)
    try:
        inventory = host.connect(verbose=True)
        if args.list or not (args.query or args.prompt):
            payload = {"config": args.config, "servers": inventory,
                       "tool_count": len(host.tools), "tools": sorted(host.tools)}
            print(json.dumps(payload, indent=2))
        else:
            query = args.query
            if args.prompt:
                prompt_args = dict(pair.split("=", 1) for pair in args.prompt_arg)
                server, query = host.prompt(args.prompt, prompt_args)
                host.record(f"[host] prompt {args.prompt} from {server}", True)
            # Load the weights before the wall clock starts: on this 8 GB
            # machine the load alone can take minutes and is not the loop's.
            import agent_runtime
            agent_runtime.warm_up()
            payload = host.run(query, attach=args.attach, verbose=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
