# 医疗器械租赁管理系统 — 需求工程 LangGraph 工作流
#
# 使用方法：
#   1. 安装依赖：pip install langgraph langchain-openai python-dotenv requests
#   2. 创建 .env 文件，填入你的 API Key
#   3. 运行：python requirement_workflow.py
#
# 工作流步骤：
#   A1(4个涉众对话) → A1汇总 → A2需求分析 → A3建模 → A4写SRS → A5验证 → CCB审批 → A6基线
#   有问题自动回退到对应上游节点

import os
import json
import requests
from typing import TypedDict, List, Optional, Literal
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.checkpoint import MemorySaver
from langgraph.types import Command
from typing_extensions import TypedDict

# ============================================================
# ⚙️ 配置（从 .env 或环境变量读取）
# ============================================================

# LLM 配置：默认用 OpenAI 格式，换 Claude 改这俩变量即可
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-your-api-key")
LLM_API_URL = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

# 老师平台的涉众API
STAKEHOLDER_API = "https://210.34.148.101:5000/api/chat"
PROJECT_ID = "10"

# ============================================================
# 📦 工作流状态定义
# ============================================================

class RequirementState(TypedDict):
    """工作流的全局状态，每个节点读取和写入"""
    # A1阶段：四个涉众的原始对话记录
    biz_dialog: List[dict]           # 招商业务员对话记录 [{q, a}, ...]
    warehouse_dialog: List[dict]     # 库房人员对话记录
    maintenance_dialog: List[dict]   # 运维工程师对话记录
    finance_dialog: List[dict]       # 财务对话记录

    # A1汇总：合并后的需求
    consolidated_requirements: str

    # A2阶段：质量分析
    quality_issues: List[dict]       # 问题清单 [{type, severity, desc}, ...]
    has_critical_issues: bool        # 是否有严重问题需要回退

    # A3阶段：UML模型
    uml_use_case: str                # 用例图 PlantUML 代码
    uml_activity_diagrams: str       # 活动图 PlantUML 代码

    # A4阶段：SRS文档
    srs_draft: str                   # SRS文档全文

    # A5阶段：验证报告
    validation_report: str
    validation_verdict: str          # "通过" / "获取类问题" / "分析类问题"
    rollback_target: str             # 回退目标节点

    # CCB阶段：人工审批
    ccb_verdict: str                 # "通过" / "不通过(获取类)" / "不通过(分析类)"
    ccb_comment: str                 # 审批意见

    # A6阶段：基线
    baseline_version: str            # BL-20260615-01
    rtm: str                         # 溯源矩阵

    # 工作流控制
    iteration_count: int             # 迭代次数（防止死循环）
    workflow_status: str             # 当前状态描述


# ============================================================
# 🛠️ 工具函数
# ============================================================

