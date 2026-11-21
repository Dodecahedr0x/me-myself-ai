"""Read completed exchanges from explicitly enabled desktop chat sources."""

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

NAMES = {"claude": "Claude", "chatgpt": "ChatGPT"}
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
TAIL = 2 * 1024 * 1024
EXPORT_LIMIT = 64 * 1024 * 1024
EXPORT_NAME = re.compile(r"conversations(?:[-_]\d+)?\.json")


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo else 0
        except (ValueError, OverflowError, OSError):
            return 0
    return value if type(value) in (int, float) and math.isfinite(value) else 0


def text_blocks(value, kind="text"):
    if isinstance(value, str):
        return value.strip()[:1200]
    if not isinstance(value, list):
        return ""
    return "\n".join(
        b["text"][:1200]
        for b in value
        if isinstance(b, dict) and b.get("type") == kind and isinstance(b.get("text"), str)
    ).strip()[:1200]


def exchange(provider, conversation, message, when, prompt, answer, start, end):
    if not (start <= when <= end and conversation and message and prompt and answer):
        return None
    ident = hashlib.sha256(json.dumps([provider, conversation, message]).encode()).hexdigest()
    detail = "You: " + prompt[:650] + "\nAssistant: " + answer[:850]
    return dict(
        id=ident,
        source=NAMES[provider],
        chat_source=provider,
        at=when,
        title=prompt.splitlines()[0][:140],
        detail=detail,
        generation=hashlib.sha256(detail.encode()).hexdigest(),
    )


def open_regular(path):
    if path.resolve() != path:
        raise ValueError("A linked chat file was skipped.")
    stream = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ValueError("Expected a regular chat file.")
    return stream


def load_json(path, limit):
    with open_regular(path) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            raise ValueError("A chat file exceeds the scan size limit.")
        return json.loads(stream.read(limit + 1))


def recent_paths(paths, start, limit, gaps):
    dated = []
    for path in paths:
        try:
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_mtime >= start:
                dated.append((info.st_mtime, path))
        except OSError:
            gaps.add("Some session files could not be read.")
    dated.sort(reverse=True)
    if len(dated) > limit:
        gaps.add("Only the most recently changed sessions were scanned.")
    return [p for _, p in dated[:limit]]


def tail_records(stream, gaps):
    size = os.fstat(stream.fileno()).st_size
    if size > TAIL:
        stream.seek(size - TAIL)
        stream.readline(TAIL)
        gaps.add("Long sessions were sampled from the end.")
    for line in stream.read(TAIL).splitlines():
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if isinstance(record, dict):
            yield record


def claude_turns(records, session, start, end):
    prompt = None
    for r in records:
        if (
            r.get("sessionId") != session
            or r.get("isSidechain")
            or r.get("isMeta")
            or r.get("isCompactSummary")
            or r.get("entrypoint") not in (None, "claude-desktop")
        ):
            continue
        msg = r.get("message") or {}
        if r.get("type") == "user":
            origin = (r.get("origin") or {}).get("kind")
            if origin not in (None, "human") or r.get("turnOrigin") not in (None, "human"):
                continue
            blocks = msg.get("content")
            if isinstance(blocks, list) and any(
                b.get("type") == "tool_result" for b in blocks if isinstance(b, dict)
            ):
                continue
            value = text_blocks(blocks)
            if value and (origin == "human" or not value.startswith("<")):
                prompt = (r.get("uuid"), timestamp(r.get("timestamp")), value)
        elif prompt and r.get("type") == "assistant" and msg.get("stop_reason") == "end_turn":
            answer = text_blocks(msg.get("content"))
            if answer and start <= timestamp(r.get("timestamp")) <= end:
                row = exchange("claude", session, *prompt, answer, start, end)
                if row:
                    yield row
                prompt = None


def chatgpt_turns(records, session, start, end):
    prompt = None
    for r in records:
        p = r.get("payload") or {}
        if r.get("type") == "event_msg":
            if p.get("type") in ("task_started", "turn_aborted"):
                prompt = None
            elif p.get("type") == "user_message" and isinstance(p.get("message"), str):
                prompt = (
                    p.get("turn_id") or r.get("timestamp"),
                    timestamp(r.get("timestamp")),
                    p["message"].strip(),
                )
            elif p.get("type") == "task_complete" and prompt:
                reply = p.get("last_agent_message")
                if isinstance(reply, str) and start <= timestamp(r.get("timestamp")) <= end:
                    row = exchange("chatgpt", session, *prompt, reply, start, end)
                    if row:
                        yield row
                prompt = None
        elif r.get("type") == "response_item" and p.get("role") == "user":
            meta = p.get("internal_chat_message_metadata_passthrough") or {}
            kinds = meta.get("content_item_kinds") or []
            items = p.get("content") or []
            if len(kinds) != len(items):
                continue
            attributes = meta.get("content_item_metadata")
            if attributes is not None and len(attributes) != len(items):
                continue
            user = [
                item
                for i, (kind, item) in enumerate(zip(kinds, items))
                if kind == "user.text"
                and (
                    attributes is None
                    or ((attributes[i] or {}).get("provenance") or {}).get("type") == "user"
                )
            ]
            value = text_blocks(user, "input_text")
            if value:
                prompt = (
                    meta.get("turn_id") or r.get("timestamp"),
                    timestamp(meta.get("create_time", r.get("timestamp"))),
                    value,
                )


