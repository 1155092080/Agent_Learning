"""Minimal SWE-agent-style ACI (Agent-Computer Interface) toolkit.

Demonstrates four core ACI ideas from SWE-agent (Yang et al., 2024):
1. Windowed file viewer with line numbers and remaining-line hints.
2. Concise, capped code search so observations do not flood context.
3. Line-range editor with a built-in syntax linter that automatically rolls back
   broken Python edits before they ever reach expensive runtime tests.
4. Compact _state() feedback appended after actions (open file, window, cwd).
"""

from __future__ import annotations

import ast
from pathlib import Path


class WorkspaceACI:
    def __init__(self, root: Path, window_size: int = 25) -> None:
        self.root = root.resolve()
        self.window_size = window_size
        self.current_file: str | None = None
        self.window_start: int = 1
        self.last_edit_status: str = "none"

    def _resolve(self, rel_path: str) -> Path:
        target = (self.root / rel_path).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"Path escapes workspace: {rel_path}")
        return target

    def open_file(self, rel_path: str, start_line: int = 1, window: int | None = None) -> str:
        target = self._resolve(rel_path)
        lines = target.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        win = window or self.window_size
        start = max(1, min(start_line, max(1, total)))
        end = min(total, start + win - 1)

        self.current_file = rel_path
        self.window_start = start

        header = f"[File: {rel_path} ({total} lines total)]"
        above = f"({start - 1} more lines above)" if start > 1 else "(top of file)"
        body = [f"{i:4d}: {lines[i - 1]}" for i in range(start, end + 1)]
        below = f"({total - end} more lines below)" if end < total else "(end of file)"
        return "\n".join([header, above, *body, below])

    def search_code(self, query: str, max_hits: int = 10) -> str:
        hits: list[str] = []
        for path in sorted(self.root.rglob("*.py")):
            rel = path.relative_to(self.root).as_posix()
            for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if query in line:
                    hits.append(f"{rel}:{idx}: {line.strip()}")
                    if len(hits) >= max_hits:
                        hits.append(f"... (capped at {max_hits} hits; narrow your query)")
                        return "\n".join(hits)
        return "\n".join(hits) if hits else f"No matches for {query!r}."

    def edit_lines(self, rel_path: str, start_line: int, end_line: int, replacement: str) -> str:
        target = self._resolve(rel_path)
        original_text = target.read_text(encoding="utf-8")
        lines = original_text.splitlines()
        if start_line < 1 or end_line < start_line or end_line > len(lines):
            self.last_edit_status = "range_error"
            return f"EDIT REJECTED: invalid range {start_line}:{end_line} (file has {len(lines)} lines)."

        new_block = replacement.splitlines()
        updated_lines = lines[: start_line - 1] + new_block + lines[end_line:]
        updated_text = "\n".join(updated_lines) + "\n"

        # SWE-agent ACI guardrail: lint before committing the edit.
        try:
            ast.parse(updated_text, filename=rel_path)
        except SyntaxError as exc:
            self.last_edit_status = "rolled_back_syntax_error"
            return (
                f"LINT FAILED -> EDIT ROLLED BACK ({rel_path} unchanged)\n"
                f"SyntaxError at line {exc.lineno}: {exc.msg}\n"
                f"Fix the syntax and retry edit_lines."
            )

        target.write_text(updated_text, encoding="utf-8")
        self.last_edit_status = "applied"
        preview_start = max(1, start_line - 2)
        return "EDIT APPLIED (lint passed)\n" + self.open_file(rel_path, start_line=preview_start, window=15)

    def state(self) -> dict[str, object]:
        return {
            "open_file": self.current_file or "n/a",
            "window_start": self.window_start,
            "last_edit_status": self.last_edit_status,
        }