def call_llm(prompt: str, system_prompt: str = "") -> str:
    """调用大语言模型（兼容 OpenAI / Claude / Ollama 格式）"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}"
    }

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 16000
    }

    try:
        resp = requests.post(LLM_API_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[LLM调用失败: {e}]"


def call_stakeholder(stakeholder: str, question: str) -> str:
    """调用老师平台的涉众智能体API"""
    payload = {
        "project_id": PROJECT_ID,
        "stakeholder": stakeholder,
        "phase": "v1",
        "question": question
    }
    try:
        resp = requests.post(
            STAKEHOLDER_API,
            json=payload,
            verify=False,
            timeout=60
        )
        resp.raise_for_status()
        return resp.json().get("answer", "")
    except Exception as e:
        return f"[涉众API调用失败: {e}]"


# ============================================================
# 🧠 Node 节点定义（每个节点是一个函数）
# ============================================================

def a1_talk_to_biz(state: RequirementState) -> dict:
    """A1-招商业务员：对话获取需求"""
    questions = [
        "请描述你一天的工作流程是怎样的？从接到客户询盘开始。",
        "目前工作中最让你头疼的问题是什么？",
        "如果某个环节出了意外（比如客户临时改需求），你们怎么处理？",
        "合同签订、回款跟踪这些方面，你希望系统怎么帮你？",
        "设备闲置情况你希望以什么方式看到？"
    ]
    dialog = []
    for q in questions:
        answer = call_stakeholder("招商业务员", q)
        dialog.append({"q": q, "a": answer})
        print(f"  [招商业务员] Q: {q[:30]}... → 已回复")
    return {"biz_dialog": dialog}


def a1_talk_to_warehouse(state: RequirementState) -> dict:
    """A1-库房人员：对话获取需求"""
    questions = [
        "请描述设备入库、存放、出库、盘点整个流程是怎样的？",
        "目前库房管理中最麻烦的事情是什么？",
        "你希望系统在入库、位置管理、出库方面怎么帮你？",
        "校准检测提醒你希望怎么设置？",
        "配件管理和冷链运输方面有什么需求？"
    ]
    dialog = []
    for q in questions:
        answer = call_stakeholder("库房人员", q)
        dialog.append({"q": q, "a": answer})
    return {"warehouse_dialog": dialog}


def a1_talk_to_maintenance(state: RequirementState) -> dict:
    """A1-运维工程师：对话获取需求"""
    questions = [
        "请描述设备安装调试、维修保养、巡检的日常工作。",
        "故障报修这块目前有什么痛点？",
        "你希望系统怎么帮你管理备品备件？",
        "巡检周期一般是多久？有什么系统需求？",
        "维修知识库你希望怎么组织？"
    ]
    dialog = []
    for q in questions:
        answer = call_stakeholder("运维工程师", q)
        dialog.append({"q": q, "a": answer})
    return {"maintenance_dialog": dialog}


def a1_talk_to_finance(state: RequirementState) -> dict:
    """A1-财务：对话获取需求"""
    questions = [
        "请描述你日常处理租赁相关的财务工作流程。",
        "租金计算、押金管理方面有什么痛点？",
        "发票管理和季度对账有什么系统需求？",
        "设备折旧和维修成本统计方面需要什么功能？"
    ]
    dialog = []
    for q in questions:
        answer = call_stakeholder("财务", q)
        dialog.append({"q": q, "a": answer})
    return {"finance_dialog": dialog}


def a1_consolidate(state: RequirementState) -> dict:
    """A1汇总：合并四个涉众的需求为结构化需求清单"""
    all_dialogs = {
        "招商业务员": state.get("biz_dialog", []),
        "库房人员": state.get("warehouse_dialog", []),
        "运维工程师": state.get("maintenance_dialog", []),
        "财务": state.get("finance_dialog", [])
    }

    prompt = f"""你作为需求分析员，将以下四个涉众的对话记录整理为结构化的需求清单。

对话记录：
{json.dumps(all_dialogs, ensure_ascii=False, indent=2)}

请按以下格式输出：
1. 按功能模块分类（用户认证、设备管理、客户管理、租赁订单、费用结算、数据统计、系统配置）
2. 每条需求格式：REQ-{模块缩写}-{编号} | 涉众来源 | 需求描述 | 优先级
3. 标注每条需求的边界条件（如数量范围、时间周期、金额限制）"""

    result = call_llm(prompt, system_prompt="你是一名资深需求分析工程师，擅长将涉众对话整理为结构化需求。")
    return {"consolidated_requirements": result}


def a2_analyze_quality(state: RequirementState) -> dict:
    """A2需求分析：检测四类质量问题"""
    prompt = f"""对以下需求清单进行四维度质量检测：

{state.get("consolidated_requirements", "")}

检测维度：
1. **模糊** - 是否包含「尽量」「大概」「合理」「快速」等不可量化词语
2. **不一致** - 同一术语在不同地方是否有不同定义
3. **矛盾** - 两条需求是否在逻辑上无法同时成立
4. **冲突** - 不同涉众的互斥期望

输出格式（JSON，必须严格按此格式）：
{{
  "issues": [
    {{
      "type": "模糊/不一致/矛盾/冲突",
      "severity": "严重/中/低",
      "description": "问题描述",
      "source": "涉及涉众",
      "suggestion": "修正建议"
    }}
  ],
  "has_critical": true/false
}}"""

    result = call_llm(prompt, system_prompt="你是一名严格的需求质量审查专家。请精确输出JSON。")

    # 尝试解析JSON
    try:
        # 提取JSON部分
        json_start = result.find("{")
        json_end = result.rfind("}") + 1
        if json_start >= 0:
            data = json.loads(result[json_start:json_end])
            issues = data.get("issues", [])
            has_critical = data.get("has_critical", False)
            return {
                "quality_issues": issues,
                "has_critical_issues": has_critical
            }
    except:
        pass

    return {
        "quality_issues": [{"type": "解析错误", "severity": "中", "description": "LLM输出格式异常，请检查"}],
        "has_critical_issues": False
    }


def a2_decide_next(state: RequirementState) -> Literal["rollback", "continue"]:
    """A2条件判断：有严重问题回退A1，没问题继续"""
    if state.get("has_critical_issues", False):
        return "rollback"
    return "continue"


def a3_generate_uml(state: RequirementState) -> dict:
    """A3建模：生成UML用例图和活动图"""
    prompt = f"""根据以下需求清单，生成UML模型（PlantUML代码）：

