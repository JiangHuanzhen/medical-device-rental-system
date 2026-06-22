# 医疗器械租赁管理系统

> **软件工程课程设计项目** | 基于《高级软件设计实践》教材的 AI 需求工程全流程自动化

多角色（招商业务员 / 库房人员 / 运维工程师 / 财务）协同管理医疗设备租赁全流程的 **需求工程全流程工作流**。项目采用 **7 类 AI 智能体 + CCB 人工审批** 的流水线架构，覆盖从涉众需求获取到需求基线创立的完整闭环，交付 10 项期中提交物。

---

## 快速开始

```bash
# 1. 安装依赖
pip install langgraph langchain-openai python-dotenv requests

# 2. 配置 API Key
#    编辑 .env 文件，确保 LLM_API_KEY 已填写

# 3. 运行工作流（终端）
python .claude/workflows/requirement_workflow.py

# 4. 或启动 Web 界面（推荐——实时看日志 + 暂停/停止/CCB审批）
python sse_server.py
#     浏览器打开 http://localhost:8502
```

---

## 项目结构

```
D:\医疗器械租赁管理系统/
│
├── sse_server.py                           # Web 界面（带 Pause/Stop 按钮）
├── README.md                               # 本文件
├── .env                                    # API Key 与环境变量
│
├── 高级软件设计实践.md                      # 课程教材全文
├── 《高级软件设计实践》实践任务书.docx        # 实践任务书
│
│── 实践规范文档/                           # 11 份 AI 工程标准规范
│   ├── SRS规范.md
│   ├── ADR架构决策记录生成规范.md
│   ├── ...
│
└── .claude/
    ├── workflows/
    │   └── requirement_workflow.py          # 核心：LangGraph 多 Agent 工作流
    │
    └── knowledge-base/                     # Obsidian 知识库 Vault
        ├── README.md                       # Vault 首页导航
        ├── compile.js                      # 四维度验证脚本（教材§2）
        ├── n8n-wrapper.json                # n8n 工作流包装器（教材§8）
        │
        ├── raw/
        │   ├── notes/                      # 原始涉众对话记录
        │   └── agents/                     # 涉众AI智能体配置文件 ×4
        ├── wiki/
        │   ├── summaries/                  # 结构化产物（带版本号）
        │   └── baselines/                  # 基线冻结目录
        └── archive/                        # 期末设计资产归档
```

---

## 多 Agent 工作流架构

```
A1_并行涉众对话 ──→ A1_汇总 ──→ A2_需求分析 ──（通过）──→ A3_UML建模
      ↑                          ↑                            │
      │    ←── A2回退 ←──────────┘                            │
      │                                                        ↓
      │                                                   A4_SRS生成
      │                                                        │
      │                                                        ↓
      │ ←── A5回退(获取类) ←──── A5_验证 ───（通过）──→ A5_缺陷分析报告
      │                               ↑                            │
      └── A5回退(分析类) ←───────────┘                            │
                                                                  ↓
      ←─── CCB回退(获取类/分析类) ←── CCB_审批 ←─────────────────┘
                                          │
                                          ↓
                                     A6_基线创立
                                          │
                                          ↓
                                     A7_ADR生成 → END
```

| 节点 | 功能 | 回退行为 |
|------|------|---------|
| **A1** | 4个涉众Agent并行对话（每Agent最多7轮提问） | A2发现严重问题→回A1 |
| **A2** | 四维度质量检测（模糊/不一致/矛盾/冲突） | 严重→回A1，最多3轮 |
| **A3** | 两阶段UML建模（先用例图→再活动图/时序图/E-R图） | — |
| **A4** | IEEE 830标准 SRS文档生成（≥15000字） | — |
| **A5** | 交叉验证（历史/对话/文档/内部）+ 5份缺陷报告 | 获取类→回A1 / 分析类→回A2 |
| **CCB** | 唯一的人工审批节点 | 通过→基线 / 退回→对应阶段 |
| **A6** | 22列RTM溯源矩阵 + 基线冻结到知识库 | — |
| **A7** | ADR-001 架构决策记录 | — |

---

## 安全机制

| 机制 | 说明 |
|------|------|
| **全局迭代上限** | 最多5轮回退循环，超限强制前进 |
| **Pause / Resume** | 每个节点入口可暂停，恢复后继续执行 |
| **Stop 按钮** | 随时停止工作流，不浪费token |
| **A2 轮次重置** | 非A2回退时自动重置轮次计数 |

---

## 10 项期中提交物（任务书§9.1）

| # | 提交物 | 生成节点 | 知识库路径 |
|---|--------|---------|-----------|
| 1 | 知识库 + compile.js | 初始化 | `knowledge-base/` |
| 2 | 涉众AI智能体配置 ×4 | 初始化 | `raw/agents/` |
| 3 | 涉众对话记录 ×4 | A1 | `raw/notes/` |
| 4 | 需求问题清单 | A2 | `wiki/summaries/` |
| 5 | UML 建模产物 | A3 | `wiki/summaries/*.puml` |
| 6 | SRS 规格说明书 | A4 | `wiki/summaries/` → `baselines/` |
| 7 | 需求验证报告 | A5 | `wiki/summaries/` |
| 8 | 需求基线 + RTM | A6 | `wiki/baselines/BL-*/` |
| 9 | n8n 工作流1 | 已配置 | `n8n-wrapper.json` |
| 10 | 缺陷分析报告 ×5 | A5 | `wiki/summaries/` |

---

## 技术栈

| 组件 | 用途 |
|------|------|
| **LangGraph** | AI Agent 状态图编排（节点 + 条件边 + 回退） |
| **LLM API** | AI 推理（DeepSeek / OpenAI / Claude 兼容） |
| **FastAPI + SSE** | Web 实时界面（日志推送 + 控件交互） |
| **Obsidian** | 知识库管理（双向链接 + frontmatter） |
| **PlantUML** | 自动生成 UML 图（纯文本代码） |
| **n8n** | 可选的工作流包装器（教材§8） |

---

## 配置

```env
# .env 文件
LLM_API_KEY=sk-your-api-key
LLM_API_URL=https://api.deepseek.com/v1/chat/completions
LLM_MODEL=deepseek-chat
```

支持任意 OpenAI 兼容 API（DeepSeek / Claude / Ollama 等）。

---

## 知识库规范（Karpathy 四层架构）

```
raw/notes/         ← 原始涉众对话（一次写入不修改）
  ↓ 提炼
wiki/summaries/    ← 结构化产物（带版本号可迭代）
  ↓ 审批
wiki/baselines/    ← 基线冻结（只增不改）
  ↓ 归档
archive/           ← 期末可复用设计资产
```

运行 `node .claude/knowledge-base/compile.js` 验证知识库完整性。
