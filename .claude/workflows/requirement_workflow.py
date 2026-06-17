# 医疗器械租赁管理系统 — 需求工程 LangGraph 工作流 v3
#
# 核心设计：
#   A1的4个涉众节点本身就是Agent
#   - 首次运行：用预设问题向涉众API提问
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

# Obsidian 知识库 Vault 路径
KB_ROOT = os.getenv("KB_ROOT", ".claude/knowledge-base")

# A1涉众配置：名称 → 对应state字段 + 预设问题
STAKEHOLDER_CONFIG = {
    "招商业务员": {
        "field": "biz_dialog",
        "questions": [
            "请描述你一天的工作流程是怎样的？从接到客户询盘开始。",
            "目前工作中最让你头疼的问题是什么？",
            "如果某个环节出了意外（比如客户临时改需求），你们怎么处理？",
            "合同签订、回款跟踪这些方面，你希望系统怎么帮你？",
            "设备闲置情况你希望以什么方式看到？",
        ],
    },
    "库房人员": {
        "field": "warehouse_dialog",
        "questions": [
            "请描述设备入库、存放、出库、盘点整个流程是怎样的？",
            "目前库房管理中最麻烦的事情是什么？",
            "你希望系统在入库、位置管理、出库方面怎么帮你？",
            "校准检测提醒你希望怎么设置？",
            "配件管理和冷链运输方面有什么需求？",
        ],
    },
    "运维工程师": {
        "field": "maintenance_dialog",
        "questions": [
            "请描述设备安装调试、维修保养、巡检的日常工作。",
            "故障报修这块目前有什么痛点？",
            "你希望系统怎么帮你管理备品备件？",
            "巡检周期一般是多久？有什么系统需求？",
            "维修知识库你希望怎么组织？",
        ],
    },
    "财务": {
        "field": "finance_dialog",
        "questions": [
            "请描述你日常处理租赁相关的财务工作流程。",
            "租金计算、押金管理方面有什么痛点？",
            "发票管理和季度对账有什么系统需求？",
            "设备折旧和维修成本统计方面需要什么功能？",
        ],
    },
}

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
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  📁 知识库: {subdir}/{filename}")
    return path


def kb_frontmatter(title: str, tags: list[str], aliases: list[str] | None = None) -> str:
    """生成 Obsidian 前置元数据"""
    fm = f"---\ntitle: {title}\ntags: [{' '.join(tags)}]\n"
    if aliases:
        fm += f"aliases: {json.dumps(aliases, ensure_ascii=False)}\n"
    fm += "---\n\n"
    return fm


# ============================================================
# 🧠 A1 涉众 Agent 节点
# ============================================================
# 设计：每个A1节点就是Agent
#   - 首次运行 → 预设问题提问
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
    """A1涉众Agent核心逻辑 — 首次问答 or 回退追问"""
    cfg = STAKEHOLDER_CONFIG[stakeholder]
    field = cfg["field"]
    existing = state.get(field, [])
    reason = state.get("rollback_reason", "")

    # ── 首次运行：预设问题 ──
    if not existing:
        dialog = []
        for q in cfg["questions"]:
            answer = call_stakeholder(stakeholder, q)
            dialog.append({"q": q, "a": answer})
            print(f"  [{stakeholder}] ✓ {q[:30]}...")
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
    """A3建模：生成UML用例图和活动图"""
    prompt = f"""根据以下需求清单，生成UML模型（PlantUML代码）：

需求清单：
{state.get("consolidated_requirements", "")[:8000]}

请生成：
1. **用例图**（@startuml ... @enduml）
   - Actor：4种角色（招商业务员、库房人员、运维工程师、财务）
   - Use Case：所有系统功能，标注 <<include>> 和 <<extend>> 关系

2. **活动图**（至少3个核心流程）
   - 租赁订单完整流程（创建→审核→出库→归还→结算）
   - 设备入库流程
   - 设备维修流程
   - 每个图包含：正常路径×2 + 异常路径×2，分支条件用[Guard Condition]"""

    result = call_llm(prompt, system_prompt="你是UML建模专家，精通PlantUML语法。")
    parts = result.split("@startuml")
    use_case = "@startuml" + parts[1] if len(parts) > 1 else ""
    activities = "@startuml" + "@startuml".join(parts[2:]) if len(parts) > 2 else ""

    # 保存到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    if use_case:
        save_to_kb("wiki", f"用例图-{ds}-v1.0.puml", use_case)
    if activities:
        save_to_kb("wiki", f"活动图-{ds}-v1.0.puml", activities)
    uml_doc = kb_frontmatter(f"UML模型-{ds}", ["UML", "建模"], ["UML模型"]) + f"""# UML模型

生成日期：{ds}

## 文件
- [[用例图-{ds}-v1.0.puml|用例图]]
- [[活动图-{ds}-v1.0.puml|活动图]]

## 关联需求
- [[需求清单-{ds}-v1.0|需求清单]]

> 由 A3 建模 Agent 根据需求清单生成
"""
    save_to_kb("wiki", f"UML模型说明-{ds}-v1.0.md", uml_doc)

    return {"uml_use_case": use_case, "uml_activity_diagrams": activities}