需求清单：
{state.get("consolidated_requirements", "")[:8000]}

请生成：
1. **用例图**（@startuml ... @enduml）
   - Actor：4种角色（招商业务员、库房人员、运维工程师、财务）
   - Use Case：系统所有功能
   - 关系：关联、<<include>>、<<extend>>

2. **活动图**（至少3个核心流程）
   - 租赁订单完整流程（创建→审核→出库→归还→结算）
   - 设备入库流程
   - 设备维修流程
   - 每个图包含：正常路径×2 + 异常路径×2，分支条件用[Guard Condition]"""

    result = call_llm(prompt, system_prompt="你是UML建模专家，精通PlantUML语法。")

    # 简单拆分用例图和活动图
    parts = result.split("@startuml")
    use_case = ""
    activities = ""
    if len(parts) > 1:
        use_case = "@startuml" + parts[1] if len(parts) > 1 else ""
        activities = "@startuml" + "@startuml".join(parts[2:]) if len(parts) > 2 else ""

    return {
        "uml_use_case": use_case,
        "uml_activity_diagrams": activities
    }


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
    return {"srs_draft": result}


def a5_validate_srs(state: RequirementState) -> dict:
    """A5验证智能体：交叉验证SRS"""
    prompt = f"""对以下SRS文档进行交叉验证。

SRS文档：
{state.get("srs_draft", "")[:10000]}

验证方法（四类比对）：
1. **涉众对话比对** - SRS需求是否与原始对话记录一致
2. **内部一致性** - SRS不同章节术语定义是否一致
3. **覆盖度检查** - 所有功能点是否有对应需求描述
4. **精确性检查** - 是否有模糊词或不完整约束

输出格式（JSON）：
{{
  "verdict": "通过/获取类问题/分析类问题",
  "findings": [
    {{
      "type": "不一致/遗漏/模糊/错误",
      "severity": "严重/中/低",
      "section": "涉及SRS章节",
      "description": "问题描述",
      "suggestion": "修正建议"
    }}
  ],
  "rollback_target": "A1/A2/无"
}}"""

    result = call_llm(prompt, system_prompt="你是严谨的需求验证审计师。请精确输出JSON。")

    # 解析JSON
    try:
        json_start = result.find("{")
        json_end = result.rfind("}") + 1
        if json_start >= 0:
            data = json.loads(result[json_start:json_end])
            return {
                "validation_report": result,
                "validation_verdict": data.get("verdict", "分析类问题"),
                "rollback_target": data.get("rollback_target", "A2")
            }
    except:
        pass

    return {
        "validation_report": result,
        "validation_verdict": "分析类问题",
        "rollback_target": "A2"
    }


def a5_decide_next(state: RequirementState) -> Literal["approve", "rollback_a1", "rollback_a2"]:
    """A5条件判断：通过→CCB，获取类问题→回A1，分析类问题→回A2"""
    verdict = state.get("validation_verdict", "分析类问题")
    if verdict == "通过":
        return "approve"
    elif verdict == "获取类问题":
        return "rollback_a1"
    else:
        return "rollback_a2"


def ccb_review(state: RequirementState) -> dict:
    """CCB人工审批节点 —— 暂停等待用户输入"""
    print()
    print("=" * 60)
    print("📋 CCB 审批 — 请人工决策")
    print("=" * 60)
    print()
    print("SRS验证报告摘要：")
    print(state.get("validation_report", "")[:500])
    print()
    print("请选择：")
    print("  1. 通过 → 进入基线创立")
    print("  2. 不通过（获取类）→ 退回A1重新对话")
    print("  3. 不通过（分析类）→ 退回A2重新分析")
    print()

    # 从命令行读取用户输入
    while True:
        choice = input("请输入你的决定 (1/2/3): ").strip()
        if choice == "1":
            return {"ccb_verdict": "通过", "ccb_comment": "人工审批通过"}
        elif choice == "2":
            return {"ccb_verdict": "不通过(获取类)", "ccb_comment": input("请说明理由: ")}
        elif choice == "3":
            return {"ccb_verdict": "不通过(分析类)", "ccb_comment": input("请说明理由: ")}
        else:
            print("无效输入，请输入 1、2 或 3")


def ccb_decide_next(state: RequirementState) -> Literal["approve", "rollback_a1", "rollback_a2"]:
    """CCB条件判断"""
    verdict = state.get("ccb_verdict", "不通过(分析类)")
    if "通过" in verdict:
        return "approve"
    elif "获取" in verdict:
        return "rollback_a1"
    else:
        return "rollback_a2"


def a6_create_baseline(state: RequirementState) -> dict:
    """A6基线智能体：创立基线+生成RTM"""
    version = f"BL-{datetime.now().strftime('%Y%m%d')}-01"

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

    print(f"\n✅ 基线 {version} 已创立！")
    print(f"📍 RTM溯源矩阵已生成")

    return {
        "baseline_version": version,
        "rtm": rtm,
        "workflow_status": "完成"
    }


def a1_rollback_retry(state: RequirementState) -> dict:
    """回退到A1后的补充追问"""
    previous_issues = state.get("quality_issues", [])
    if not previous_issues:
        # 来自CCB或A5回退
        previous_issues = [{"description": "CCB/A5要求补充需求"}]

    prompt = f"""根据以下需求问题清单，生成需要补充追问的问题清单：

