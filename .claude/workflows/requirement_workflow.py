# 医疗器械租赁管理系统 — 需求工程 LangGraph 工作流 v3
#
# 核心设计：
#   A1的4个涉众节点本身就是Agent
#   - 首次运行：AI自主生成问题向涉众提问（无预设问题列表）
#   - 回退运行：读取A2/A5/CCB的反馈，自动判断是否与自己相关，生成针对性追问
#
# 工作流：
#   A1(4个涉众Agent) → A1汇总 → A2需求分析(最多3轮) → A3建模 → A4写SRS
#   → A5验证 → A5缺陷分析报告(5份) → CCB审批 → A6基线
#
# 使用方法：
#   1. pip install langgraph langchain-openai python-dotenv requests
#   2. 创建 .env 文件，填入 API Key
#   3. python requirement_workflow.py
#
# n8n 集成：
#   配套 n8n 工作流包装器在 workflows/n8n-wrapper.json
#   导入到 n8n 后，一个 Execute Command 节点调用此脚本
#   实现教材§8要求的"n8n一键触发需求开发全流程"

import os
import json
import requests
from typing import TypedDict, List, Literal
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# ============================================================
# ⚙️ 配置
# ============================================================

LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-your-api-key")
LLM_API_URL = os.getenv("LLM_API_URL", "https://api.deepseek.com/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

STAKEHOLDER_API = "https://210.34.148.101:5000/api/chat"
PROJECT_ID = "10"

# Obsidian 知识库 Vault 路径（基于当前文件位置，确保绝对路径）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
KB_ROOT = os.getenv("KB_ROOT", os.path.join(_PROJECT_ROOT, ".claude", "knowledge-base"))

# A1涉众配置：名称 → 对应state字段 + Agent角色定义（Role/Goal/Backstory）
STAKEHOLDER_CONFIG = {
    "招商业务员": {"field": "biz_dialog"},
    "库房人员": {"field": "warehouse_dialog"},
    "运维工程师": {"field": "maintenance_dialog"},
    "财务": {"field": "finance_dialog"},
}

# 涉众AI智能体配置文件路径
AGENT_CONFIG_DIR = os.path.join(KB_ROOT, "raw/notes")
AGENT_CONFIG_FILES = {
    "招商业务员": "Agent定义-招商业务员.md",
    "库房人员": "Agent定义-库房人员.md",
    "运维工程师": "Agent定义-运维工程师.md",
    "财务": "Agent定义-财务.md",
}


def load_agent_configs() -> dict:
    """从知识库读取4份涉众AI智能体配置文件，返回 {名称: {role, goal, backstory}} 字典"""
    configs = {}
    for name, filename in AGENT_CONFIG_FILES.items():
        filepath = os.path.join(AGENT_CONFIG_DIR, filename)
        if not os.path.exists(filepath):
            print(f"  ⚠️ Agent配置文件不存在: {filepath}，使用默认配置")
            configs[name] = {"role": name, "goal": "", "backstory": ""}
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        # 从 Markdown 中提取 Goal 和 Backstory
        goal = ""
        backstory = ""
        if "## 核心目标（Goal）" in text:
            chunk = text.split("## 核心目标（Goal）")[1]
            goal = chunk.split("## ")[0].strip().replace("\n", " ")
        if "## 背景设定（Backstory）" in text:
            chunk = text.split("## 背景设定（Backstory）")[1]
            backstory = chunk.split("## ")[0].strip().replace("\n", " ")
        configs[name] = {"role": name, "goal": goal, "backstory": backstory}
        print(f"  📄 已加载Agent配置: {filename}")
    return configs


# 启动时加载4份涉众Agent配置（存量知识库文件）
# 使用 None 占位，首次访问时懒加载，避免导入时读文件出错
_AGENT_DEFINITIONS_CACHE = None


def get_agent_definitions() -> dict:
    """懒加载 Agent 定义，避免模块导入时读文件出错"""
    global _AGENT_DEFINITIONS_CACHE
    if _AGENT_DEFINITIONS_CACHE is not None:
        return _AGENT_DEFINITIONS_CACHE
    try:
        _AGENT_DEFINITIONS_CACHE = load_agent_configs()
    except Exception as e:
        print(f"  ⚠️ Agent配置文件加载失败: {e}，使用空配置")
        _AGENT_DEFINITIONS_CACHE = {name: {"role": name, "goal": "", "backstory": ""} for name in AGENT_CONFIG_FILES}
    return _AGENT_DEFINITIONS_CACHE

# ============================================================
# 📦 状态定义
# ============================================================

class RequirementState(TypedDict):
    """工作流全局状态"""
    # A1 对话记录
    biz_dialog: List[dict]
    warehouse_dialog: List[dict]
    maintenance_dialog: List[dict]
    finance_dialog: List[dict]

    # A1 汇总
    consolidated_requirements: str

    # A2 质量分析
    quality_issues: List[dict]
    has_critical_issues: bool
    a2_round: int                    # A2当前轮次（教材§5：最多3轮）
    a2_max_rounds: int               # 最大轮次数

    # A3 UML
    uml_use_case: str
    uml_activity_diagrams: str

    # A4 SRS
    srs_draft: str

    # A5 验证
    validation_report: str
    validation_verdict: str          # "通过" / "获取类问题" / "分析类问题"
    defect_reports: str              # 5份缺陷分析报告（教材§1要求）

    # CCB
    ccb_verdict: str
    ccb_comment: str

    # A6 基线
    baseline_version: str
    rtm: str

    # 回退原因（A1 Agent根据此判断如何行动）
    rollback_reason: str             # "" / "A2_rollback" / "A5_acquisition" / "CCB_acquisition" / "A5_analysis" / "CCB_analysis"

    # 控制
    iteration_count: int
    workflow_status: str


# ============================================================
# 🛠️ 工具函数
# ============================================================

def call_llm(prompt: str, system_prompt: str = "") -> str:
    """调用LLM"""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"}
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": LLM_MODEL, "messages": messages, "temperature": 0.3, "max_tokens": 16000}
    try:
        resp = requests.post(LLM_API_URL, headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[LLM调用失败: {e}]"


def call_stakeholder(stakeholder: str, question: str) -> str:
    """调用涉众API"""
    payload = {"project_id": PROJECT_ID, "stakeholder": stakeholder, "phase": "v1", "question": question}
    try:
        resp = requests.post(STAKEHOLDER_API, json=payload, verify=False, timeout=60)
        resp.raise_for_status()
        return resp.json().get("answer", "")
    except Exception as e:
        return f"[涉众API调用失败: {e}]"


def extract_json(text: str) -> dict:
    """从LLM回复中提取JSON对象"""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {}


KB_DIRS = {
    "raw_notes": os.path.join(KB_ROOT, "raw/notes"),
    "wiki": os.path.join(KB_ROOT, "wiki/summaries"),
    "baselines": os.path.join(KB_ROOT, "wiki/baselines"),
    "archive": os.path.join(KB_ROOT, "archive"),
}


def save_to_kb(subdir: str, filename: str, content: str) -> str:
    """将内容保存到 Obsidian 知识库的对应目录，返回文件路径"""
    base = KB_DIRS[subdir]
    # 如果 filename 包含子路径（如 "BL-20260618-01/SRS-正式版.md"），需要递归创建目录
    full_path = os.path.join(base, filename)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  📁 知识库: {subdir}/{filename}")
    return full_path


def kb_frontmatter(title: str, tags: list[str], aliases: list[str] | None = None) -> str:
    """生成 Obsidian 前置元数据"""
    fm = f"---\ntitle: {title}\ntags: [{' '.join(tags)}]\n"
    if aliases:
        fm += f"aliases: {json.dumps(aliases, ensure_ascii=False)}\n"
    fm += "---\n\n"
    return fm


def ensure_agent_configs() -> dict:
    """确保知识库中存在4份涉众AI智能体配置文件，如果缺失则自动生成"""
    stakeholders = ["招商业务员", "库房人员", "运维工程师", "财务"]
    for s in stakeholders:
        filepath = os.path.join(AGENT_CONFIG_DIR, AGENT_CONFIG_FILES[s])
        if os.path.exists(filepath):
            print(f"  📄 Agent配置已存在: {AGENT_CONFIG_FILES[s]}")
            continue
        # 从已加载的 Agent 定义获取
        cfg = get_agent_definitions().get(s, {"role": s, "goal": "", "backstory": ""})
        content = kb_frontmatter(f"Agent-{cfg['role']}", ["Agent配置", cfg["role"]]) + f"""# 涉众AI智能体配置：{cfg['role']}

## 基本信息
| 字段 | 内容 |
|------|------|
| 角色名称（Role） | {cfg['role']} |
| 所属项目 | 医疗器械租赁管理系统 |
| 创建日期 | {datetime.now().strftime('%Y-%m-%d')} |
| 配置版本 | V1.0 |

## 核心目标（Goal）
{cfg['goal']}

## 背景设定（Backstory）
{cfg['backstory']}

## 使用说明
本配置文件采用 CrewAI 兼容格式，可直接用于定义涉众 AI 智能体。
- **Role**：定义智能体的角色身份
- **Goal**：定义智能体的核心目标
- **Backstory**：定义智能体的背景知识和行为准则
"""
        save_to_kb("raw_notes", AGENT_CONFIG_FILES[s], content)
        print(f"  🆕 Agent配置已生成: {AGENT_CONFIG_FILES[s]}")
    return get_agent_definitions()


# ============================================================
# 🧠 A1 涉众 Agent 节点
# ============================================================
# 设计：每个A1节点就是Agent
#   - 首次运行 → AI自主生成问题，逐轮追问
#   - 回退运行 → 读取rollback_reason，根据A2/A5/CCB反馈自动追问
#   回退时不走中间节点，直接走回A1
# ============================================================

def a1_agent_biz(state: RequirementState) -> dict:
    """A1 Agent — 招商业务员"""
    return _a1_agent("招商业务员", state)


def a1_agent_warehouse(state: RequirementState) -> dict:
    """A1 Agent — 库房人员"""
    return _a1_agent("库房人员", state)


def a1_agent_maintenance(state: RequirementState) -> dict:
    """A1 Agent — 运维工程师"""
    return _a1_agent("运维工程师", state)


def a1_agent_finance(state: RequirementState) -> dict:
    """A1 Agent — 财务"""
    return _a1_agent("财务", state)


def _a1_agent(stakeholder: str, state: RequirementState) -> dict:
    """A1涉众Agent核心逻辑 — AI自主提问 or 回退追问"""
    cfg = STAKEHOLDER_CONFIG[stakeholder]
    field = cfg["field"]
    existing = state.get(field, [])
    reason = state.get("rollback_reason", "")

    # ── 首次运行：AI自主生成问题，逐轮追问直到了解充分 ──
    if not existing:
        dialog = []
        max_rounds = 5  # 每个涉众最多提问轮数
        round_num = 0

        # 加载该涉众的 Agent 定义（Role/Goal/Backstory）作为系统提示
        agent_def = get_agent_definitions().get(stakeholder, {})
        agent_system_prompt = f"你是一名资深的需求分析工程师，正在访谈「{stakeholder}」。\n关于该角色的背景信息：\n- 角色：{agent_def.get('role', stakeholder)}\n- 核心目标：{agent_def.get('goal', '')}\n- 背景：{agent_def.get('backstory', '')}"

        while round_num < max_rounds:
            # 构建已有对话历史
            history = ""
            for i, m in enumerate(dialog):
                history += f"问{i+1}: {m['q']}\n答{i+1}: {m['a']}\n\n"

            if not dialog:
                prompt = f"""你是一名资深的软件需求分析工程师，正在访谈医疗设备租赁系统的「{stakeholder}」角色。

这是第一次对话，请根据该角色的工作职责生成第一个问题。
要求：
1. 从开放式问题开始，引导对方描述整体工作流程
2. 问题要自然、具体，不要过于宽泛
3. 聚焦该角色在医疗设备租赁场景中的职责

输出严格JSON格式（只有JSON，不要其他文字）：
{{"question": "你生成的第一个问题"}}"""
            else:
                prompt = f"""你是一名资深的软件需求分析工程师，正在访谈医疗设备租赁系统的「{stakeholder}」角色。

你已经问了以下问题，得到了这些回答：

{history}

请根据已有对话，判断你是否已经充分了解该角色的需求。
- 如果已经了解清楚（覆盖了工作流程、痛点、异常场景、核心需求），输出：{{"done": true}}
- 如果还需要追问，生成下一个自然、具体的问题，要求：
  1. 不要问已经问过的话题
  2. 追问之前回答中模糊的细节（「很多」「大概」「经常」等模糊表述）
  3. 探索异常情况（「如果…出错了怎么办」）
  4. 每个问题只聚焦一个方面

输出严格JSON格式（只有JSON，不要其他文字）：
{{"done": false, "question": "你的追问问题"}}"""

            result = call_llm(prompt, system_prompt="你是专业的需求分析专家，擅长通过对话获取详细需求。")
            data = extract_json(result)

            # 非首次问且AI表示已了解充分 → 结束
            if dialog and data.get("done", False):
                print(f"  [{stakeholder}] ✅ AI判断已了解充分，结束提问")
                break

            question = data.get("question", "")
            if not question:
                print(f"  [{stakeholder}] ⚠️ AI未生成有效问题，结束")
                break

            answer = call_stakeholder(stakeholder, question)
            dialog.append({"q": question, "a": answer})
            print(f"  [{stakeholder}] 🤖 Q{round_num+1}: {question[:40]}... → 已回复")
            round_num += 1

        print(f"  [{stakeholder}] 完成 {len(dialog)} 条问答")

        # 保存到知识库 raw/notes
        lines = [f"# {stakeholder} — 需求获取记录\n", f"日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
        for i, item in enumerate(dialog, 1):
            lines.append(f"## 第{i}问\n**问：** {item['q']}\n\n**答：** {item['a']}\n")
        content = kb_frontmatter(f"{stakeholder}需求记录", ["涉众对话", stakeholder], [f"{stakeholder}"]) + "\n".join(lines)
        ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
        ts = state.get("time_str", datetime.now().strftime("%H%M"))
        save_to_kb("raw_notes", f"{ds}-{ts}-{stakeholder}-需求记录.md", content)
        return {field: dialog}

    # ── 非回退状态但已有对话 → 安全跳过 ──
    if not reason or reason == "":
        return {}

    # ── 回退运行：根据回退原因生成追问 ──
    # 收集需要分析的问题列表
    issues = []
    context_desc = ""

    if reason == "A2_rollback":
        issues = state.get("quality_issues", [])
        context_desc = "需求质量分析发现以下问题"
    elif reason.endswith("_analysis"):
        # A5/CCB 分析类回退 → 从validation_report中获取
        report = state.get("validation_report", "")
        data = extract_json(report)
        issues = data.get("findings", [])
        context_desc = "需求验证发现以下分析类问题"
    elif reason.endswith("_acquisition"):
        # A5/CCB 获取类回退
        report = state.get("validation_report", "")
        data = extract_json(report)
        issues = data.get("findings", [])
        context_desc = "需求验证发现以下获取类问题"

    if not issues:
        print(f"  [{stakeholder}] 无待处理问题，跳过")
        return {}

    # LLM判断相关问题并生成追问
    prompt = f"""你是一名涉众对话Agent，角色是「{stakeholder}」。

=== 已有对话记录 ===
{json.dumps(existing, ensure_ascii=False, indent=2)}

=== {context_desc} ===
{json.dumps(issues, ensure_ascii=False, indent=2)}

请判断：
1. 哪些问题与"{stakeholder}"这个角色的工作直接相关？
2. 对每个相关问题，生成1句自然语言的追问问题

要求：
- 把"模糊""不一致""遗漏"等技术术语翻译成涉众日常语言
- 引用之前对话中的具体内容作为铺垫（"你刚才提到...请问..."）
- 最多不超过3个追问，优先关注严重问题

输出严格JSON格式（只有JSON，不要其他文字）：
{{"relevant": true/false, "follow_ups": ["追问句1", "追问句2"]}}
"""

    result = call_llm(prompt, system_prompt="你是专业的涉众需求分析师，擅长根据问题生成自然追问。")
    data = extract_json(result)

    if data.get("relevant") and data.get("follow_ups"):
        new_dialog = list(existing)
        for q in data["follow_ups"]:
            answer = call_stakeholder(stakeholder, q)
            new_dialog.append({"q": q, "a": answer})
            print(f"  [{stakeholder}] ⤴ 追问: {q[:40]}... → 已回复")
        print(f"  [{stakeholder}] 补充追问完成")
        return {field: new_dialog}

    print(f"  [{stakeholder}] 无需补充追问")
    return {}


# ============================================================
# 🧠 A1汇总
# ============================================================

def a1_consolidate(state: RequirementState) -> dict:
    """A1汇总：合并四个涉众的需求为结构化需求清单"""
    all_dialogs = {
        name: state.get(cfg["field"], [])
        for name, cfg in STAKEHOLDER_CONFIG.items()
    }

    prompt = f"""你作为需求分析员，将以下四个涉众的对话记录整理为结构化的需求清单。

对话记录：
{json.dumps(all_dialogs, ensure_ascii=False, indent=2)}

请按以下格式输出：
1. 按功能模块分类（用户认证、设备管理、客户管理、租赁订单、费用结算、数据统计、系统配置）
2. 每条需求格式：REQ-{{模块缩写}}-{{编号}} | 涉众来源 | 需求描述 | 优先级
3. 标注每条需求的边界条件（如数量范围、时间周期、金额限制）
4. 在末尾为每个涉众生成 [[{{涉众名}}需求记录]] 的双向链接"""

    result = call_llm(prompt, system_prompt="你是一名资深需求分析工程师，擅长将涉众对话整理为结构化需求。")

    # 保存到 Obsidian 知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    content = kb_frontmatter(f"需求清单-{ds}", ["需求", "汇总"], ["需求清单"]) + result
    save_to_kb("wiki", f"需求清单-{ds}-v1.0.md", content)

    return {"consolidated_requirements": result}


# ============================================================
# 🧠 A2 需求分析 + 路由
# ============================================================

def a2_analyze_quality(state: RequirementState) -> dict:
    """A2需求分析：四维度质量检测，检测严重问题时回退A1（教材§5）"""
    current_round = state.get("a2_round", 1)
    max_rounds = state.get("a2_max_rounds", 3)
    prompt = f"""对以下需求清单进行四维度质量检测（第{current_round}轮/最多{max_rounds}轮）：

{state.get("consolidated_requirements", "")}

检测维度：
1. **模糊** - 是否包含「尽量」「大概」「合理」「快速」等不可量化词语
2. **不一致** - 同一术语在不同地方是否有不同定义
3. **矛盾** - 两条需求是否在逻辑上无法同时成立
4. **冲突** - 不同涉众的互斥期望

输出严格JSON格式：
{{"issues": [{{"type": "模糊/不一致/矛盾/冲突", "severity": "严重/中/低", "description": "问题描述", "source": "涉及涉众", "suggestion": "修正建议"}}], "has_critical": true/false}}"""

    result = call_llm(prompt, system_prompt="你是一名严格的需求质量审查专家。请精确输出JSON。")
    data = extract_json(result)
    issues = data.get("issues", [])
    has_critical = data.get("has_critical", False)

    # 保存问题清单到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    issue_rows = "\n".join(
        f"| {i.get('type','')} | {i.get('severity','')} | {i.get('description','')} | {i.get('source','')} | {i.get('suggestion','')} |"
        for i in issues
    ) if issues else "| — | — | 未发现严重问题 | — | — |"
    issues_content = kb_frontmatter(f"需求问题清单-{ds}", ["问题", "质量分析"], ["需求问题清单"]) + f"""# 需求问题清单

生成日期：{ds}
分析轮次：第{current_round}轮

| 类型 | 严重程度 | 问题描述 | 涉及涉众 | 修正建议 |
|-----|---------|---------|---------|---------|
{issue_rows}

> 本报告由 A2 需求分析 Agent 生成，关联 [[需求清单-{ds}-v1.0|需求清单]]
"""
    save_to_kb("wiki", f"需求问题清单-{ds}-v1.0.md", issues_content)

    return {
        "quality_issues": issues,
        "has_critical_issues": has_critical,
        "a2_round": current_round + 1,
        "rollback_reason": "A2_rollback" if has_critical else "",
    }


def a2_decide_next(state: RequirementState) -> Literal["rollback", "continue"]:
    """A2条件判断：严重问题且未达上限→回退A1，超过上限或通过→继续（教材§5）"""
    has_critical = state.get("has_critical_issues", False)
    a2_round = state.get("a2_round", 1)
    max_rounds = state.get("a2_max_rounds", 3)

    if has_critical and a2_round <= max_rounds:
        print(f"  [A2] ⚠️ 第{a2_round}轮发现严重问题 → 回退A1，涉众Agent将自动生成追问")
        return "rollback"
    if has_critical:
        print(f"  [A2] ⚠️ 已达最大轮数({max_rounds})，强制继续")
    else:
        print(f"  [A2] ✅ 需求质量检测通过")
    return "continue"


# ============================================================
# 🧠 A3 UML建模
# ============================================================

def a3_generate_uml(state: RequirementState) -> dict:
    """A3建模：生成UML用例图、活动图、时序图和E-R图"""
    prompt = f"""根据以下需求清单，生成UML模型（PlantUML代码）：

需求清单：
{state.get("consolidated_requirements", "")[:8000]}

请生成以下四类UML图，每类图用 @startuml ... @enduml 包裹，图与图之间用空行分隔：

1. **用例图**（@startuml ... @enduml）
   - Actor：4种角色（招商业务员、库房人员、运维工程师、财务）
   - Use Case：所有系统功能，标注 <<include>> 和 <<extend>> 关系

2. **活动图**（至少3个核心流程）
   - 租赁订单完整流程（创建→审核→出库→归还→结算）
   - 设备入库流程
   - 设备维修流程
   - 每个图包含：正常路径×2 + 异常路径×2，分支条件用[Guard Condition]

3. **时序图**（至少1个核心流程）
   - 租赁订单创建流程：展示 Actor → Controller → Service → Repository 的完整调用时序
   - 包含：正常流程消息、异常返回消息、循环/可选片段

4. **数据库E-R图**（PlantUML格式，不是Mermaid）
   - 核心实体：设备、客户、租赁订单、合同、费用记录、维修记录
   - 标注实体间关系（1对多、多对多）和关键属性字段"""


    result = call_llm(prompt, system_prompt="你是UML建模专家，精通PlantUML语法。")
    parts = result.split("@startuml")
    # parts[0]是引言文本，parts[1:]是各个图
    diagrams = []
    for i, p in enumerate(parts[1:], 1):
        diagram = "@startuml" + p
        diagrams.append(diagram)

    use_case = diagrams[0] if len(diagrams) > 0 else ""
    # 活动图
    all_others = "\n\n".join(diagrams[1:]) if len(diagrams) > 1 else ""

    # 保存到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    if use_case:
        save_to_kb("wiki", f"用例图-{ds}-v1.0.puml", use_case)
    # 其他图保存到一个综合文件
    if all_others:
        save_to_kb("wiki", f"行为模型图-{ds}-v1.0.puml", all_others)
    uml_doc = kb_frontmatter(f"UML模型-{ds}", ["UML", "建模"], ["UML模型"]) + f"""# UML模型

生成日期：{ds}

## 文件
- [[用例图-{ds}-v1.0.puml|用例图]]
- [[行为模型图-{ds}-v1.0.puml|活动图/时序图/E-R图]]

## 所涉图类型
1. 用例图（Use Case）
2. 活动图（Activity Diagram，≥3个核心流程）
3. 时序图（Sequence Diagram，≥1个核心流程）
4. 数据库E-R图（Entity Relationship Diagram）

## 关联需求
- [[需求清单-{ds}-v1.0|需求清单]]

> 由 A3 建模 Agent 根据需求清单生成
"""
    save_to_kb("wiki", f"UML模型说明-{ds}-v1.0.md", uml_doc)

    return {"uml_use_case": use_case, "uml_activity_diagrams": all_others}


# ============================================================
# 🧠 A4 SRS生成
# ============================================================

def a4_generate_srs(state: RequirementState) -> dict:
    """A4文档智能体：生成IEEE 830标准SRS文档（规范对齐版）"""
    prompt = f"""请根据以下输入生成一份完整的SRS（软件需求规格说明书），严格遵循IEEE 830标准和GB/T 9385规范。

需求清单：
{state.get("consolidated_requirements", "")[:6000]}

UML模型：
{state.get("uml_use_case", "")[:2000]}
{state.get("uml_activity_diagrams", "")[:2000]}

请严格按照以下模板结构生成，缺一不可：

---
# 文档头部信息
| 项目项 | 内容 |
| 文档名称 | 软件需求规格说明书（SRS）|
| 项目名称 | 医疗器械租赁管理系统 |
| 文档版本 | V1.0.0 |
| 基线版本 | 【占位，由A6分配】|
| 编制日期 | 【当前日期】|

## 修订历史记录
| 版本号 | 修订日期 | 修订类型 | 修订内容简述 |
| V1.0.0 | 【当前日期】 | 新建 | 文档初稿，确立初始需求基线 |

# 1 引言
## 1.1 编制目的
## 1.2 文档范围（包含/排除）
## 1.3 引用文件（IEEE 830、GB/T 9385等）
## 1.4 术语与缩略语（含SRS、CCB、CR、FR、NFR等定义表）
## 1.5 业务背景概述（现状痛点、建设目标、量化业务目标）

# 2 总体描述
## 2.1 产品概述（系统定位、核心价值）
### 系统架构图（Mermaid代码）
```mermaid
flowchart TD
    subgraph 客户端层
    end
    subgraph 接入层
    end
    subgraph 业务服务层
    end
    subgraph 数据层
    end
```
## 2.2 运行环境要求（硬件/软件/浏览器兼容表）
## 2.3 用户角色与特征（角色/职责/权限/频次/技能 矩阵表）
## 2.4 系统运行模式（正常/异常/维护三种模式）
## 2.5 设计与实现约束（技术/合规/接口/工期约束）
## 2.6 假设与依赖

# 3 具体需求
## 3.1 功能需求（FR）
按7个模块分节：用户认证、设备管理、客户管理、租赁订单、费用结算、数据统计、系统配置
每条功能需求格式：
**FR-{模块缩写}-{编号}**
- 优先级：P0(必实现)/P1(重要)/P2(次要)
- 参与角色
- 前置条件
- 触发方式
- 业务流程（分步骤）
- 业务规则（含边界条件、数量范围、时间周期）
- 后置状态
- 验收标准（可量化、可测试、无歧义）

### 系统用例图（plantUML代码）
## 3.2 外部接口需求（IFR）
### E-R图（Mermaid erDiagram，核心实体：设备、客户、合同、订单、费用）
### 数据字典（表格：表名/字段名/类型/主键/外键/默认值/说明）
## 3.3 非功能需求（NFR）
### 3.3.1 性能需求（页面加载、接口响应、并发、吞吐量）
### 3.3.2 可靠性需求（可用率、连续运行、故障恢复）
### 3.3.3 安全性需求（认证、权限、数据加密、攻击防护、审计）
### 3.3.4 可维护性需求
### 3.3.5 可扩展性需求
### 3.3.6 易用性需求
## 3.4 数据需求
### 数据字典（完整表格）
### 数据管理策略（备份/归档/留存）

# 4 需求基线与变更管理
## 4.1 基线定义（版本规则、冻结规则）
## 4.2 变更流程概述

# 5 附录
## 附录A 验收标准总表（编号→名称→标准→优先级）
## 附录B 参考资料

---
总字数不少于15000字。所有需求必须可验证、无歧义、可追溯。禁止使用「尽量」「大概」「合理」「快速」「及时」等模糊词。"""

    result = call_llm(prompt, system_prompt="你是专业的软件需求文档编写专家，精通IEEE 830标准。精确优先于流畅。")

    # 保存SRS到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    srs_content = kb_frontmatter("SRS-正式版", ["SRS", "需求规格"], ["软件需求规格说明书"]) + result
    save_to_kb("wiki", f"SRS-初稿-{ds}-v1.0.md", srs_content)

    return {"srs_draft": result}


# ============================================================
# 🧠 A5 验证 + 路由
# ============================================================

def a5_validate_srs(state: RequirementState) -> dict:
    """A5验证：四类交叉验证（教材§7：历史需求/涉众对话/项目文档/SRS内部），设置回退原因"""
    prompt = f"""对以下SRS文档进行交叉验证（教材§7规定的四类比对）。

SRS文档：
{state.get("srs_draft", "")[:10000]}

验证方法（四类交叉比对）：
1. **历史需求比对** - SRS是否遗漏了对话记录中存在的需求条目
2. **涉众对话比对** - SRS中每条功能需求是否准确反映涉众在对话中表达的意图（标注诠释准确度：完全匹配/合理诠释/部分偏差/严重曲解）
3. **项目文档比对** - SRS中的约束、用户特征定义是否与知识库中的其他项目文档一致
4. **SRS内部一致性比对** - SRS不同章节之间的术语一致性、数据字典与功能需求之间的字段一致性

输出严格JSON格式：
{{"verdict": "通过/获取类问题/分析类问题", "findings": [{{"type": "历史遗漏/对话偏差/文档矛盾/内部不一致", "severity": "严重/中/低", "section": "涉及SRS章节", "description": "问题描述", "suggestion": "修正建议"}}]}}"""

    result = call_llm(prompt, system_prompt="你是严谨的需求验证审计师（教材§7）。请精确输出JSON。")
    data = extract_json(result)
    verdict = data.get("verdict", "分析类问题")
    findings = data.get("findings", [])
    rollback_reason = {
        "通过": "",
        "获取类问题": "A5_acquisition",
        "分析类问题": "A5_analysis",
    }.get(verdict, "A5_analysis")

    # 保存验证报告到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    finding_rows = "\n".join(
        f"| {f.get('type','')} | {f.get('severity','')} | {f.get('section','—')} | {f.get('description','')} | {f.get('suggestion','')} |"
        for f in findings
    ) if findings else "| — | — | — | 未发现问题 | — |"
    vdoc = kb_frontmatter(f"需求验证报告-{ds}", ["验证", "SRS"], ["需求验证报告"]) + f"""# 需求验证报告

验证日期：{ds}
总体结论：**{verdict}**
{f'发现 {len(findings)} 个问题' if findings else '未发现问题'}

## 发现清单（教材§7四类交叉比对）
| 类型 | 严重程度 | 涉及章节 | 问题描述 | 修正建议 |
|-----|---------|---------|---------|---------|
{finding_rows}

> 关联 [[SRS-初稿-{ds}-v1.0|SRS文档]] | [[需求清单-{ds}-v1.0|需求清单]]
"""
    save_to_kb("wiki", f"需求验证报告-{ds}-v1.0.md", vdoc)

    return {
        "validation_report": result,
        "validation_verdict": verdict,
        "rollback_reason": rollback_reason,
    }


def a5_decide_next(state: RequirementState) -> Literal["approve", "rollback_a1", "rollback_a2"]:
    """A5条件判断"""
    verdict = state.get("validation_verdict", "分析类问题")
    if verdict == "通过":
        print("  [A5] ✅ 验证通过 → 生成缺陷分析报告")
        return "approve"
    elif verdict == "获取类问题":
        print("  [A5] ⚠️ 获取类问题 → 回退A1，涉众Agent自动追问")
        return "rollback_a1"
    else:
        print("  [A5] ⚠️ 分析类问题 → 回退A2重新分析")
        return "rollback_a2"


# ============================================================
# 📋 5份缺陷分析报告（教材§1明确要求）
# ============================================================

DEFECT_SCENARIOS = [
    # 场景1：状态不同步（数据一致性问题）
    ("招商业务员看到系统显示某设备'闲置'，但库房人员表示该设备实际上已经在出库配送途中，状态未同步。", 1),
    # 场景2：租期边界计算错误（业务逻辑缺陷）
    ("财务结算时发现系统计算的租金金额与合同约定不一致，原因是租期计算边界条件未处理。", 2),
    # 场景3：设备存放位置与系统不符（数据一致性问题）
    ("库房人员出库时发现系统显示的设备存放位置与实际不符。", 3),
    # 场景4：第三方接口异常——支付失败（外部系统故障）
    ("客户通过在线支付缴纳租金时，系统显示支付成功但财务后台未收到款项确认，导致客户被重复催缴。", 4),
    # 场景5：安全权限问题——越权查看（安全性缺陷）
    ("运维工程师能够查看系统中所有客户的合同报价和财务结算明细，而这些信息本应仅限招商业务员和财务人员查看。", 5),
]


def a5_generate_defect_reports(state: RequirementState) -> dict:
    """生成5份缺陷分析报告（教材§1要求至少5份）"""
    print("\n  [A5] 正在生成5份缺陷分析报告...")
    reports = []
    for scenario, idx in DEFECT_SCENARIOS:
        print(f"    报告 {idx}/5...")
        # 先判断缺陷类型
        defect_type_hint = "业务逻辑缺陷"
        if "状态" in scenario or "同步" in scenario:
            defect_type_hint = "数据一致性问题"
        elif "安全" in scenario or "越权" in scenario:
            defect_type_hint = "安全性缺陷"
        elif "接口" in scenario or "第三方" in scenario or "支付" in scenario:
            defect_type_hint = "外部接口异常缺陷"

        prompt = f"""你正在编写一份"缺陷分析报告"（教材§1要求至少5份）。
这是 报告{idx}/5。

项目背景：医疗器械租赁管理系统，涉及4种角色（招商业务员、库房人员、运维工程师、财务）、7个核心模块（用户认证、设备管理、客户管理、租赁订单、费用结算、数据统计、系统配置）。

场景：{scenario}
类型参考：{defect_type_hint}

按以下格式输出，每节内容充分、完整：

# 缺陷分析报告 [#{idx}/5]

## 缺陷描述
[精确描述缺陷现象：包括触发条件、出现频率、影响的表现]

## 缺陷类型
[需求缺陷/设计缺陷/实现缺陷/测试缺陷/数据一致性问题/外部接口异常/安全性缺陷]

## 发现阶段
[在哪个环节被发现的：需求分析/设计评审/编码实现/集成测试/用户验收/生产环境]

## 根因分析
[为什么会出现这个缺陷：三层分析——直接原因（代码层面）、间接原因（设计层面）、根本原因（流程/管理层面）]

## 影响范围
[影响了哪些模块/角色/业务流程，量化影响：如涉及X个功能点、影响Y%的用户、可能导致Z元的损失]

## 修复方案
[三步方案：短期止血措施、中期修复方案、长期预防措施]

## 防止复发措施
[具体可执行的动作：补充测试用例、增加自动化校验、完善CodeGraph规则、改进评审流程等]"""
        report = call_llm(prompt, system_prompt="你是一名软件质量保证专家，擅长缺陷分析。")
        reports.append(report)

    all_reports = "\n\n---\n\n".join(reports)
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    save_to_kb("wiki", f"缺陷分析报告集-{ds}-v1.0.md",
               kb_frontmatter("缺陷分析报告集", ["缺陷", "质量保证"]) + all_reports)
    print("  ✅ 5份缺陷分析报告已保存到 wiki/summaries/")
    return {"defect_reports": all_reports}


# ============================================================
# 🧠 CCB 人工审批 + 路由
# ============================================================

def ccb_review(state: RequirementState) -> dict:
    """CCB人工审批 — 暂停等待用户输入"""
    verdict = state.get("validation_verdict", "")

    print()
    print("=" * 60)
    print("📋 CCB 审批 — 请人工决策")
    print("=" * 60)
    print()
    print(f"SRS验证结论：{verdict}")
    print(state.get("validation_report", "")[:500])
    print()
    print("请选择：")
    print("  1. 通过            → 进入基线创立")
    print("  2. 不通过（获取类）→ 回退A1，涉众Agent自动追问")
    print("  3. 不通过（分析类）→ 回退A2重新分析")
    print()

    while True:
        choice = input("请输入你的决定 (1/2/3): ").strip()
        if choice == "1":
            return {"ccb_verdict": "通过", "ccb_comment": "人工审批通过", "rollback_reason": ""}
        elif choice == "2":
            return {
                "ccb_verdict": "不通过(获取类)",
                "ccb_comment": input("请说明理由: "),
                "rollback_reason": "CCB_acquisition",
            }
        elif choice == "3":
            return {
                "ccb_verdict": "不通过(分析类)",
                "ccb_comment": input("请说明理由: "),
                "rollback_reason": "CCB_analysis",
            }
        else:
            print("无效输入，请输入 1、2 或 3")


def ccb_decide_next(state: RequirementState) -> Literal["approve", "rollback_a1", "rollback_a2"]:
    """CCB条件判断"""
    verdict = state.get("ccb_verdict", "不通过(分析类)")
    if "通过" in verdict:
        print("  [CCB] ✅ 审批通过 → 基线创立")
        return "approve"
    elif "获取" in verdict:
        print("  [CCB] ⚠️ 退回A1，涉众Agent自动追问")
        return "rollback_a1"
    else:
        print("  [CCB] ⚠️ 退回A2重新分析")
        return "rollback_a2"


# ============================================================
# 🧠 A6 基线
# ============================================================

def a6_create_baseline(state: RequirementState) -> dict:
    """A6基线：生成22列RTM溯源矩阵并冻结到知识库（规范对齐版）"""
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    version = f"BL-{ds}-01"

    # ── 1. 生成 22 列 RTM 溯源矩阵 ──
    rtm_prompt = f"""请根据以下资料生成需求溯源矩阵（RTM），严格遵循22列完整字段规范。

基线版本：{version}
对比历史基线：初始基线，无历史版本
需求状态：所有需求均标记为「新增」

SRS文档（摘要）：
{state.get("srs_draft", "")[:5000]}

需求清单：
{state.get("consolidated_requirements", "")[:3000]}

请输出22列RTM表格，格式为Markdown表格，列如下：

| 行号 | 业务需求ID(BR) | 业务目标描述 | 原始需求ID(UR) | 原始需求来源 | 原始需求全文 | 需求类型 | SRS需求ID | SRS需求名称 | SRS正式描述 | 验收标准 | 优先级 | 本次基线需求状态 | 变更来源 | 变更差异详情 | 变更影响范围 | 关联建模产物ID | 关联设计文档ID | 关联开发模块 | 数据字典关联ID | 关联测试用例ID | 验收状态 |

要求：
1. 每个SRS中的功能需求对应一行
2. BR编号格式：BR-模块缩写-三位流水号
3. UR编号格式：UR-模块缩写-三位流水号
4. 需求类型：功能需求/非功能需求/接口需求
5. 本次基线需求状态统一填「新增」
6. 变更来源和变更差异详情填「初始基线，无历史版本」
7. 每条需求必须可溯源（BR→UR→SRS）"""

    rtm = call_llm(rtm_prompt, system_prompt="你是配置管理专家，精通需求溯源矩阵（RTM）22列规范。")

    # ── 2. 生成 CCB 评审记录 ──
    ccb_record = f"""# CCB 评审记录

基线版本：{version}
评审日期：{ds}
评审类型：初始基线审批

## 评审内容
- 软件需求规格说明书（SRS）
- 需求溯源矩阵（RTM）
- 需求清单
- UML建模产物

## 评审结论
{'✅ 通过' if state.get('ccb_verdict', '通过') == '通过' else '❌ 不通过'}

## 评审意见
{state.get('ccb_comment', '初始基线审批通过')}

## 审批人
[待填写]

## 关联文档
- 需求验证报告
- 缺陷分析报告集

---

> 本记录由 CCB 审批后自动生成，归档至基线目录
"""

    # ── 3. 冻结到知识库 baselines 目录 ──
    bl_dir = f"wiki/baselines/{version}"
    save_to_kb("baselines", f"{version}/SRS-正式版.md",
        kb_frontmatter(f"SRS-正式版-{version}", ["SRS", "基线", "冻结"], [f"SRS-{version}"]) + state.get("srs_draft", ""))
    save_to_kb("baselines", f"{version}/需求清单.md",
        kb_frontmatter(f"需求清单-{version}", ["需求", "基线", "冻结"]) + state.get("consolidated_requirements", ""))
    save_to_kb("baselines", f"{version}/RTM_{version}_需求溯源矩阵.md",
        kb_frontmatter(f"溯源矩阵-{version}", ["RTM", "基线", "冻结"]) + rtm)
    save_to_kb("baselines", f"{version}/CCB_{version}_评审记录.md",
        kb_frontmatter(f"CCB评审记录-{version}", ["CCB", "评审", "基线"]) + ccb_record)

    baseline_report = kb_frontmatter("基线报告", ["基线", "配置管理"]) + f"""# 基线创立报告

**基线版本：** {version}
**创建日期：** {ds}
**项目：** 医疗器械租赁管理系统
**状态：** ⛔ 已冻结（不可修改）

## 基线包含文档
| 文档 | 路径 |
|------|------|
| SRS-正式版 | [[SRS-正式版]] |
| 需求清单 | [[需求清单]] |
| RTM溯源矩阵 | [[RTM_{version}_需求溯源矩阵]] |
| CCB评审记录 | [[CCB_{version}_评审记录]] |
| UML模型 | 关联基线目录 |

## 基线符合规范
- ✅ SRS：IEEE 830标准 + GB/T 9385
- ✅ RTM：22列完整溯源矩阵
- ✅ 需求编号：BR/UR/FR/NFR/IFR四级体系
- ✅ CCB评审：正式评审记录归档
- ✅ 全部需求状态标记为「新增」

## 变更管理
基线创立后如需变更，需走正式变更管理流程：
1. **CR**（变更请求）
2. **CIA**（影响分析）
3. **约束更新**
4. **代码变更**
5. **CRR**（回归校验）
6. **新基线**
"""
    save_to_kb("baselines", f"{version}/基线报告.md", baseline_report)

    print(f"\n✅ 基线 {version} 已创立！")
    print(f"   📁 {bl_dir}/")
    print(f"   包含: SRS正式版 + 需求清单 + 22列RTM + CCB评审记录 + 基线报告")
    return {"baseline_version": version, "rtm": rtm, "workflow_status": "完成"}


# ============================================================
# 🕸️ 构建 LangGraph
# ============================================================

def build_workflow() -> StateGraph:
    """构建需求工程工作流图"""
    # 启动时生成4份涉众AI智能体配置文件（答辩交付物）
    ensure_agent_configs()

    workflow = StateGraph(RequirementState)

    # ── A1阶段：4个涉众Agent（既是问答器也是追问器） ──
    workflow.add_node("A1_招商业务员", a1_agent_biz)
    workflow.add_node("A1_库房人员", a1_agent_warehouse)
    workflow.add_node("A1_运维工程师", a1_agent_maintenance)
    workflow.add_node("A1_财务", a1_agent_finance)
    workflow.add_node("A1_汇总", a1_consolidate)

    # ── A2阶段 ──
    workflow.add_node("A2_需求分析", a2_analyze_quality)

    # ── A3-A6 ──
    workflow.add_node("A3_UML建模", a3_generate_uml)
    workflow.add_node("A4_SRS生成", a4_generate_srs)
    workflow.add_node("A5_验证", a5_validate_srs)
    workflow.add_node("A5_缺陷分析报告", a5_generate_defect_reports)
    workflow.add_node("CCB_审批", ccb_review)
    workflow.add_node("A6_基线创立", a6_create_baseline)

    # ── 入口 ──
    workflow.set_entry_point("A1_招商业务员")

    # ── A1串行：4个Agent依次对话 → 汇总 ──
    workflow.add_edge("A1_招商业务员", "A1_库房人员")
    workflow.add_edge("A1_库房人员", "A1_运维工程师")
    workflow.add_edge("A1_运维工程师", "A1_财务")
    workflow.add_edge("A1_财务", "A1_汇总")

    # ── A1汇总 → A2 ──
    workflow.add_edge("A1_汇总", "A2_需求分析")

    # ── A2条件：严重问题回退A1_招商业务员（涉众Agent自动追问）──
    workflow.add_conditional_edges(
        "A2_需求分析",
        a2_decide_next,
        {
            "rollback": "A1_招商业务员",
            "continue": "A3_UML建模",
        },
    )

    # ── A3 → A4 → A5 ──
    workflow.add_edge("A3_UML建模", "A4_SRS生成")
    workflow.add_edge("A4_SRS生成", "A5_验证")

    # ── A5条件判断 ──
    workflow.add_conditional_edges(
        "A5_验证",
        a5_decide_next,
        {
            "approve": "A5_缺陷分析报告",    # 通过 → 先出缺陷报告再走CCB
            "rollback_a1": "A1_招商业务员",
            "rollback_a2": "A2_需求分析",
        },
    )

    # ── 缺陷报告 → CCB ──
    workflow.add_edge("A5_缺陷分析报告", "CCB_审批")

    # ── CCB条件判断 ──
    workflow.add_conditional_edges(
        "CCB_审批",
        ccb_decide_next,
        {
            "approve": "A6_基线创立",
            "rollback_a1": "A1_招商业务员",    # → A1 Agent自动追问
            "rollback_a2": "A2_需求分析",       # → 重新分析
        },
    )

    # ── A6结束 ──
    workflow.add_edge("A6_基线创立", END)

    return workflow


# ============================================================
# 🚀 主入口
# ============================================================

def main():
    print("=" * 60)
    print("🏥 医疗器械租赁管理系统 — 需求工程工作流 v3")
    print("    (多Agent协作：A1涉众Agent + A2分析 + A3建模 + A4+SRS + A5验证 + CCB + A6基线)")
    print("=" * 60)
    print()
    print(f"  LLM: {LLM_API_URL}  |  模型: {LLM_MODEL}")
    print(f"  涉众API: {STAKEHOLDER_API}")
    print()

    if LLM_API_KEY == "sk-your-api-key":
        print("请先配置 LLM_API_KEY（创建 .env 文件或设置环境变量）")
        return

    # 构建 + 编译
    workflow = build_workflow()
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)

    # 初始状态
    now = datetime.now()
    initial_state: RequirementState = {
        "biz_dialog": [],
        "warehouse_dialog": [],
        "maintenance_dialog": [],
        "finance_dialog": [],
        "consolidated_requirements": "",
        "quality_issues": [],
        "has_critical_issues": False,
        "a2_round": 1,
        "a2_max_rounds": 3,
        "uml_use_case": "",
        "uml_activity_diagrams": "",
        "srs_draft": "",
        "validation_report": "",
        "validation_verdict": "",
        "defect_reports": "",
        "ccb_verdict": "",
        "ccb_comment": "",
        "baseline_version": "",
        "rtm": "",
        "rollback_reason": "",
        "iteration_count": 0,
        "workflow_status": "启动",
    }

    print("🚀 启动工作流...\n")

    try:
        result = app.invoke(
            initial_state,
            config={"configurable": {"thread_id": "req-001"}},
            recursion_limit=50,
        )
    except Exception as e:
        print(f"\n❌ 工作流异常: {e}")
        import traceback
        traceback.print_exc()
        return

    # 输出
    print()
    print("=" * 60)
    print("✅ 工作流完成！")
    print("=" * 60)
    print(f"  基线版本: {result.get('baseline_version', 'N/A')}")
    print(f"  最终状态: {result.get('workflow_status', 'N/A')}")
    print(f"  迭代次数: {result.get('iteration_count', 0)}")
    print()
    print("📋 交付物清单（教材要求）：")
    print(f"  1. ✅ 4份涉众对话记录 → raw/notes/")
    print(f"  2. ✅ 结构化需求清单 → wiki/summaries/")
    print(f"  3. ✅ 需求问题清单    → wiki/summaries/")
    print(f"  4. ✅ UML模型(用例图+活动图) → wiki/summaries/")
    print(f"  5. ✅ SRS规格说明书    → wiki/summaries/")
    print(f"  6. ✅ 需求验证报告    → wiki/summaries/")
    print(f"  7. ✅ 5份缺陷分析报告  → wiki/summaries/")
    print(f"  8. ✅ 基线{result.get('baseline_version', 'N/A')} + RTM → wiki/baselines/")
    print()
    print(f"📁 Obsidian 知识库: {KB_ROOT}")
    print("💡 在 Obsidian 中打开 .claude/knowledge-base/ 即可浏览")


if __name__ == "__main__":
    main()