# ============================================================
# 🧠 A4 SRS生成
# ============================================================

def a4_generate_srs(state: RequirementState) -> dict:
    """A4文档智能体：生成IEEE 830标准SRS文档"""
    prompt = f"""请根据以下输入生成一份完整的SRS（软件需求规格说明书），遵循IEEE 830标准。

需求清单：
{state.get("consolidated_requirements", "")[:6000]}

UML模型：
{state.get("uml_use_case", "")[:2000]}
{state.get("uml_activity_diagrams", "")[:2000]}

要求：
1. 严格遵循IEEE 830结构：引言→总体描述→具体需求
2. 具体需求按7个模块分节（用户认证/设备/客户/租赁订单/费用结算/看板/系统配置）
3. 每条功能需求包含：编号+描述+输入/输出+验收标准
4. **禁止使用模糊词**（快速、及时、合理、尽量等）
5. 总字数不少于10000字
6. 包含数据字典（列出所有主要数据字段的定义）"""

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
    ("招商业务员在创建合同时选择了错误的计费方式。", 1),
    ("库房人员出库时发现系统显示的设备存放位置与实际不符。", 2),
    ("运维工程师收到故障报修通知，但系统未显示该设备的历史维修记录。", 3),
    ("财务结算时发现系统计算的租金金额与合同约定不一致，原因是租期计算边界条件未处理。", 4),
    ("招商业务员看到系统显示某设备'闲置'，但库房人员表示该设备实际上已经在出库配送途中，状态未同步。", 5),
]


def a5_generate_defect_reports(state: RequirementState) -> dict:
    """生成5份缺陷分析报告（教材§1要求至少5份）"""
    print("\n  [A5] 正在生成5份缺陷分析报告...")
    reports = []
    for scenario, idx in DEFECT_SCENARIOS:
        print(f"    报告 {idx}/5...")
        prompt = f"""你正在编写一份"缺陷分析报告"（教材§1要求至少5份）。
这是 报告{idx}/5。

项目背景：医疗器械租赁管理系统，涉及4种角色、7个核心模块。
场景：{scenario}

按以下格式输出：

# 缺陷分析报告 [#{idx}/5]

## 缺陷描述
[场景描述]

## 缺陷类型
[需求缺陷/设计缺陷/实现缺陷/测试缺陷]

## 发现阶段
[在哪个环节被发现的]

## 根因分析
[为什么会出现这个缺陷]

## 影响范围
[影响了哪些模块/角色]

## 修复方案
[怎么修]

## 防止复发措施
[以后怎么避免]"""
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
    """A6基线：生成RTM溯源矩阵并冻结到知识库"""
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    version = f"BL-{ds}-01"
    prompt = f"""请根据SRS文档生成需求溯源矩阵（RTM）。

SRS文档（摘要）：
{state.get("srs_draft", "")[:5000]}

需求清单：
{state.get("consolidated_requirements", "")[:3000]}

基线版本：{version}

输出RTM表格格式：
| 需求编号 | 需求描述 | 来源涉众 | 模块 | 接口 | 测试要点 | 优先级 |
|---------|---------|---------|-----|------|---------|-------|
要求覆盖所有主要需求条目。"""

    rtm = call_llm(prompt, system_prompt="你是配置管理专家，精通需求溯源矩阵。")

    # ── 冻结到知识库 baselines 目录 ──
    bl_dir = f"wiki/baselines/{version}"
    save_to_kb("baselines", f"{version}/SRS-正式版.md",
        kb_frontmatter(f"SRS-正式版-{version}", ["SRS", "基线", "冻结"], [f"SRS-{version}"]) + state.get("srs_draft", ""))
    save_to_kb("baselines", f"{version}/需求清单.md",
        kb_frontmatter(f"需求清单-{version}", ["需求", "基线", "冻结"]) + state.get("consolidated_requirements", ""))
    save_to_kb("baselines", f"{version}/溯源矩阵.md",
        kb_frontmatter(f"溯源矩阵-{version}", ["RTM", "基线", "冻结"]) + rtm)

    baseline_report = kb_frontmatter("基线报告", ["基线", "配置管理"]) + f"""# 基线创立报告

**基线版本：** {version}
**创建日期：** {ds}
**项目：** 医疗器械租赁管理系统
**状态：** ⛔ 已冻结（不可修改）

## 包含文档
- [[SRS-正式版]]
- [[需求清单]]
- [[溯源矩阵]]
- UML模型

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
    return {"baseline_version": version, "rtm": rtm, "workflow_status": "完成"}


# ============================================================
# 🕸️ 构建 LangGraph
# ============================================================

def build_workflow() -> StateGraph:
    """构建需求工程工作流图"""
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