问题清单：
{json.dumps(previous_issues, ensure_ascii=False, indent=2)}

为每个问题生成一句可以向涉众追问的自然语言问题。
注意：要把需求分析的术语（"模糊""不一致"）翻译成涉众能理解的语言。"""

    questions = call_llm(prompt, system_prompt="你擅长将技术问题转化为涉众易懂的追问。")

    print(f"\n⚠️ 回退到A1 — 需要补充追问涉众")
    print(f"追问问题建议：\n{questions[:500]}")
    print()

    # 这里可以手动补充对话
    print("请用 /stakeholder 命令补充对话后，按回车继续...")
    input()

    return {"iteration_count": state.get("iteration_count", 0) + 1}


def a2_rollback_retry(state: RequirementState) -> dict:
    """回退到A2后的重新分析"""
    print(f"\n⚠️ 回退到A2 — 根据验证报告重新分析需求")
    print(f"验证报告反馈：{state.get('validation_report', '')[:300]}")
    print(f"按回车继续重新分析...")
    input()
    return {"iteration_count": state.get("iteration_count", 0) + 1}


# ============================================================
# 🕸️ 构建 LangGraph
# ============================================================

def build_workflow() -> StateGraph:
    """构建需求工程工作流图"""

    workflow = StateGraph(RequirementState)

    # === 添加节点 ===
    # A1阶段：4个涉众对话（可以并行，但API可能限制，先串行）
    workflow.add_node("A1_招商业务员", a1_talk_to_biz)
    workflow.add_node("A1_库房人员", a1_talk_to_warehouse)
    workflow.add_node("A1_运维工程师", a1_talk_to_maintenance)
    workflow.add_node("A1_财务", a1_talk_to_finance)
    workflow.add_node("A1_汇总", a1_consolidate)
    workflow.add_node("A1_补充追问", a1_rollback_retry)

    # A2阶段
    workflow.add_node("A2_需求分析", a2_analyze_quality)
    workflow.add_node("A2_重新分析", a2_rollback_retry)

    # A3阶段
    workflow.add_node("A3_UML建模", a3_generate_uml)

    # A4阶段
    workflow.add_node("A4_SRS生成", a4_generate_srs)

    # A5阶段
    workflow.add_node("A5_验证", a5_validate_srs)

    # CCB（人工）
    workflow.add_node("CCB_审批", ccb_review)

    # A6阶段
    workflow.add_node("A6_基线创立", a6_create_baseline)

    # === 设置入口 ===
    workflow.set_entry_point("A1_招商业务员")

    # === 添加边 ===
    # A1串行：4个涉众依次对话
    workflow.add_edge("A1_招商业务员", "A1_库房人员")
    workflow.add_edge("A1_库房人员", "A1_运维工程师")
    workflow.add_edge("A1_运维工程师", "A1_财务")
    workflow.add_edge("A1_财务", "A1_汇总")

    # A1汇总 → A2
    workflow.add_edge("A1_汇总", "A2_需求分析")

    # A2 → 条件判断
    workflow.add_conditional_edges(
        "A2_需求分析",
        a2_decide_next,
        {
            "rollback": "A1_补充追问",     # 严重问题 → 回A1追问
            "continue": "A3_UML建模"       # 没问题 → 继续建模
        }
    )

    # A1补充追问 → 回到A2重新分析
    workflow.add_edge("A1_补充追问", "A2_重新分析")
    workflow.add_edge("A2_重新分析", "A3_UML建模")  # 重分析后继续

    # A3 → A4
    workflow.add_edge("A3_UML建模", "A4_SRS生成")

    # A4 → A5
    workflow.add_edge("A4_SRS生成", "A5_验证")

    # A5 → 条件判断
    workflow.add_conditional_edges(
        "A5_验证",
        a5_decide_next,
        {
            "approve": "CCB_审批",         # 通过 → 人工审批
            "rollback_a1": "A1_补充追问",   # 获取类 → 回A1
            "rollback_a2": "A2_重新分析"    # 分析类 → 回A2
        }
    )

    # CCB → 条件判断
    workflow.add_conditional_edges(
        "CCB_审批",
        ccb_decide_next,
        {
            "approve": "A6_基线创立",
            "rollback_a1": "A1_补充追问",
            "rollback_a2": "A2_重新分析"
        }
    )

    # A6 → 结束
    workflow.add_edge("A6_基线创立", END)

    return workflow


# ============================================================
# 🚀 主入口
# ============================================================

def main():
    print("=" * 60)
    print("🏥 医疗器械租赁管理系统 — 需求工程工作流")
    print("=" * 60)
    print()
    print("配置检查：")
    print(f"  LLM API: {LLM_API_URL}")
    print(f"  Model: {LLM_MODEL}")
    print(f"  涉众API: {STAKEHOLDER_API}")
    print()

    if LLM_API_KEY == "sk-your-api-key":
        print("⚠️  请先配置 LLM_API_KEY")
        print("   创建 .env 文件或在环境变量中设置")
        print()
        choice = input("是否仍要继续？(y/n): ")
        if choice.lower() != "y":
            return

    # 构建工作流
    workflow = build_workflow()

    # 添加记忆检查点（支持暂停恢复）
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)

    # 初始状态
    initial_state = {
        "biz_dialog": [],
        "warehouse_dialog": [],
        "maintenance_dialog": [],
        "finance_dialog": [],
        "consolidated_requirements": "",
        "quality_issues": [],
        "has_critical_issues": False,
        "uml_use_case": "",
        "uml_activity_diagrams": "",
        "srs_draft": "",
        "validation_report": "",
        "validation_verdict": "",
        "rollback_target": "",
        "ccb_verdict": "",
        "ccb_comment": "",
        "baseline_version": "",
        "rtm": "",
        "iteration_count": 0,
        "workflow_status": "启动"
    }

    print("\n🚀 启动工作流...\n")

    # 执行工作流（设置递归限制防止死循环）
    try:
        result = app.invoke(initial_state, config={"configurable": {"thread_id": "req-001"}}, recursion_limit=50)
    except Exception as e:
        print(f"\n❌ 工作流异常: {e}")
        return

    # 输出结果
    print("\n" + "=" * 60)
    print("✅ 工作流完成！")
    print("=" * 60)
    print(f"\n基线版本: {result.get('baseline_version', 'N/A')}")
    print(f"最终状态: {result.get('workflow_status', 'N/A')}")
    print(f"迭代次数: {result.get('iteration_count', 0)}")

    # 保存产出物
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)

    with open(f"{output_dir}/需求清单.md", "w", encoding="utf-8") as f:
        f.write(result.get("consolidated_requirements", ""))
    with open(f"{output_dir}/SRS文档.md", "w", encoding="utf-8") as f:
        f.write(result.get("srs_draft", ""))
    with open(f"{output_dir}/基线信息.md", "w", encoding="utf-8") as f:
        f.write(f"基线版本: {result.get('baseline_version', '')}\n\n")
        f.write(result.get("rtm", ""))

    print(f"\n📁 产出物已保存到 {output_dir}/ 目录")
    print("  - 需求清单.md")
    print("  - SRS文档.md")
    print("  - 基线信息.md")


if __name__ == "__main__":
    main()
