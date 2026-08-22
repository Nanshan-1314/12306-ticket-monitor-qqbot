#!/usr/bin/env python3
"""Minimal MCP stdio client for mcp-server-12306."""
import json
import subprocess
import sys

SERVER_CMD = ["uvx", "mcp-server-12306"]

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


class MCPClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            SERVER_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._id = 0

    def _next_id(self):
        self._id += 1
        return self._id

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read_message(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def request(self, method, params=None):
        rid = self._next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        while True:
            resp = self._read_message()
            if resp is None:
                raise RuntimeError("server closed stream")
            if resp.get("id") == rid:
                return resp

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def initialize(self):
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pycli", "version": "1.0.0"},
            },
        )
        self.notify("notifications/initialized")

    def list_tools(self):
        r = self.request("tools/list", {})
        return r.get("result", {}).get("tools", [])

    def call_tool(self, name, arguments):
        r = self.request("tools/call", {"name": name, "arguments": arguments})
        if "error" in r:
            return {"__error__": r["error"]}
        result = r.get("result", {})
        parts = []
        for c in result.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
        text = "\n".join(parts)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"__raw__": text}

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass
