"""Stdio MCP server that gives Hermes MeMyselfAI's context and schedule tools.

Hermes starts this process. Each tool call is forwarded to the running app's loopback
service, found through hermes.json in the workspace data directory.
"""

import argparse
import http.client
import json
import os
from pathlib import Path
import sys

from context import TOOLS

DEFAULT_DATA = Path(os.environ.get("MEMYSELFAI_DATA") or Path.home() / "Library/Application Support/MeMyselfAI")
PROTOCOL = "2025-06-18"


def forward(data, name, arguments):
    try:
        info = json.loads((data / "hermes.json").read_text())
        connection = http.client.HTTPConnection("127.0.0.1", int(info["port"]), timeout=30)
        body = json.dumps({"name": name, "arguments": arguments})
        connection.request(
            "POST",
            "/hermes/tool",
            body,
            {
                "Host": f"127.0.0.1:{info['port']}",
                "Authorization": "Bearer " + info["token"],
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read() or b"{}")
    except (OSError, ValueError, KeyError):
        return None, "MeMyselfAI is not running. Open the app and retry."
    if response.status == 403:
        return None, "MeMyselfAI rejected the workspace token. Run scripts/connect-hermes.sh again."
    if response.status != 200:
        return None, payload.get("error", "The tool failed.")
    return payload["result"], None


def handle(data, message):
    method = message.get("method")
    if "id" not in message:
        return None  # Notifications such as notifications/initialized need no reply.
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        reply["result"] = {
            "protocolVersion": requested or PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "memyselfai", "version": "1"},
        }
    elif method == "ping":
        reply["result"] = {}
    elif method == "tools/list":
        reply["result"] = {
            "tools": [
                {"name": name, "description": description, "inputSchema": schema}
                for name, (description, schema) in TOOLS.items()
            ]
        }
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        if name not in TOOLS:
            reply["error"] = {"code": -32602, "message": f"Unknown tool: {name}"}
            return reply
        result, error = forward(data, name, params.get("arguments") or {})
        text = error or json.dumps(result, ensure_ascii=False)
        reply["result"] = {"content": [{"type": "text", "text": text}], "isError": bool(error)}
    else:
        reply["error"] = {"code": -32601, "message": f"Method not found: {method}"}
    return reply


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    data = parser.parse_args().data
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(data, message) if isinstance(message, dict) else None
        if reply:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
