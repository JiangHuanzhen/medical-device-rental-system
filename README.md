# 🏥 医疗器械租赁管理系统

> **软件工程课程设计项目** | 基于《高级软件设计实践》教材的 AI 需求工程全流程自动化

多角色（招商业务员 / 库房人员 / 运维工程师 / 财务）协同管理医疗设备租赁全流程的 **需求工程全流程工作流**。项目采用 **7 类 AI 智能体 + CCB 人工审批** 的流水线架构，覆盖从涉众需求获取到需求基线创立的完整闭环。

---

## 🧭 快速开始

```bash
# 1. 安装依赖
pip install streamlit langgraph langchain-openai python-dotenv requests

# 2. 配置 API Key（已配置则可跳过）
#    编辑 .env 文件，确保 LLM_API_KEY 已填写

# 3. 一键启动交互式前端（推荐 🆕）
streamlit run streamlit_app.py

# 4. 或直接运行命令行工作流
python .claude/workflows/requirement_workflow.py
```

---

## 📁 项目结构

```
D:\医疗器械租赁管理系统/
│
├── streamlit_app.py                    # 🆕 交互式前端（Streamlit）
├── README.md                           # 本文件
├── .env                                # API Key 与环境变量
│
├── 高级软件设计实践.md                  # 课程教材全文
├── 《高级软件设计实践》实践任务书.docx    # 实践任务书
│
├── 实践规范文档/                        # 11 份 AI 工程标准规范
│   ├── SRS规范.md                      # 软件需求规格说明规范
│   ├── ADR架构决策记录生成规范.md       # 架构决策记录规范
│   ├── OpenAPI YAML接口契约生成规范.md  # 接口契约规范
│   ├── 三层约束设计生成规范.md           # 架构-模块-代码约束规范
│   ├── 需求溯源矩阵（RTM）规范.md       # 溯源矩阵规范
│   ├── 变更需求文档（CR）生成规范.md     # 变更管理规范
│   └── ...（共 11 份）
│
└── .claude/
    ├── workflows/
    │   └── requirement_workflow.py      # 🧠 核心：LangGraph 多 Agent 工作流
    │
    └── knowledge-base/                 # 📚 Obsidian 知识库 Vault
        ├── README.md                   # Vault 首页导航
        ├── compile.js                  # 四维度验证脚本（教材§2）
        ├── n8n-wrapper.json            # n8n 工作流包装器（教材§8）
        ├── .obsidian/                  # Obsidian 配置
        │
        ├── raw/notes/                  # 原始涉众对话
        ├── wiki/summaries/             # 结构化产物（带版本号）
        ├── wiki/baselines/             # 基线冻结
        └── archive/                    # 归档
```

---

## 🎮 交互式前端（Streamlit）

`streamlit_app.py` 提供了一个 **实时可视化的操作界面**，核心设计理念是"每次只走一步，立刻刷新"——让用户能实时看到每条 AI 提问和涉众的回答。

### 主要功能

| 功能 | 说明 |
|------|------|
| **一键运行** | 点击按钮启动全流程，从 A1 涉众对话到 A6 基线创立 |
| **💬 对话记录** | 4 个涉众分 Tab 展示，气泡样式（AI 蓝 / 涉众绿） |
| **📋 需求清单** | 实时展示 A1 汇总后的结构化需求 |
| **📐 UML 模型** | 显示 PlantUML 源码（用例图 + 活动图） |
| **📄 SRS 文档** | 展示 IEEE 830 标准需求规格说明书 |
| **🔎 验证/缺陷** | 验证结论 + 5 份缺陷分析报告 |
| **⛔ 基线/RTM** | 基线版本 + 溯源矩阵 |
| **实时日志** | 侧边栏显示每一步的运行状态 |
| **CCB 审批** | 内置审批表单，人工决定通过/回退 |

---

## 🧠 多 Agent 工作流架构

