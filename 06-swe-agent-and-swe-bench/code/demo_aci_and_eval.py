"""Offline lab for Lesson 6: SWE-agent ACI + SWE-bench evaluation harness.

Runs natively on Windows PowerShell or Linux/WSL with Python 3.10+ stdlib only.
No API key, no Docker, no network required.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from aci_tools import WorkspaceACI
from swebench_harness import INSTANCES, evaluate_prediction


def run_aci_demo() -> None:
    print("=" * 68)
    print("PART 1 | SWE-agent ACI 演示：窗口阅读 + 语法门禁自动回滚 + _state")
    print("=" * 68)
    instance = INSTANCES["memleak__session-cache-001"]
    with TemporaryDirectory(prefix="lesson6-aci-") as tmp:
        work = Path(tmp)
        for rel, content in instance["base_files"].items():
            (work / rel).write_text(content, encoding="utf-8")

        aci = WorkspaceACI(work, window_size=20)
        print("\n[1] search_code('_sessions'):")
        print(aci.search_code("_sessions"))

        print("\n[2] open_file('session_store.py', start_line=1):")
        print(aci.open_file("session_store.py", start_line=1))

        print("\n[3] 故意提交一段缺冒号的语法错误编辑 -> 观察 ACI 自动回滚:")
        bad_edit = (
            "    def create_session(self, sid: str, payload: bytearray) -> None:\n"
            "        self._sessions[sid] = payload\n"
            "        while len(self._sessions) > self.max_sessions\n"
            "            self._sessions.pop(next(iter(self._sessions)))"
        )
        print(aci.edit_lines("session_store.py", 6, 7, bad_edit))
        print("state ->", aci.state())

        print("\n[4] 修正语法后重新编辑 -> 语法门禁通过并落盘:")
        good_edit = (
            "    def create_session(self, sid: str, payload: bytearray) -> None:\n"
            "        self._sessions[sid] = payload\n"
            "        while len(self._sessions) > self.max_sessions:\n"
            "            self._sessions.pop(next(iter(self._sessions)))"
        )
        print(aci.edit_lines("session_store.py", 6, 7, good_edit))
        print("state ->", aci.state())


def run_eval_demo(predictions_path: Path, run_id: str) -> None:
    print("\n" + "=" * 68)
    print("PART 2 | SWE-bench 评测闭环：隐藏 FAIL_TO_PASS + PASS_TO_PASS 独立判分")
    print("=" * 68)
    log_dir = Path("eval_logs") / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    for raw_line in predictions_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        pred = json.loads(raw_line)
        reports.append(evaluate_prediction(pred, log_dir))

    print(f"\n{'instance_id':<30} {'candidate':<28} {'F2P':<6} {'P2P':<6} {'verdict'}")
    print("-" * 96)
    for r in reports:
        f2p = "PASS" if all(v == "PASSED" for v in r["tests_status"]["FAIL_TO_PASS"].values()) else "FAIL"
        p2p = "PASS" if all(v == "PASSED" for v in r["tests_status"]["PASS_TO_PASS"].values()) else "FAIL"
        print(f"{r['instance_id']:<30} {r['model_name_or_path']:<28} {f2p:<6} {p2p:<6} {r['verdict']}")

    resolved_count = sum(1 for r in reports if r["resolved"])
    summary = {
        "run_id": run_id,
        "total_predictions": len(reports),
        "resolved": resolved_count,
        "resolve_rate": round(resolved_count / len(reports), 4) if reports else 0.0,
        "reports": reports,
    }
    results_file = log_dir / "results.json"
    results_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n汇总已写入: {results_file.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lesson 6 offline demo: SWE-agent ACI + SWE-bench harness.")
    parser.add_argument(
        "--predictions",
        type=Path,
        default=Path(__file__).with_name("predictions.jsonl"),
        help="Path to SWE-bench-style predictions.jsonl",
    )
    parser.add_argument("--run-id", default="lesson6-demo", help="Evaluation run_id under eval_logs/")
    args = parser.parse_args()

    run_aci_demo()
    run_eval_demo(args.predictions, args.run_id)


if __name__ == "__main__":
    main()
