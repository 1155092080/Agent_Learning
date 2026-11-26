"""用第 1 课的玩具程序演示离线结构化工具调用。"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


LESSON1 = Path(__file__).resolve().parents[2] / "01-building-effective-agents" / "code"
APP_NAME = "leaky_app.py"
MAX_STEPS = 6
FUNCTION_CHECK = """
import runpy
import sys

app = runpy.run_path(sys.argv[1])
cache = app["_cache"]
request = app["handle_request"]
if any(request(i) != 10000 for i in range(100)):
    sys.exit("请求返回值不符合预期")
if len(cache) != 100:
    sys.exit("预期缓存保留 100 个对象")
oldest, newest = cache[0], cache[-1]
if request(100) != 10000 or len(cache) != 100 or cache[-2] is not newest:
    sys.exit("请求或缓存顺序不符合预期")
if any(item is oldest for item in cache) or cache[-1] is newest:
    sys.exit("预期淘汰最旧对象，追加最新对象")
print("请求返回值和缓存淘汰行为通过")
"""


def mock_model(messages, early_finish):
    plan = [
        ("check_leak", {"revision": "baseline"}),
        ("read_source", {}),
        ("apply_fixture", {"fix": "bounded_cache"}),
        ("check_leak", {"revision": "candidate"}),
    ]
    turn = sum(message["role"] == "tool" for message in messages)
    if turn == (1 if early_finish else len(plan)):
        return {"role": "assistant", "content": "结束。按剧本声称已修复，请平台验收。"}
    name, arguments = plan[turn]
    return {"role": "assistant", "tool_calls": [
        {"id": f"call_{turn + 1}", "name": name, "arguments": arguments},
    ]}


def run_python(*args):
    completed = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", *map(str, args)],
        capture_output=True, encoding="utf-8", timeout=15,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise ValueError(f"子进程退出码 {completed.returncode}：{detail}")
    return completed.stdout.strip()


class LeakLab:
    def __init__(self, lesson1, workspace):
        self.baseline = lesson1 / APP_NAME
        self.checker = lesson1 / "check_leak.py"
        self.candidate = workspace / APP_NAME
        self.baseline_status = None
        self.report_count = 0

    def measure(self, revision, path):
        self.report_count += 1
        result = {"revision": revision, "report_id": f"R{self.report_count:02}"}
        try:
            output = run_python(self.checker, path)
            status_line = output.splitlines()[-1:]
            status = {"结果: LEAK": "LEAK", "结果: PASS": "PASS"}.get(
                status_line[0] if status_line else "", "ERROR",
            )
            return {**result, "status": status, "detail": output or "检测器没有输出"}
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            return {**result, "status": "ERROR", "detail": str(error)}

    def dispatch(self, name, arguments):
        if name == "check_leak" and arguments == {"revision": "baseline"}:
            report = self.measure("baseline", self.baseline)
            self.baseline_status = report["status"]
            return report
        if name == "check_leak" and arguments == {"revision": "candidate"}:
            if not self.candidate.is_file():
                raise ValueError("候选不存在，不能复测。")
            return self.measure("candidate", self.candidate)
        if name == "read_source" and arguments == {}:
            return {"status": "OK", "source": self.baseline.read_text(encoding="utf-8")}
        if name == "apply_fixture" and arguments == {"fix": "bounded_cache"}:
            if self.baseline_status != "LEAK":
                raise ValueError("必须先复现泄漏。")
            source = self.baseline.read_text(encoding="utf-8")
            before = "_cache = []"
            if source.count(before) != 1:
                raise ValueError("修复夹具只适用于第 1 课的预置代码。")
            after = "from collections import deque\n\n_cache = deque(maxlen=100)"
            self.candidate.write_text(source.replace(before, after, 1), encoding="utf-8")
            return {"status": "OK", "revision": "candidate"}
        raise ValueError("工具名或参数不在本实验的白名单中。")

    def verify(self):
        if self.baseline_status != "LEAK" or not self.candidate.is_file():
            print("平台验收：NOT_VERIFIED（缺少原版泄漏证据或候选）")
            return 1
        report = self.measure("candidate", self.candidate)
        log("平台独立复测", report)
        if report["status"] != "PASS":
            print(f"平台验收：NOT_VERIFIED（复测 {report['status']}）")
            return 1
        try:
            print(f"功能检查：{run_python('-c', FUNCTION_CHECK, self.candidate)}")
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(f"平台验收：NOT_VERIFIED（功能检查失败：{error}）")
            return 1
        print("平台验收：PASS")
        return 0


def log(label, record):
    print(f"{label} {json.dumps(record, ensure_ascii=False)}")


def run_agent(lab, model):
    messages = [
        {"role": "system", "content": "先用原版复现泄漏；平台独立验收。"},
        {"role": "user", "content": "检查并修复第 1 课的玩具服务。"},
    ]
    for _ in range(MAX_STEPS):
        reply = model(messages)
        messages.append(reply)
        log("assistant", reply)
        calls = reply.get("tool_calls", [])
        if not calls:
            return lab.verify()
        for call in calls:
            try:
                observation = lab.dispatch(call["name"], call["arguments"])
            except (OSError, ValueError) as error:
                observation = {"status": "ERROR", "detail": str(error)}
            result = {"role": "tool", "tool_call_id": call["id"], "content": observation}
            messages.append(result)
            log("tool", result)
    print("平台验收：NOT_VERIFIED（达到最大步数）")
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lesson1", type=Path, default=LESSON1, help="第 1 课的 code 目录")
    parser.add_argument("--early-finish", action="store_true", help="演示没有候选就声称成功")
    args = parser.parse_args()
    lesson1 = args.lesson1.resolve()
    if not all((lesson1 / name).is_file() for name in (APP_NAME, "check_leak.py")):
        parser.error("找不到第 1 课的文件；请用 --lesson1 指定第 1 课的 code 目录。")
    with TemporaryDirectory(prefix="lesson04-") as workspace:
        lab = LeakLab(lesson1, Path(workspace))
        return run_agent(lab, lambda messages: mock_model(messages, args.early_finish))


if __name__ == "__main__":
    sys.exit(main())