```
A1_招商业务员(Agent) ─┐
A1_库房人员(Agent)    ├─→ A1_汇总 → A2_需求分析(最多3轮)
A1_运维工程师(Agent)  │                ↑            │
A1_财务(Agent)       ┘                │      ⚠️严重问题 ├──── ✅通过
                                       │            ↓          ↓
                                       └── A1回退 ──┘    A3_UML建模
                                                           │
                                                           ↓
                                                      A4_SRS生成
                                                           │
                                                           ↓
                                                      A5_验证 ──→ 5份缺陷报告
                                                       ↑    │
                                                       │    ├── ✅通过
                                                       │    ├── ⚠️获取类→回A1
                                                       │    └── ⚠️分析类→回A2
                                                       │         ↓
                                                  CCB回退 ←─ CCB_审批
                                                               │
                                                               ↓
                                                          A6_基线
```

### 各阶段详情

| 节点 | 功能 | 回退行为 |
|------|------|---------|
| **A1 涉众 Agent** | AI 自主生成问题，逐轮向涉众追问直到了解充分 / 回退时根据问题自动生成追问（最多 5 轮） | 直接回 A1 走完整链 |
| **A1 汇总** | LLM 合并四份对话为结构化需求清单 | — |
| **A2 需求分析** | 四维度检测：模糊 / 不一致 / 矛盾 / 冲突 | 严重→回 A1 |
| **A3 UML 建模** | 生成用例图 + 活动图（PlantUML 代码） | — |
| **A4 SRS 生成** | 生成 IEEE 830 标准文档（≥10000 字） | — |
| **A5 验证** | 四类交叉比对 + 5 份缺陷报告 | 获取类→回 A1 / 分析类→回 A2 |
| **CCB** | 人工审批（通过 / 退回 A1 / 退回 A2） | 通过→基线 / 退回 → 对应阶段 |
| **A6 基线** | 生成 RTM 溯源矩阵 + 冻结到知识库 | — |

---

## 📦 8 份交付物（教材要求）

| # | 交付物 | 知识库路径 |
|---|--------|-----------|
| 1 | 4 份涉众对话记录 | `raw/notes/` |
| 2 | 结构化需求清单 | `wiki/summaries/` |
| 3 | 需求问题清单 | `wiki/summaries/` |
| 4 | UML 模型（用例图 + 活动图） | `wiki/summaries/` |
| 5 | SRS 规格说明书 | `wiki/summaries/` |
| 6 | 需求验证报告 | `wiki/summaries/` |
| 7 | 5 份缺陷分析报告 | `wiki/summaries/` |
| 8 | 基线 + RTM 溯源矩阵 | `wiki/baselines/` |

---

## 🔧 技术栈

| 组件 | 用途 |
|------|------|
| **Streamlit** | 交互式前端界面 |
| **LangGraph** | AI Agent 状态图编排（节点 + 条件边 + 循环 + 回退） |
| **LLM API** | AI 推理（兼容 OpenAI / DeepSeek / Claude / Ollama） |
| **Obsidian** | 知识库管理（`[[双向链接]]` + frontmatter） |
| **PlantUML** | 自动生成 UML 图 |
| **n8n** | 可选的自动化工作流包装器（教材§8） |

---

## ⚙️ 配置

```env
# .env 文件
LLM_API_KEY=sk-your-api-key          # API 密钥
LLM_API_URL=https://api.deepseek.com/v1/chat/completions  # API 端点
LLM_MODEL=deepseek-chat               # 模型名称
```

支持切换任意 OpenAI 兼容 API（DeepSeek / Claude / Ollama 本地模型等）。

---

## 🔗 n8n 集成

教材§8 要求用 n8n 一键触发全流程。本方案采用 **LangGraph + n8n 双重方案**：

- 核心流程由 **LangGraph** 在代码中精确控制（状态管理、条件路由、循环上限、回退追踪）
- **n8n** 作为包装器调用 Python 脚本
- 将 `n8n-wrapper.json` 导入 n8n 即可（包含 1 个定时触发节点 + 1 个 Execute Command 节点）

---

## 📚 实践规范文档

项目配备了 11 份 AI 工程标准规范文档，覆盖需求工程和软件设计的各个环节：

- **需求阶段**：SRS 规范、需求溯源矩阵规范
- **设计阶段**：三层约束设计规范、架构风格声明规范、模块划分与依赖拓扑规范
- **接口阶段**：OpenAPI YAML 接口契约生成规范 + 使用指南 + 全覆盖范围
- **变更管理**：变更需求文档规范、变更影响分析规范、变更回归校验规范
- **架构决策**：ADR 架构决策记录生成规范
- **质量验证**：逆向校验报告生成规范
