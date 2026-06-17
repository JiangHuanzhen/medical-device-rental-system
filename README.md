# 医疗器械租赁管理系统

> **软件工程课程设计项目** | 教材《高级软件设计实践》自动化实现

多角色（招商/库房/运维/财务）协同管理医疗设备租赁全流程的**需求工程全流程工作流**。

---

## 快速开始

```bash
# 1. 安装依赖
pip install langgraph langchain-openai python-dotenv requests

# 2. 创建 .env 文件
echo "LLM_API_KEY=your-api-key" > .env

# 3. 运行工作流
python .claude/workflows/requirement_workflow.py
```

---

## 核心文件

| 文件 | 说明 |
|------|------|
| `.claude/workflows/requirement_workflow.py` | **LangGraph 工作流** |
| `.claude/knowledge-base/` | **Obsidian 知识库 Vault** |
| `.claude/knowledge-base/README.md` | Obsidian Vault 首页导航 |
| `.claude/knowledge-base/compile.js` | 知识库四维度验证脚本（教材§2） |
| `.claude/knowledge-base/n8n-wrapper.json` | n8n 工作流包装器（教材§8要求） |
| `高级软件设计实践.md` | 课程教材 |

---

## 工作流架构

```
A1_招商业务员(Agent) → A1_库房人员(Agent) → A1_运维工程师(Agent) → A1_财务(Agent)
  → A1_汇总 → A2_需求分析(最多3轮)
     ↑            │
     │      ⚠️严重问题 ├──── ✅通过
     │            ↓          ↓
     └── A1回退 ──┘    A3_UML建模 → A4_SRS生成 → A5_验证
                                  ↑               │
                                  │        ✅通过──┤ ⚠️回退A1/A2
                                  │               ↓
                                  │         5份缺陷分析报告
                                  │               ↓
                                  └── CCB回退 ── CCB_审批 → A6_基线
```

### 8 份交付物（教材要求）

| # | 交付物 | 知识库路径 |
|---|--------|-----------|
| 1 | 4 份涉众对话记录 | `raw/notes/` |
| 2 | 结构化需求清单 | `wiki/summaries/` |
| 3 | 需求问题清单 | `wiki/summaries/` |
| 4 | UML 模型（用例图+活动图） | `wiki/summaries/` |
| 5 | SRS 规格说明书 | `wiki/summaries/` |
| 6 | 需求验证报告 | `wiki/summaries/` |
| 7 | 5 份缺陷分析报告 | `wiki/summaries/` |
| 8 | 基线 + RTM 溯源矩阵 | `wiki/baselines/` |

---

## 技术栈

- **LangGraph** — AI Agent 状态图编排（节点 + 条件边 + 循环 + 回退）
- **OpenAI API** — LLM 推理（兼容 Claude/Ollama）
- **Obsidian** — 知识库管理（`[[双向链接]]` + frontmatter）
- **PlantUML** — 自动生成 UML 图
- **n8n** — 可选的包装器（教材§8，导入 `n8n-wrapper.json`）

---

## n8n 集成说明

教材§8要求用 n8n 一键触发全流程。本方案采用 **LangGraph + n8n 双重方案**：

- 核心流程由 **LangGraph** 在代码中精确控制（状态管理、条件路由、循环上限、回退追踪）
- **n8n** 作为包装器调用 Python 脚本
- 将 `n8n-wrapper.json` 导入 n8n 即可（包含 1 个定时触发节点 + 1 个 Execute Command 节点）

---

## LangGraph 工作流细节

| 节点 | 功能 | 回退行为 |
|------|------|---------|
| A1 涉众 Agent | 预设问题提问 / 根据回退原因自动生成追问 | 直接回A1走完整链 |
| A1 汇总 | LLM 合并四份对话为结构化需求清单 | — |
| A2 需求分析 | 四维度检测（模糊/不一致/矛盾/冲突），最多3轮 | 严重→回A1 |
| A3 UML 建模 | 生成用例图 + 活动图 PlantUML | — |
| A4 SRS 生成 | 生成 IEEE 830 标准文档 | — |
| A5 验证 | 四类交叉比对（教材§7） + 5份缺陷报告 | 获取类→回A1 / 分析类→回A2 |
| CCB | 命令行人工审批 | 通过→基线 / 退回A1/A2 |
| A6 基线 | 生成 RTM + 冻结到知识库 | — |
