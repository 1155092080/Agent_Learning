"""Minimal SWE-bench-style offline evaluation harness for memory-leak tasks.

Each instance mirrors the SWE-bench schema:
- base_files: snapshot at base_commit (visible to the agent)
- problem_statement: issue description (visible to the agent)
- test_files: hidden test_patch injected ONLY during evaluation
- fail_to_pass: tests that fail on base_commit and must pass after model_patch
- pass_to_pass: regression tests that pass on base_commit and must stay passing

An instance is RESOLVED only if:
1. model_patch applies cleanly to a fresh copy of base_files;
2. every FAIL_TO_PASS test passes; AND
3. every PASS_TO_PASS test passes.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

INSTANCES: dict[str, dict] = {
    "memleak__session-cache-001": {
        "instance_id": "memleak__session-cache-001",
        "repo": "demo/session-service",
        "base_commit": "a1b2c3d",
        "problem_statement": (
            "SessionStore._sessions grows without bound after repeated logins, "
            "causing tracemalloc growth > 512 KiB over 200 sessions. "
            "Cap stored sessions to max_sessions (default 32) by evicting the oldest "
            "sessions while keeping recent sessions retrievable via get_session()."
        ),
        "base_files": {
            "session_store.py": (
                "class SessionStore:\n"
                "    def __init__(self, max_sessions: int = 32) -> None:\n"
                "        self.max_sessions = max_sessions\n"
                "        self._sessions: dict[str, bytearray] = {}\n"
                "\n"
                "    def create_session(self, sid: str, payload: bytearray) -> None:\n"
                "        self._sessions[sid] = payload\n"
                "\n"
                "    def get_session(self, sid: str) -> bytearray | None:\n"
                "        return self._sessions.get(sid)\n"
                "\n"
                "    def active_count(self) -> int:\n"
                "        return len(self._sessions)\n"
            ),
        },
        "test_files": {
            "test_swebench.py": (
                "import tracemalloc\n"
                "import unittest\n"
                "from session_store import SessionStore\n"
                "\n"
                "class TestSessionStore(unittest.TestCase):\n"
                "    def test_f2p_memory_bounded(self):\n"
                "        store = SessionStore(max_sessions=32)\n"
                "        tracemalloc.start()\n"
                "        before, _ = tracemalloc.get_traced_memory()\n"
                "        for i in range(200):\n"
                "            store.create_session(f's-{i}', bytearray(4096))\n"
                "        current, _ = tracemalloc.get_traced_memory()\n"
                "        tracemalloc.stop()\n"
                "        growth = current - before\n"
                "        self.assertLessEqual(store.active_count(), 32)\n"
                "        self.assertLess(growth, 256 * 1024)\n"
                "\n"
                "    def test_p2p_recent_session_readable(self):\n"
                "        store = SessionStore(max_sessions=32)\n"
                "        payload = bytearray(b'hello-session')\n"
                "        store.create_session('recent', payload)\n"
                "        self.assertEqual(store.get_session('recent'), payload)\n"
                "        self.assertIsNone(store.get_session('missing'))\n"
            ),
        },
        "fail_to_pass": ["test_f2p_memory_bounded"],
        "pass_to_pass": ["test_p2p_recent_session_readable"],
    },
    "memleak__event-bus-002": {
        "instance_id": "memleak__event-bus-002",
        "repo": "demo/event-bus",
        "base_commit": "e4f5g6h",
        "problem_statement": (
            "EventBus.unsubscribe() does not remove handlers, so closures holding "
            "buffers stay referenced forever. Fix unsubscribe() without breaking "
            "delivery to active subscribers."
        ),
        "base_files": {
            "event_bus.py": (
                "from collections.abc import Callable\n"
                "\n"
                "class EventBus:\n"
                "    def __init__(self) -> None:\n"
                "        self._listeners: dict[str, list[Callable]] = {}\n"
                "\n"
                "    def subscribe(self, topic: str, handler: Callable) -> None:\n"
                "        self._listeners.setdefault(topic, []).append(handler)\n"
                "\n"
                "    def unsubscribe(self, topic: str, handler: Callable) -> None:\n"
                "        # Bug: forgets to remove handler from self._listeners[topic]\n"
                "        _ = (topic, handler)\n"
                "\n"
                "    def publish(self, topic: str, data: object) -> None:\n"
                "        for fn in list(self._listeners.get(topic, [])):\n"
                "            fn(data)\n"
                "\n"
                "    def listener_count(self, topic: str) -> int:\n"
                "        return len(self._listeners.get(topic, []))\n"
            ),
        },
        "test_files": {
            "test_swebench.py": (
                "import tracemalloc\n"
                "import unittest\n"
                "from event_bus import EventBus\n"
                "\n"
                "class TestEventBus(unittest.TestCase):\n"
                "    def test_f2p_unsubscribe_releases_memory(self):\n"
                "        bus = EventBus()\n"
                "        tracemalloc.start()\n"
                "        before, _ = tracemalloc.get_traced_memory()\n"
                "        for _ in range(200):\n"
                "            buf = bytearray(4096)\n"
                "            handler = lambda x, b=buf: len(b)\n"
                "            bus.subscribe('tick', handler)\n"
                "            bus.unsubscribe('tick', handler)\n"
                "        current, _ = tracemalloc.get_traced_memory()\n"
                "        tracemalloc.stop()\n"
                "        self.assertEqual(bus.listener_count('tick'), 0)\n"
                "        self.assertLess(current - before, 128 * 1024)\n"
                "\n"
                "    def test_p2p_publish_delivers_in_order(self):\n"
                "        bus = EventBus()\n"
                "        seen = []\n"
                "        bus.subscribe('tick', lambda x: seen.append(('a', x)))\n"
                "        bus.subscribe('tick', lambda x: seen.append(('b', x)))\n"
                "        bus.publish('tick', 42)\n"
                "        self.assertEqual(seen, [('a', 42), ('b', 42)])\n"
            ),
        },
        "fail_to_pass": ["test_f2p_unsubscribe_releases_memory"],
        "pass_to_pass": ["test_p2p_publish_delivers_in_order"],
    },
}

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_unified_diff(root: Path, patch_text: str) -> None:
    """Pure-Python unified diff applier (no external git/patch binary needed)."""
    if not patch_text.strip():
        raise ValueError("Empty model_patch")
    lines = patch_text.splitlines()
    idx = 0
    applied_any = False
    while idx < len(lines):
        if lines[idx].startswith("--- ") and idx + 1 < len(lines) and lines[idx + 1].startswith("+++ "):
            rel = lines[idx + 1][4:].strip()
            if rel.startswith("b/"):
                rel = rel[2:]
            target = (root / rel).resolve()
            if not str(target).startswith(str(root.resolve())) or not target.exists():
                raise ValueError(f"Patch target missing or invalid: {rel}")
            orig = target.read_text(encoding="utf-8").splitlines()
            idx += 2
            out: list[str] = []
            cursor = 0
            while idx < len(lines) and not lines[idx].startswith("--- "):
                m = _HUNK_RE.match(lines[idx])
                if not m:
                    idx += 1
                    continue
                old_start = int(m.group(1))
                idx += 1
                while cursor < old_start - 1:
                    out.append(orig[cursor])
                    cursor += 1
                while idx < len(lines) and not lines[idx].startswith(("@@ ", "--- ", "diff ")):
                    tag = lines[idx][:1]
                    text = lines[idx][1:]
                    if tag == " ":
                        if cursor >= len(orig) or orig[cursor] != text:
                            raise ValueError(f"Context mismatch in {rel} at line {cursor + 1}")
                        out.append(orig[cursor])
                        cursor += 1
                    elif tag == "-":
                        if cursor >= len(orig) or orig[cursor] != text:
                            raise ValueError(f"Delete mismatch in {rel} at line {cursor + 1}")
                        cursor += 1
                    elif tag == "+":
                        out.append(text)
                    idx += 1
                applied_any = True
            out.extend(orig[cursor:])
            target.write_text("\n".join(out) + "\n", encoding="utf-8")
        else:
            idx += 1
    if not applied_any:
        raise ValueError("No unified diff hunks found in model_patch")


def _parse_unittest_output(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in output.splitlines():
        m = re.match(r"^(test_\w+)\s+\([^)]+\)\s+\.\.\.\s+(ok|FAIL|ERROR)", line.strip())
        if m:
            statuses[m.group(1)] = "PASSED" if m.group(2) == "ok" else "FAILED"
    return statuses


def evaluate_prediction(prediction: dict, log_dir: Path) -> dict:
    instance_id = prediction["instance_id"]
    model_name = prediction.get("model_name_or_path", "unknown")
    model_patch = prediction.get("model_patch", "")
    instance = INSTANCES[instance_id]

    inst_log_dir = log_dir / f"{instance_id}__{model_name}"
    inst_log_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="swebench-eval-") as tmp:
        work = Path(tmp)
        for rel, content in instance["base_files"].items():
            p = work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        try:
            apply_unified_diff(work, model_patch)
            patch_applied = True
            patch_error = ""
        except Exception as exc:
            patch_applied = False
            patch_error = str(exc)

        test_output = ""
        test_statuses: dict[str, str] = {}
        if patch_applied:
            for rel, content in instance["test_files"].items():
                (work / rel).write_text(content, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "-v", "test_swebench.py"],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=15,
            )
            test_output = (proc.stdout + "\n" + proc.stderr).strip()
            test_statuses = _parse_unittest_output(test_output)

    f2p_results = {t: test_statuses.get(t, "FAILED") for t in instance["fail_to_pass"]}
    p2p_results = {t: test_statuses.get(t, "FAILED") for t in instance["pass_to_pass"]}
    f2p_ok = patch_applied and all(v == "PASSED" for v in f2p_results.values())
    p2p_ok = patch_applied and all(v == "PASSED" for v in p2p_results.values())
    resolved = f2p_ok and p2p_ok

    if not patch_applied:
        verdict = "PATCH_APPLY_FAILED"
    elif resolved:
        verdict = "RESOLVED"
    elif f2p_ok and not p2p_ok:
        verdict = "UNRESOLVED_REGRESSION"
    else:
        verdict = "UNRESOLVED_STILL_LEAKING"

    report = {
        "instance_id": instance_id,
        "model_name_or_path": model_name,
        "patch_applied": patch_applied,
        "patch_error": patch_error,
        "resolved": resolved,
        "verdict": verdict,
        "tests_status": {
            "FAIL_TO_PASS": f2p_results,
            "PASS_TO_PASS": p2p_results,
        },
    }
    (inst_log_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (inst_log_dir / "test_output.txt").write_text(test_output or patch_error, encoding="utf-8")
    return report
