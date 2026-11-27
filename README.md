# Agent Learning｜AI Agent 入门学习笔记

从零学习 AI Agent 开发的中文图文笔记，共 7 课。每一课精读一份公开资料，并用同一个贯穿例子串起来：**内存泄漏的注入 → 检测 → 修复 → 验证**。最终目标是为搭建一个“内存泄漏注入、监测、修正”的 Agent 平台打基础。

适合读者：了解 LLM 的基本概念，但还没有写过 Agent。

## 课程目录

按顺序学习，后面的课会用到前面的概念和代码。

| 课 | 文件夹 | 主题 | 动手内容 |
| --- | --- | --- | --- |
| 1 | [01-building-effective-agents](01-building-effective-agents/README.md) | workflow 和 agent 的区别、常见模式、什么时候不该用 agent | `mini_agent.py`：最小的“修泄漏”Agent（LLM + 工具 + 循环） |
| 2 | [02-llm-powered-autonomous-agents](02-llm-powered-autonomous-agents/README.md) | 规划、记忆、工具、反馈和验收 | 复用第 1 课代码的跟做练习 |
| 3 | [03-react-reasoning-and-acting](03-react-reasoning-and-acting/README.md) | ReAct：思考、行动、观察的循环 | `react_text_agent.py`：文本格式的 ReAct 演示 |
| 4 | [04-hf-agents-course](04-hf-agents-course/README.md) | 从手写循环到框架，结构化工具调用 | `structured_tool_agent.py`：把文本动作改成结构化工具调用 |
| 5 | [05-mini-swe-agent](05-mini-swe-agent/README.md) | 通读 mini-swe-agent 源码，走完一次修复 | `leak_probe.py`、`offline_replay.py` |
| 6 | [06-swe-agent-and-swe-bench](06-swe-agent-and-swe-bench/README.md) | 代码修复 Agent 的工具设计（ACI）和基准评测 | `demo_aci_and_eval.py`：迷你评测流程 |
| 7 | [07-model-context-protocol](07-model-context-protocol/README.md) | 用 MCP 这一通用协议给 Agent 接工具 | `run_lab.py`：通过 MCP 注入、探测、修复泄漏 |

## 项目架构

```
agent/
├── README.md                          本文件
├── 01-building-effective-agents/
│   ├── README.md                      本课讲义
│   ├── images/                        讲义配图
│   └── code/
│       ├── leaky_app.py               故意带内存泄漏的玩具服务
│       ├── check_leak.py              检测器，输出 LEAK 或 PASS
│       └── mini_agent.py              Agent 本体
├── 02-llm-powered-autonomous-agents/  只有讲义和配图
├── 03-react-reasoning-and-acting/
├── 04-hf-agents-course/
├── 05-mini-swe-agent/
├── 06-swe-agent-and-swe-bench/
└── 07-model-context-protocol/
```

课与课之间的依赖：第 2、3、4 课的演示复用第 1 课的 `code/`（玩具服务和检测器），所以各课文件夹要保持并排，不要单独挪走。

贯穿全课的主线是同一个循环，每一课从不同角度加深它：

```
注入泄漏 → 检测器给出证据 → Agent 读代码定位 → 写入修复 → 再次检测验收
```

## 每课的格式

每一课是一个独立文件夹，命名为 `两位序号-英文主题`，内部结构固定：

- `README.md`：中文讲义，包含原文要点、和内存泄漏平台的对应关系、动手步骤，部分课带自测题或练习。
- `images/`：讲义里的示意图，文件名按出现顺序编号（`01-xxx.png`），在讲义里用相对路径引用。
- `code/`：可以运行的演示（第 2 课没有）。

运行时生成的 `workspace/`、`eval_logs/`、`.venv/` 和 `__pycache__/` 不纳入版本管理，见 `.gitignore`。

## 运行环境

- Python 3.12，命令统一用 `python`。
- 第 1 到 6 课的演示只用 Python 标准库，不需要安装任何包，也不需要 API Key。
- 第 7 课需要官方 MCP Python SDK，按该课讲义在课程文件夹里建虚拟环境安装。
- 第 1 课的 `mini_agent.py` 有三种模式：`--mock`（按剧本返回的假 LLM，不需要 Key）、Gemini（设置环境变量 `GEMINI_API_KEY`）、Anthropic（设置 `ANTHROPIC_API_KEY`）。**不要把 Key 写进代码或提交到仓库。**
- 第 5 课的 `offline_replay.py` 需要在 WSL 或 Linux 里安装 mini-swe-agent，步骤见该课讲义。

快速体验：

```powershell
cd 01-building-effective-agents\code
python check_leak.py leaky_app.py
python mini_agent.py --mock
```

第一条命令的结论是 `LEAK`，Agent 跑完后复测的结论是 `PASS`。

## Reference

| 课 | 资料 | 链接 |
| --- | --- | --- |
| 1 | Anthropic, *Building effective agents* | https://www.anthropic.com/engineering/building-effective-agents |
| 2 | Lilian Weng, *LLM Powered Autonomous Agents* | https://lilianweng.github.io/posts/2023-06-23-agent/ |
| 3 | Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models* | https://arxiv.org/abs/2210.03629 |
| 4 | Hugging Face Agents Course | https://huggingface.co/learn/agents-course |
| 5 | mini-swe-agent | https://github.com/SWE-agent/mini-swe-agent |
| 6 | SWE-agent | https://github.com/SWE-agent/SWE-agent |
| 6 | SWE-bench | https://www.swebench.com |
| 7 | Model Context Protocol | https://modelcontextprotocol.io |

各课讲义里另有更细的出处和延伸阅读。
