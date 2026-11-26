"""最小可运行的“修内存泄漏”Agent：LLM + 工具 + 循环。

  python mini_agent.py --mock           # 不需要 API Key，用脚本化的假 LLM 演示循环
  python mini_agent.py                  # 自动检测 GEMINI_API_KEY（或 ANTHROPIC_API_KEY）
  python mini_agent.py --gemini         # 强制使用 Google AI Studio (Gemini)
  python mini_agent.py --model gemini-3.8-flash  # 指定模型名
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.join(HERE, "workspace")          # 沙箱：Agent 只能动这里
TARGET = os.path.join(WORKSPACE, "leaky_app.py")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TURNS = 10                                       # 停止条件：最多循环 10 轮

SYSTEM = (
    "你是一个修复 Python 内存泄漏的工程师。"
    "先运行检测器拿到证据，再读代码定位原因，然后写入修复后的完整文件，"
    "最后再次运行检测器确认结果为 PASS 后，用一两句话总结你改了什么。"
)

# ---------- 工具定义（ACI：写给模型看的“说明书”） ----------
TOOLS = [
    {
        "name": "run_leak_check",
        "description": "对 workspace 里的 leaky_app.py 运行内存泄漏检测。"
                       "返回内存增长量、增长最多的代码行（文件:行号），以及结论 LEAK 或 PASS。无需参数。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "读取一个文本文件的完整内容。path 必须是绝对路径，且位于 workspace 目录内。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件的绝对路径"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "用 content 整体覆盖写入文件（不是 diff，是完整的新文件内容）。"
                       "path 必须是绝对路径，且位于 workspace 目录内。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件的绝对路径"},
                "content": {"type": "string", "description": "完整的新文件内容"},
            },
            "required": ["path", "content"],
        },
    },
]


def _check_path(path):
    if not os.path.isabs(path):
        raise ValueError(f"必须使用绝对路径，收到: {path}")
    abs_path = os.path.abspath(path)
    if os.path.normcase(os.path.commonpath([abs_path, WORKSPACE])) != os.path.normcase(WORKSPACE):
        raise ValueError(f"只能访问 workspace 目录: {WORKSPACE}")
    return abs_path


def run_tool(name, args):
    """执行工具，返回给模型看的字符串。出错也返回字符串，让模型能自我纠正。"""
    try:
        if name == "run_leak_check":
            r = subprocess.run([sys.executable, os.path.join(HERE, "check_leak.py"), TARGET],
                               capture_output=True, text=True, timeout=60)
            return (r.stdout + r.stderr)[-3000:]          # 截断：别把海量日志塞给模型
        if name == "read_file":
            with open(_check_path(args["path"]), encoding="utf-8") as f:
                return f.read()
        if name == "write_file":
            with open(_check_path(args["path"]), "w", encoding="utf-8") as f:
                f.write(args["content"])
            return "写入成功"
        return f"未知工具: {name}"
    except Exception as e:  # noqa: BLE001
        return f"工具出错: {e}"


# ---------- Agent 循环 ----------
def agent_loop(client, task, model):
    messages = [{"role": "user", "content": task}]
    for turn in range(1, MAX_TURNS + 1):
        resp = client.messages.create(model=model, max_tokens=4096, system=SYSTEM,
                                      tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                print(f"\n[第{turn}轮 LLM 说] {block.text}")
        if resp.stop_reason != "tool_use":          # 模型不再调用工具 -> 认为任务完成
            return
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                print(f"[第{turn}轮 调用工具] {block.name} {json.dumps(block.input, ensure_ascii=False)[:120]}")
                out = run_tool(block.name, block.input)
                print(f"[工具返回] {out[:300]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})  # 环境反馈喂回给模型
    print(f"\n达到最大轮数 {MAX_TURNS}，强制停止。")


# ---------- Gemini REST 客户端（纯标准库，无需额外 pip install） ----------
class GeminiClient:
    """调用 Google AI Studio (Gemini) GenerateContent REST API，并适配上面的 agent_loop 接口。"""

    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key, api_base=None):
        self.api_key = api_key
        if api_base:
            self.API_BASE = api_base.rstrip("/")
        self.messages = self
        self._raw_contents = {}  # 保存模型原始 content（含 thoughtSignature），原样回传
        self._call_info = {}     # tool_use_id -> (function_name, original_id)

    def _to_gemini_contents(self, messages):
        contents = []
        for msg in messages:
            role, content = msg["role"], msg["content"]
            if role == "assistant":
                contents.append(self._raw_contents[id(content)])
            elif isinstance(content, str):
                contents.append({"role": "user", "parts": [{"text": content}]})
            else:
                parts = []
                for item in content:
                    name, orig_id = self._call_info[item["tool_use_id"]]
                    fr = {"name": name, "response": {"result": item["content"]}}
                    if orig_id:
                        fr["id"] = orig_id
                    parts.append({"functionResponse": fr})
                contents.append({"role": "user", "parts": parts})
        return contents

    @staticmethod
    def _to_gemini_tools(tools):
        decls = []
        for t in tools:
            decl = {"name": t["name"], "description": t["description"]}
            if t["input_schema"].get("properties"):
                decl["parameters"] = t["input_schema"]
            decls.append(decl)
        return [{"functionDeclarations": decls}]

    def create(self, *, model, system, tools, messages, **_kwargs):
        url = f"{self.API_BASE}/{model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._to_gemini_contents(messages),
            "tools": self._to_gemini_tools(tools),
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            hint = ""
            if e.code in (401, 403):
                hint = "\n提示：请检查 GEMINI_API_KEY 是否正确、是否有效。"
            elif e.code == 404:
                hint = f"\n提示：模型 {model!r} 不可用，可用 --model 指定其他模型（例如 gemini-3.8-flash）。"
            elif e.code == 429:
                hint = "\n提示：触发了频率/配额限制，稍等片刻再试或切换模型。"
            raise RuntimeError(f"Gemini API 请求失败 (HTTP {e.code}): {body}{hint}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"无法连接到 Gemini API: {e.reason}\n"
                "提示：请检查网络，若使用代理请在终端设置 HTTPS_PROXY 环境变量。"
            ) from e

        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini 未返回候选结果: {json.dumps(data, ensure_ascii=False)}")

        raw_content = candidates[0].get("content") or {"role": "model", "parts": []}
        blocks = []
        for part in raw_content.get("parts") or []:
            if "text" in part and not part.get("thought"):
                blocks.append(SimpleNamespace(type="text", text=part["text"]))
            if "functionCall" in part:
                fc = part["functionCall"]
                orig_id = fc.get("id")
                call_id = orig_id or f"gemini_call_{len(self._call_info)}"
                self._call_info[call_id] = (fc["name"], orig_id)
                blocks.append(SimpleNamespace(
                    type="tool_use",
                    id=call_id,
                    name=fc["name"],
                    input=fc.get("args") or {},
                ))
        self._raw_contents[id(blocks)] = raw_content
        has_tool = any(b.type == "tool_use" for b in blocks)
        return SimpleNamespace(stop_reason="tool_use" if has_tool else "end_turn", content=blocks)


# ---------- 假 LLM：按剧本返回，用来在没有 API Key 时看清循环 ----------
FIXED_CODE = '''"""修复后的玩具服务。"""

from collections import deque

_cache = deque(maxlen=100)  # 只保留最近 100 个请求的数据，旧的自动丢弃


def handle_request(request_id: int) -> int:
    data = bytearray(10_000)
    _cache.append(data)
    return len(data)


def main(n: int = 2000) -> None:
    for i in range(n):
        handle_request(i)


if __name__ == "__main__":
    main()
'''


class MockClient:
    def __init__(self):
        def tool(name, inp, text):
            return SimpleNamespace(stop_reason="tool_use", content=[
                SimpleNamespace(type="text", text=text),
                SimpleNamespace(type="tool_use", id=f"t_{name}_{len(inp)}", name=name, input=inp)])
        self.script = iter([
            tool("run_leak_check", {}, "先跑检测器，拿到证据。"),
            tool("read_file", {"path": TARGET}, "增长集中在 leaky_app.py 的 bytearray 那行，读代码看看。"),
            tool("write_file", {"path": TARGET, "content": FIXED_CODE},
                 "_cache 是只增不减的全局列表，改成有上限的 deque。"),
            tool("run_leak_check", {}, "修好了，再检测一次确认。"),
            SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(
                type="text", text="检测结果 PASS：把无上限的全局列表 _cache 改成了 deque(maxlen=100)。")]),
        ])
        self.messages = self

    def create(self, **kwargs):
        return next(self.script)


def _select_client(args):
    if args.mock:
        return MockClient(), "mock", "Mock（假 LLM 剧本）"

    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if args.gemini or (not args.anthropic and gemini_key):
        if not gemini_key:
            sys.exit(
                "未检测到环境变量 GEMINI_API_KEY。\n"
                "请先在终端设置你的 Google AI Studio API Key，再运行：\n"
                "  PowerShell: $env:GEMINI_API_KEY=\"你的key\"\n"
                "  CMD:        set GEMINI_API_KEY=你的key\n"
                "  macOS/Linux: export GEMINI_API_KEY=\"你的key\"\n"
                "或者运行不需要 Key 的演示模式：python mini_agent.py --mock"
            )
        model = args.model or GEMINI_MODEL
        return GeminiClient(gemini_key), model, f"Gemini ({model})"

    if args.anthropic or anthropic_key:
        if not anthropic_key:
            sys.exit("未检测到环境变量 ANTHROPIC_API_KEY。")
        import anthropic
        model = args.model or ANTHROPIC_MODEL
        return anthropic.Anthropic(), model, f"Anthropic ({model})"

    sys.exit(
        "未检测到 API Key（GEMINI_API_KEY 或 ANTHROPIC_API_KEY）。\n"
        "使用 Google AI Studio (Gemini) 只需设置环境变量（无需 pip install）：\n"
        "  PowerShell: $env:GEMINI_API_KEY=\"你的key\"\n"
        "  CMD:        set GEMINI_API_KEY=你的key\n"
        "  python mini_agent.py\n"
        "或者运行不需要 Key 的演示模式：python mini_agent.py --mock"
    )


def main():
    parser = argparse.ArgumentParser(description="最小可运行的修内存泄漏 Agent")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true", help="使用剧本化的假 LLM（无需 API Key）")
    group.add_argument("--gemini", action="store_true", help="强制使用 Google AI Studio (Gemini)")
    group.add_argument("--anthropic", action="store_true", help="强制使用 Anthropic (Claude)")
    parser.add_argument("--model", help="覆盖默认模型名（也可用 GEMINI_MODEL / ANTHROPIC_MODEL 环境变量）")
    args = parser.parse_args()

    client, model, label = _select_client(args)
    print(f"[运行模式] {label}")

    os.makedirs(WORKSPACE, exist_ok=True)
    shutil.copy(os.path.join(HERE, "leaky_app.py"), TARGET)  # 每次从带 bug 的原版开始
    agent_loop(client, f"{TARGET} 有内存泄漏，请修复它。", model=model)


if __name__ == "__main__":
    main()
