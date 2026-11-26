"""用纯文本动作复现 ReAct；默认使用离线剧本，不调用模型 API。"""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE / "workspace"
APP_NAME = "leaky_app.py"
DEFAULT_LESSON1 = HERE.parents[1] / "01-building-effective-agents" / "code"
MAX_STEPS = 8
ACTION_START = re.compile(r"^Action:[ \t]*", re.M)
ACTION_BODY = re.compile(r"([a-z_]+)\[(.*)\]", re.S)
PROMPT = """你要定位并修正工作区 leaky_app.py 的玩具内存泄漏。
每轮输出一行 Thought: 简短的下一步理由，再输出 Action: 工具[参数]，然后停止。
不要编写 Observation:，它只由执行器填写。先检测、读代码，再改动并用相同工具复测。
可用工具：
- check[文件]：运行内存检测。文件名相对于工作区。
- read[文件]：读取工作区中的文件。
- replace[旧文本 ||| 新文本]：只在 leaky_app.py 精确替换一处；可使用实际换行。
- finish[总结]：提交结论，不代表通过平台验收。

格式示例（另一个文件）：
Thought: 先检查示例文件是否通过。
Action: check[example.py]
Observation: 结果: PASS
Thought: 没有复现，不修改文件。
Action: finish[检查未发现泄漏。]

开始处理 leaky_app.py：
"""
MOCK = (
    "Thought: 先复现，拿到分配证据。\nAction: check[leaky_app.py]",
    "Thought: 第 7 行是分配点，不一定是持有者，读代码。\nAction: read[leaky_app.py]",
    "Thought: 无上限全局列表留住了对象，试用有界队列。\n"
    "Action: replace[_cache = [] ||| from collections import deque\n\n_cache = deque(maxlen=100)]",
    "Thought: 用相同负载复测候选版。\nAction: check[workspace/leaky_app.py]",
    "Thought: 复测未发现持续增长，提交结论。\nAction: finish[已将无上限列表改为有界队列，复测 PASS。]",
)
MOCK_LOOP = (
    "Thought: 先读代码。\nAction: read[leaky_app.py]",
    "Thought: 再读一次同一个文件。\nAction: read[leaky_app.py]",
) + ("Thought: 继续检查。\nAction: check[leaky_app.py]",
     "Thought: 回去读原来的代码。\nAction: read[leaky_app.py]") * 3


def make_llm(mode, mock_loop):
    # 让离线剧本和真实模型提供同一个纯文本接口。
    if mode == "mock":
        replies = iter(MOCK_LOOP if mock_loop else MOCK)

        def call_llm(prompt, *, stop):
            return next(replies)
    else:
        # 可选 API 分支未实测；仅在隔离环境中运行模型写入的候选代码。
        from openai import OpenAI

        model = os.environ["REACT_MODEL"]
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                        base_url=os.environ.get("OPENAI_BASE_URL"), timeout=30)

        def call_llm(prompt, *, stop):
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}], stop=stop,
            )
            return response.choices[0].message.content or ""

    return call_llm


def workspace_file(arg):
    # 允许相对工作区的名称，也允许显式写 workspace/ 前缀。
    name = Path(arg)
    if name.parts[:1] == (WORKSPACE.name,):
        name = Path(*name.parts[1:])
    target = (WORKSPACE / name).resolve()
    if name.anchor or WORKSPACE not in target.parents or not target.is_file():
        raise ValueError("只能访问工作区内已有的文件。")
    return target


def check_file(checker, arg):
    # 检测器用独立进程执行，继承 UTF-8 设置，并限制执行时间。
    target = workspace_file(arg)
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(checker), str(target)],
        cwd=WORKSPACE, capture_output=True, encoding="utf-8", errors="replace", timeout=15,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise ValueError(f"检测器退出码 {result.returncode}：{detail}")
    return result.stdout.strip()[-800:]


def run_tool(action, arg, checker):
    if action == "check":
        return check_file(checker, arg)
    if action == "read":
        return workspace_file(arg).read_text(encoding="utf-8")
    if action == "replace":
        old, sep, new = arg.partition(" ||| ")
        if not sep or not old:
            raise ValueError("替换格式应为 replace[旧文本 ||| 新文本]，旧文本不能为空。")
        target = workspace_file(APP_NAME)
        source = target.read_text(encoding="utf-8")
        if source.count(old) != 1:
            raise ValueError("旧文本必须恰好出现一次；请先读文件。")
        target.write_text(source.replace(old, new, 1), encoding="utf-8")
        return "已写入工作区副本。"
    raise ValueError(f"未知工具：{action}")


def run_agent(call_llm, checker):
    trajectory = ""
    previous = None
    for step in range(1, MAX_STEPS + 1):
        # 即使模型服务未遵守 stop，也不采信模型写出的观测。
        reply = call_llm(PROMPT + trajectory, stop=["Observation:"]).split("Observation:", 1)[0].strip()
        starts = list(ACTION_START.finditer(reply))
        match = ACTION_BODY.fullmatch(reply[starts[-1].end():]) if starts else None
        print(f"\n[第{step}步] {reply or '（模型没有输出）'}")
        current = None
        finished = False
        if not match:
            observation = "格式错误；请输出 Action: tool[arg]。"
        else:
            action, raw_arg = match.groups()
            current = (action, raw_arg if action == "replace" else raw_arg.strip())
            if action == "finish":
                observation = "已收到总结；由程序单独复测。"
                finished = True
            elif current == previous:
                observation = "重复动作，换一个假设或 finish"
            else:
                try:
                    observation = run_tool(*current, checker=checker)
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    observation = f"工具错误：{exc}"

        print(f"Observation: {observation}")
        trajectory += f"{reply}\nObservation: {observation}\n"
        previous = current
        if finished:
            # 用真实检测结果验收，不读取模型给出的 PASS。
            try:
                last_line = check_file(checker, APP_NAME).splitlines()[-1:]
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f"平台验收：ERROR（{exc}）")
                return 1
            status = {"结果: PASS": "PASS", "结果: LEAK": "LEAK"}.get(
                last_line[0] if last_line else "", "ERROR"
            )
            print(f"平台验收：{status}")
            return 0 if status == "PASS" else 1

    print(f"\n达到最大步数（{MAX_STEPS}）；未验收。")
    return 1


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lesson1", type=Path, default=DEFAULT_LESSON1,
                        help="第 1 课的 code 目录")
    parser.add_argument("--llm", choices=("mock", "openai"), default="mock",
                        help="默认离线；openai 仅限隔离环境")
    parser.add_argument("--mock-loop", action="store_true", help="演示重复动作与最大步数")
    args = parser.parse_args()
    if args.mock_loop and args.llm != "mock":
        parser.error("--mock-loop 只能用于离线模式。")

    lesson1 = args.lesson1.resolve()
    checker, original = lesson1 / "check_leak.py", lesson1 / APP_NAME
    if not checker.is_file() or not original.is_file():
        parser.error("找不到第 1 课的 check_leak.py 和 leaky_app.py；请用 --lesson1 指定 code 目录。")
    if lesson1 == WORKSPACE or WORKSPACE.is_symlink():
        parser.error("工作区必须是本课目录内的普通目录，不能和第 1 课共用。")
    WORKSPACE.mkdir(exist_ok=True)
    candidate = WORKSPACE / APP_NAME
    candidate.unlink(missing_ok=True)
    shutil.copyfile(original, candidate)
    call_llm = make_llm(args.llm, args.mock_loop)
    return run_agent(call_llm, checker)


if __name__ == "__main__":
    sys.exit(main())