def live(provider, home, start, end, gaps):
    if provider == "claude":
        index = home / "Library/Application Support/Claude/claude-code-sessions"
        projects = home / ".claude/projects"
        if not index.is_dir() or not projects.is_dir():
            gaps.add("No local Claude Desktop Code sessions found.")
            return
        paths = recent_paths(index.glob("*/*/local_*.json"), start, 48, gaps)
    else:
        index = home / ".codex/sessions"
        if not index.is_dir():
            gaps.add("No local ChatGPT desktop work sessions found.")
            return
        paths = recent_paths(index.glob("*/*/*/rollout-*.jsonl"), start, 96, gaps)
    for path in paths:
        try:
            if provider == "claude":
                metadata = load_json(path, 2 * 1024 * 1024)
                session = metadata.get("cliSessionId")
                if not isinstance(session, str) or not UUID.fullmatch(session):
                    continue
                files = list(projects.glob("*/" + session + ".jsonl"))
                if len(files) != 1:
                    gaps.add("Some indexed sessions have no unique local transcript.")
                    continue
                with open_regular(files[0]) as stream:
                    yield from claude_turns(tail_records(stream, gaps), session, start, end)
            else:
                with open_regular(path) as stream:
                    # Desktop, terminal and subagent rollouts share a directory.
                    header = json.loads(stream.readline(256 * 1024))
                    meta = header.get("payload") or {}
                    if (
                        header.get("type") != "session_meta"
                        or meta.get("originator") != "Codex Desktop"
                        or meta.get("source") not in ("vscode", "desktop", "appServer", "app_server")
                    ):
                        continue
                    yield from chatgpt_turns(tail_records(stream, gaps), meta.get("id"), start, end)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            gaps.add("Some local session files could not be read.")


def exported_messages(provider, conv):
    if provider == "claude":
        for m in conv["chat_messages"]:
            yield (
                m.get("sender"),
                m.get("uuid"),
                timestamp(m.get("created_at")),
                text_blocks(m.get("content") or m.get("text")),
            )
    else:
        mapping = conv["mapping"]
        key = conv.get("current_node")
        seen = set()
        branch = []
        while key:
            if key in seen or key not in mapping:
                raise ValueError("Incomplete conversation branch.")
            seen.add(key)
            node = mapping[key]
            m = node.get("message") or {}
            if m and not (m.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
                content = m.get("content") or {}
                if (
                    content.get("content_type") == "text"
                    and m.get("recipient", "all") == "all"
                    and m.get("channel") not in ("analysis", "commentary")
                ):
                    body = "\n".join(
                        p[:1200] for p in content.get("parts", []) if isinstance(p, str)
                    ).strip()[:1200]
                    branch.append(
                        (
                            (m.get("author") or {}).get("role"),
                            m.get("id") or key,
                            timestamp(m.get("create_time")),
                            body,
                        )
                    )
            key = node.get("parent")
        yield from reversed(branch)


def exports(provider, folder, start, end, gaps):
    root = Path(folder)
    if not root.is_dir() or root.resolve() != root:
        gaps.add("The selected export folder is unavailable.")
        return
    files = sorted(p for p in root.glob("conversations*.json") if EXPORT_NAME.fullmatch(p.name))
    if not files:
        gaps.add("Choose the extracted folder containing conversations.json or its numbered parts.")
    for path in files:
        try:
            records = load_json(path, EXPORT_LIMIT)
            if not isinstance(records, list):
                raise ValueError()
            for conv in records:
                if (
                    not isinstance(conv, dict)
                    or ("chat_messages" if provider == "claude" else "mapping") not in conv
                ):
                    gaps.add("The export contains an unrecognized conversation format.")
                    continue
                session = conv.get("uuid") or conv.get("conversation_id") or conv.get("id")
                prompt = None
                for role, ident, when, body in exported_messages(provider, conv):
                    if role in ("human", "user"):
                        prompt = (ident, when, body)
                    elif role == "assistant" and prompt and body and start <= when <= end:
                        row = exchange(provider, session, *prompt, body, start, end)
                        if row:
                            yield row
                        prompt = None
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError):
            gaps.add("Some exported conversations could not be read.")


def collect(provider, settings, start, end, home=None):
    gaps = set()
    rows = list(live(provider, home or Path.home(), start, end, gaps))
    if settings.get("folder"):
        rows.extend(exports(provider, settings["folder"], start, end, gaps))
    latest = sorted(rows, key=lambda r: (r["at"], r["id"]), reverse=True)[:16]
    for row in latest:
        row["chat_revision"] = settings["revision"]
    return latest, [NAMES[provider] + ": " + gap for gap in sorted(gaps)]
