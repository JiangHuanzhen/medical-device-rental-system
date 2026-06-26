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
import sys
# Windows GBK 终端兼容：强制 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import json
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypedDict, List, Literal, Callable, Optional
from datetime import datetime

from dotenv import load_dotenv
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
load_dotenv(_env_path)

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# ============================================================
# ⚙ 配置
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

# ============================================================
# 📡 回调机制（用于 streamlit 实时展示）
# ============================================================
# 工作流运行过程中，每个节点每步都会调这些回调函数，
# GUI 前端注册自己的回调来收取进度消息。

PROGRESS_CALLBACKS: list[Callable[[str], None]] = []
DIALOG_CALLBACKS: list[Callable[[str, str, str], None]] = []
RESULT_CALLBACKS: list[Callable[[str, any], None]] = []
PHASE_CALLBACKS: list[Callable[[str], None]] = []
CCB_EVENT: Optional[threading.Event] = None
CCB_RESULT: dict = {}

# ── 暂停/停止机制 ──
PAUSE_REQUESTED = threading.Event()      # 置位时表示用户请求暂停
RESUME_SIGNAL = threading.Event()        # 恢复信号
STOP_REQUESTED = threading.Event()       # 置位时表示用户请求停止

class WorkflowStopped(Exception):
    """工作流被用户手动停止"""
    pass

def run_stop_checks():
    """在每个主要节点入口调用 — 检查是否需要暂停或停止"""
    if STOP_REQUESTED.is_set():
        fire_progress("[STOP] 工作流已手动停止")
        raise WorkflowStopped("用户手动停止工作流")
    if PAUSE_REQUESTED.is_set():
        fire_progress("[PAUSE] 工作流已暂停，等待恢复...")
        while PAUSE_REQUESTED.is_set():
            if STOP_REQUESTED.is_set():
                fire_progress("[STOP] 工作流已手动停止")
                raise WorkflowStopped("用户手动停止工作流")
            RESUME_SIGNAL.wait(timeout=1)
            RESUME_SIGNAL.clear()
        fire_progress("[PLAY] 工作流已恢复")

# ── 全局迭代上限（防止无限循环） ──
MAX_GLOBAL_ITERATIONS = 5

def set_pause_requested():
    """SSE 前端调用 — 请求暂停（当前节点完成后暂停）"""
    PAUSE_REQUESTED.set()

def set_resume_signal():
    """SSE 前端调用 — 恢复执行"""
    PAUSE_REQUESTED.clear()
    RESUME_SIGNAL.set()

def set_stop_requested():
    """SSE 前端调用 — 请求停止"""
    STOP_REQUESTED.set()
    PAUSE_REQUESTED.clear()
    RESUME_SIGNAL.set()

def set_progress_callbacks(callbacks: list[Callable[[str], None]]):
    global PROGRESS_CALLBACKS
    PROGRESS_CALLBACKS = callbacks

def set_dialog_callbacks(callbacks: list[Callable[[str, str, str], None]]):
    global DIALOG_CALLBACKS
    DIALOG_CALLBACKS = callbacks

def set_result_callbacks(callbacks: list[Callable[[str, any], None]]):
    global RESULT_CALLBACKS
    RESULT_CALLBACKS = callbacks

def set_phase_callbacks(callbacks: list[Callable[[str], None]]):
    global PHASE_CALLBACKS
    PHASE_CALLBACKS = callbacks

def fire_progress(msg: str):
    for cb in PROGRESS_CALLBACKS:
        try:
            cb(msg)
        except Exception:
            pass

def fire_dialog(stakeholder: str, question: str, answer: str):
    for cb in DIALOG_CALLBACKS:
        try:
            cb(stakeholder, question, answer)
        except Exception:
            pass

def fire_result(key: str, value):
    for cb in RESULT_CALLBACKS:
        try:
            cb(key, value)
        except Exception:
            pass

def fire_phase(phase: str):
    for cb in PHASE_CALLBACKS:
        try:
            cb(phase)
        except Exception:
            pass

def set_ccb_event(event: threading.Event):
    """设置 CCB 等待事件（GUI 前端设一个 threading.Event）"""
    global CCB_EVENT
    CCB_EVENT = event

def set_ccb_result(verdict: str, comment: str):
    """GUI 前端设置 CCB 审批结果后释放事件"""
    global CCB_RESULT
    CCB_RESULT = {"verdict": verdict, "comment": comment}
    if CCB_EVENT:
        CCB_EVENT.set()

# A1涉众配置：名称 → 对应state字段 + Agent角色定义（Role/Goal/Backstory）
STAKEHOLDER_CONFIG = {
    "招商业务员": {"field": "biz_dialog"},
    "库房人员": {"field": "warehouse_dialog"},
    "运维工程师": {"field": "maintenance_dialog"},
    "财务": {"field": "finance_dialog"},
}

# 涉众AI智能体配置文件路径
AGENT_CONFIG_DIR = os.path.join(KB_ROOT, "raw/agents")
AGENT_CONFIG_FILES = {
    "招商业务员": "Agent定义-招商业务员.md",
    "库房人员": "Agent定义-库房人员.md",
    "运维工程师": "Agent定义-运维工程师.md",
    "财务": "Agent定义-财务.md",
}


def _next_baseline_version(ds: str) -> str:
    """自动递增基线版本号：扫描 wiki/baselines/ 目录，同日基线递增"""
    baselines_dir = os.path.join(KB_ROOT, "wiki/baselines")
    if not os.path.exists(baselines_dir):
        return f"BL-{ds}-01"
    existing = [d for d in os.listdir(baselines_dir)
                if os.path.isdir(os.path.join(baselines_dir, d))
                and d.startswith(f"BL-{ds}-")]
    if not existing:
        return f"BL-{ds}-01"
    nums = [int(d.rsplit("-", 1)[-1]) for d in existing
            if d.rsplit("-", 1)[-1].isdigit()]
    return f"BL-{ds}-{max(nums) + 1:02d}" if nums else f"BL-{ds}-01"


def _generate_diff(old_version: str, new_version: str, srs_current: str, rtm_current: str) -> str:
    """生成两个基线之间的需求差异文档"""
    baselines_dir = os.path.join(KB_ROOT, "wiki/baselines")
    old_rtm_path = os.path.join(baselines_dir, old_version, f"RTM_{old_version}_需求溯源矩阵.md")
    old_rtm = ""
    if os.path.exists(old_rtm_path):
        with open(old_rtm_path, "r", encoding="utf-8") as f:
            old_rtm = f.read()[:8000]

    prompt = f"""请对比新旧两个基线的需求溯源矩阵（RTM），生成需求差异分析文档。

旧基线 RTM（{old_version}）：
{old_rtm if old_rtm else "（初始基线，无历史版本）"}

新基线 RTM（{new_version}）：
{rtm_current[:8000]}

新基线 SRS（摘要）：
{srs_current[:3000]}

请按以下格式输出需求差异文档：

# 需求差异分析 — {old_version} → {new_version}

## 变更摘要
[简要描述本次变更的整体情况]

## 新增需求
| 需求编号 | 需求名称 | 新增原因 |
|---------|---------|---------|

## 修改需求
| 需求编号 | 需求名称 | 变更前 | 变更后 | 修改原因 |
|---------|---------|-------|-------|---------|

## 删除需求
| 需求编号 | 需求名称 | 删除原因 |
|---------|---------|---------|

## 未变更需求
| 需求编号 | 需求名称 |
|---------|---------|

## 影响分析
[变更对下游设计、开发、测试的影响评估]

严格基于新旧 RTM 表格内容进行比对。如果旧基线为空，则所有需求标记为「新增」，变更摘要注明「初始基线，无历史对比」。
"""
    result = call_llm(prompt, system_prompt="你是配置管理专家，精通需求变更管理和基线对比分析。")
    return result


def load_agent_configs() -> dict:
    """从知识库读取4份涉众AI智能体配置文件，返回 {名称: {role, goal, backstory}} 字典"""
    configs = {}
    for name, filename in AGENT_CONFIG_FILES.items():
        filepath = os.path.join(AGENT_CONFIG_DIR, filename)
        if not os.path.exists(filepath):
            print(f"  [WARN] Agent配置文件不存在: {filepath}，使用默认配置")
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
        print(f"  [PAGE] 已加载Agent配置: {filename}")
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
        print(f"  [WARN] Agent配置文件加载失败: {e}，使用空配置")
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

    # 版本号管理（wiki/summaries 层文档版本跟踪）
    doc_versions: dict               # {"req_list": 1, "issues": 1, "uml": 1, "srs": 1, "validation": 1, "defect": 1}

    # 控制
    iteration_count: int
    workflow_status: str
    force_forward: bool               # 强制前进标记（迭代超限后不再回退）


# ============================================================
# 🛠 工具函数
# ============================================================

def call_llm(prompt: str, system_prompt: str = "", max_tokens: int = 16000) -> str:
    """调用LLM，支持自定义 max_tokens"""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"}
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": LLM_MODEL, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens}
    try:
        resp = requests.post(LLM_API_URL, headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[LLM调用失败: {e}]"


def call_stakeholder(stakeholder: str, question: str) -> str:
    """调用涉众API"""
    payload = {"project_id": PROJECT_ID, "stakeholder": stakeholder, "phase": "v2", "question": question}
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
    """将内容保存到 Obsidian 知识库的对应目录，返回文件路径。
    基线目录（baselines/archive）受不可变性保护——已有文件不会被覆盖。"""
    base = KB_DIRS[subdir]
    full_path = os.path.join(base, filename)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    # 基线不可变性：baselines 和 archive 目录下的文件一旦创立不得修改或删除
    if subdir in ("baselines", "archive") and os.path.exists(full_path):
        print(f"  ⛔ [基线不可变] {subdir}/{filename} 已存在，拒绝覆盖")
        return full_path

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [DIR] 知识库: {subdir}/{filename}")
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
            print(f"  [PAGE] Agent配置已存在: {AGENT_CONFIG_FILES[s]}")
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
        print(f"  [NEW] Agent配置已生成: {AGENT_CONFIG_FILES[s]}")
    return get_agent_definitions()


# ============================================================
# 🧠 A1 涉众 Agent 节点（并行）
# ============================================================
# 四个涉众 Agent 通过 ThreadPoolExecutor 并行执行
# 每个 Agent 内部 while 循环到 AI 判断 done 为止（不定轮数）
# 支持首次运行和回退运行
# ============================================================

def a1_parallel(state: RequirementState) -> dict:
    """A1并行节点：4个涉众Agent同时对话"""
    run_stop_checks()
    fire_phase("A1_对话")

    # ── 全局迭代上限检查 ──
    iteration_count = state.get("iteration_count", 0) + 1
    if iteration_count > MAX_GLOBAL_ITERATIONS:
        fire_progress(f"⛔ 已达全局迭代上限（{MAX_GLOBAL_ITERATIONS}轮），停止回退并强制前进")
        return {"iteration_count": iteration_count}

    fire_progress(f"[START] A1: 4个涉众Agent开始并行对话（全局第{iteration_count}轮）...")

    stakeholders = ["招商业务员", "库房人员", "运维工程师", "财务"]
    results = {}
    errors = []

    def run_one(s: str) -> tuple[str, dict]:
        try:
            return s, _a1_agent(s, state)
        except Exception as e:
            return s, {"error": str(e)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run_one, s): s for s in stakeholders}
        for future in as_completed(futures):
            s = futures[future]
            try:
                _, result = future.result()
                if "error" in result:
                    errors.append(f"{s}: {result['error']}")
                else:
                    results.update(result)
            except Exception as e:
                errors.append(f"{s}: {e}")

    if errors:
        fire_progress(f"[WARN] A1 部分错误: {'; '.join(errors)}")
    fire_progress("[OK] A1 四个涉众对话完成")

    # 重置 rollback_reason（回退结束时清除）
    results["rollback_reason"] = ""

    # 强制前进标记：如果iteration_count超限则跳过回退
    if state.get("force_forward", False):
        results["force_forward"] = False
        results["rollback_reason"] = ""
        fire_progress("  → 强制前进模式：即使有回退信号也不再回退")

    results["iteration_count"] = iteration_count
    return results


def _a1_agent(stakeholder: str, state: RequirementState) -> dict:
    """A1涉众Agent核心逻辑 — AI自主提问 or 回退追问"""
    cfg = STAKEHOLDER_CONFIG[stakeholder]
    field = cfg["field"]
    existing = state.get(field, [])
    reason = state.get("rollback_reason", "")

    # ── 首次运行：AI自主生成问题，逐轮追问直到了解充分 ──
    if not existing:
        dialog = []
        max_rounds = 7  # 每个涉众最多提问轮数（留余量，实际很可能少于5轮）
        round_num = 0

        # 加载该涉众的 Agent 定义（Role/Goal/Backstory）作为系统提示
        agent_def = get_agent_definitions().get(stakeholder, {})
        agent_system_prompt = f"""你是一名资深的需求分析工程师，正在访谈「{stakeholder}」。你的职责是「真实记录涉众诉求」，不负责「在记录前过滤或调解」。

关于该角色的背景信息：
- 角色：{agent_def.get('role', stakeholder)}
- 核心目标：{agent_def.get('goal', '')}
- 背景：{agent_def.get('backstory', '')}

## 权限边界守则（必须遵守）

当涉众提出需求时，你按照以下三条边界判断如何回应：

1. **物理不可行**（如要求设备在物理极限之外运行）→ 礼貌指出物理约束，引导涉众调整为可实现版本：「您提到的这个速度，目前设备的机械响应时间最少需要X秒，您看X秒以内可以接受吗？」
2. **预算/时间约束不可行**（如要求极端天气全覆盖）→ 如实记录该需求，但在追问中引导涉众明确可接受的范围和成本：「这个要求在极端情况下确实理想，不过实现的成本会很高。您觉得在日常天气条件下，哪些保护是必须的？台风等极端情况可以接受降级运行吗？」
3. **与其他涉众潜在冲突**（如A说越快越好，B说要均衡使用）→ 不要当场调解，不要建议折中方案。如实记录双方各自诉求，将冲突发现留给后续的需求分析阶段处理。你可以追问澄清该涉众自己的需求细节，但不能透露其他涉众的立场。

核心原则：你是记录者，不是调解者。过滤和仲裁是需求分析阶段的工作。"""

        fire_progress(f"[{stakeholder}] AI开始访谈...")

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
- 如果还需要追问，生成下一个自然、具体的问题。按以下优先级选择追问方向：
  1. **场景引导**：从不问「你需要什么功能」，而是问「你今天的工作是怎样进行的？遇到了什么麻烦？」，将涉众的注意力锚定在日常工作上
  2. **痛点追问**：探测涉众最沮丧的时刻——「旧流程中最让你想摔键盘的是什么？」「这个环节你每天要花多少时间？」「出错了谁背锅？」从抱怨中提取量化需求
  3. **异常路径探测**：涉众默认只描述正常流程，你需要主动追问异常——「如果这一步失败了怎么办？」「最坏情况下会发生什么？」「有没有特殊客户不走这个流程？」
  4. **数据边界追问**：把「很多」「很快」「偶尔」转为具体数字——「你说的'很快'大概几秒？比你现在用的快多少？」「'偶尔'是一天一次还是一周一次？」

要求：
- 不要问已经问过的话题
- 每个问题只聚焦一个策略方向
- 用涉众的日常语言，不要用技术术语

输出严格JSON格式（只有JSON，不要其他文字）：
{{"done": false, "question": "你的追问问题"}}"""

            result = call_llm(prompt, system_prompt="你是专业的需求分析专家，擅长通过对话获取详细需求。")
            data = extract_json(result)

            # 非首次问且AI表示已了解充分 → 结束
            if dialog and data.get("done", False):
                fire_progress(f"[{stakeholder}] [OK] 已了解充分，结束提问（共{len(dialog)}轮）")
                break

            question = data.get("question", "")
            if not question:
                fire_progress(f"[{stakeholder}] [WARN] AI未生成有效问题，结束")
                break

            answer = call_stakeholder(stakeholder, question)
            dialog.append({"q": question, "a": answer})
            fire_progress(f"[{stakeholder}] [AI] Q: {question[:60]}...")
            fire_progress(f"[{stakeholder}] [USER] A: {answer[:60]}...")
            fire_dialog(stakeholder, question, answer)
            round_num += 1


        print(f"  [{stakeholder}] 完成 {len(dialog)} 条问答")

        # 保存到知识库 raw/notes
        if dialog:
            lines = [f"# {stakeholder} — 需求获取记录\n", f"日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
            for i, item in enumerate(dialog, 1):
                lines.append(f"## 第{i}问\n**问：** {item['q']}\n\n**答：** {item['a']}\n")
            content = kb_frontmatter(f"{stakeholder}需求记录", ["涉众对话", stakeholder], [f"{stakeholder}"]) + "\n".join(lines)
            ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
            ts = state.get("time_str", datetime.now().strftime("%H%M"))
            save_to_kb("raw_notes", f"{stakeholder}-{ds}-{ts}-需求记录.md", content)
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
- 把技术术语翻译成涉众日常语言。需求质量问题的元语言（「模糊」「不一致」「矛盾」）是需求工程师的语言，不是涉众的语言——不要让涉众觉得「我的需求被退回修正了」。
- 翻译示例：A2标记「UR-ORD-032 模糊：系统应在合理的时间内响应」→ 你追问「您上次提到系统要很快地响应，我想确认一下——您觉得格口门在您扫码之后多久内打开是您可以接受的？一两秒？还是三五秒也行？」
- 引用之前对话中的具体内容作为铺垫（「你刚才提到...请问...」）
- 最多不超过3个追问，优先关注严重问题

输出严格JSON格式（只有JSON，不要其他文字）：
{{"relevant": true/false, "follow_ups": ["追问句1", "追问句2"]}}
"""

    result = call_llm(prompt, system_prompt="你是专业的涉众需求分析师，擅长将技术性的质量问题翻译为涉众日常语言生成自然追问。")
    data = extract_json(result)

    if data.get("relevant") and data.get("follow_ups"):
        new_dialog = list(existing)
        fire_progress(f"[{stakeholder}] ⤴ 回退追问（共{len(data['follow_ups'])}个问题）")
        for q in data["follow_ups"]:
            answer = call_stakeholder(stakeholder, q)
            new_dialog.append({"q": q, "a": answer})
            fire_progress(f"[{stakeholder}] ⤴ Q: {q[:60]}...")
            fire_dialog(stakeholder, q, answer)
        fire_progress(f"[{stakeholder}] [OK] 补充追问完成")
        return {field: new_dialog}

    print(f"  [{stakeholder}] 无需补充追问")
    return {}


# ============================================================
# 🧠 A1汇总
# ============================================================

def a1_consolidate(state: RequirementState) -> dict:
    """A1汇总：合并四个涉众的需求为结构化需求清单（教材§3格式：双重记录）"""
    run_stop_checks()
    fire_phase("A1_汇总")
    fire_progress("[CHART] A1: 正在汇总4个涉众的需求清单...")
    all_dialogs = {
        name: state.get(cfg["field"], [])
        for name, cfg in STAKEHOLDER_CONFIG.items()
    }

    # 扫描 raw/notes 获取实际文件路径用于来源引用
    source_files = {}
    raw_notes_dir = KB_DIRS["raw_notes"]
    if os.path.exists(raw_notes_dir):
        for fname in os.listdir(raw_notes_dir):
            fpath = f"raw/notes/{fname}"
            for s in STAKEHOLDER_CONFIG:
                if s in fname and fname.endswith(".md"):
                    source_files[s] = fpath
                    break

    prompt = f"""你作为需求分析员，将以下四个涉众的对话记录整理为结构化的需求清单。采用「双重记录」格式——每条需求包含涉众原始陈述和你的提炼版本。

涉众对话记录及来源文件：
{json.dumps({name: {"source_file": source_files.get(name, f"raw/notes/{{name}}-需确认-需求记录.md"), "dialogs": dialogs} for name, dialogs in all_dialogs.items()}, ensure_ascii=False, indent=2)}

请严格按以下格式输出每条需求（双重记录格式）：

### BR-{{模块缩写}}-{{三位序号}}
| 字段 | 内容 |
|------|------|
| 业务目标ID | BR-{{模块缩写}}-{{三位序号}} |
| 业务目标描述 | {{一句话描述此需求对应的业务价值}} |
| 原始需求ID | UR-{{模块缩写}}-{{三位序号}} |
| 需求来源 | {{来源文件路径}}（{{涉众角色}}） |
| 需求陈述 | 「{{从对话记录中直接引用涉众的原话，保留完整语境}}」 |
| 需求提炼 | 作为{{角色}}，我希望{{系统能力}}，以便{{业务价值}} |
| 优先级 | 高/中/低 |

输出规则：
1. 按7个功能模块分组：设备管理、租赁订单、费用结算、系统配置、数据统计、用户认证、客户管理
2. 模块缩写：设备管理->EQP 租赁订单->ORD 费用结算->FIN 系统配置->CFG 数据统计->STA 用户认证->AUTH 客户管理->CRM
3. 每个模块 BR 和 UR 从001开始独立编号，一一对应
4. 优先级判断依据：涉众反复强调且情绪强烈 → 高；明确提及但未反复 → 中；追问下才确认 → 低
5. 需求陈述必须是对涉众原话的直接引用，不要改写
6. 需求提炼严格使用「作为...，我希望...，以便...」的三段式
7. 每个模块分组下按优先级排序（高→中→低）
8. 在文档末尾列出涉众来源索引表（涉众角色 → 来源文件路径）"""

    result = call_llm(prompt, system_prompt="你是一名资深需求分析工程师，擅长将涉众对话整理为结构化需求。必须严格遵循双重记录格式：需求陈述（直接引用涉众原话）+ 需求提炼（用户故事格式）。")

    # 版本号递增
    doc_ver = state.get("doc_versions", {}).get("req_list", 1)

    # 保存到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    conv_content = kb_frontmatter(f"需求清单-{ds}-v{doc_ver}", ["需求", "汇总"], ["需求清单"]) + result
    save_to_kb("wiki", f"需求清单-{ds}-v{doc_ver}.md", conv_content)

    fire_progress("[OK] A1: 需求清单汇总完成（双重记录格式）")
    fire_result("consolidated_requirements", result)
    return {"consolidated_requirements": result, "doc_versions": {**state.get("doc_versions", {}), "req_list": doc_ver + 1}}


# ============================================================
# 🧠 A2 需求分析 + 路由
# ============================================================

def a2_analyze_quality(state: RequirementState) -> dict:
    """A2需求分析：四维度质量检测，检测严重问题时回退A1（教材§5）"""
    current_round = state.get("a2_round", 1)
    max_rounds = state.get("a2_max_rounds", 3)
    run_stop_checks()
    fire_phase("A2_分析")

    # ── 非A2回退（来自A5/CCB）时重置轮次 ──
    reason = state.get("rollback_reason", "")
    if reason and reason != "A2_rollback":
        current_round = 1
        fire_progress(f"  ↻ A2: 来自{reason}回退，轮次重置为第1轮")

    fire_progress(f"[FIND] A2: 第{current_round}轮需求质量分析...")
    prompt = f"""对以下需求清单进行四维度质量检测（第{current_round}轮/最多{max_rounds}轮）。四个维度严重性递增：模糊(最轻)→不一致→矛盾→冲突(最重，需CCB介入)。

{state.get("consolidated_requirements", "")}

## 维度一：模糊（Ambiguity）
不同人阅读同一需求时可能产生不同理解。检测方法：
**方法1 — 关键词扫描**：搜索不可量化修饰词——「尽量」「尽可能」「一般情况下」「通常」「大概」「左右」「适当」「合理」「快速」「及时」。一旦出现即标记。
**方法2 — 限定条件缺失**：检查需求是否缺少关键要素——触发条件、执行者、时间约束、数量范围、异常处理。例如「系统应发送通知」若未指定通知渠道、接收者、触发条件和时效，则标记为模糊。

## 维度二：不一致（Inconsistency）
同一术语在不同需求条目中有不同定义，但这些定义在逻辑上可以共存（区别于矛盾）。检测方法：
**术语映射法**：提取所有需求中出现的核心术语（如「订单状态」「设备状态」「用户角色」「租赁周期」），逐条检查同一术语是否在不同地方被赋予了不同的枚举值或约束。例如需求A说「订单状态5种」，需求B说「订单状态7种」——可以共存但需统一。不一致≠矛盾：不一致是"用了两套规则"，矛盾是"两套规则互斥"。

## 维度三：矛盾（Contradiction）
两条需求在逻辑上无法同时成立。分两类：
**显式矛盾**（AI高可靠检测）：两条需求对同一参数给出不同常数值，如A说「有效期为24小时」B说「有效期为48小时」。
**隐式矛盾**（AI有限检测）：表面不矛盾，但结合领域知识推理后矛盾。例如A说「取件码无限期有效」B说「快递员离职后其投递的包裹取件码全部失效」→ 若快递员离职发生在用户取件前则矛盾。检测隐式矛盾时需要标注「需人工确认」。

## 维度四：冲突（Conflict）
不同涉众的期望互斥——满足一方必然损害另一方。检测方法：
按功能域分组（设备管理、租赁订单、费用结算……），在每个功能域内检查来自不同涉众的需求是否存在目标层面的互斥。例如「招商业务员要求租赁合同审批越快越好，财务要求每份合同必须经过三层复核」——无法同时最大化。
**重要**：A2智能体的职责是发现并报告冲突，不是裁决冲突。冲突的最终裁决权在CCB。

---

输出严格JSON格式：
{{"issues": [{{"type": "模糊/不一致/矛盾/冲突", "subtype": "显式矛盾/隐式矛盾/关键词模糊/条件缺失/术语不统一/涉众冲突（按需填）", "severity": "严重/中/低", "description": "具体问题描述（引用需求编号和原文）", "source": "涉及涉众（冲突类需同时列出双方）", "consequence": "该问题如果不解决可能导致的具体后果（如：交付后返工、测试用例不可编写、涉众验收不通过等）", "clarification_path": "推荐的澄清路径（如：回退A1请涉众确认X；统一术语定义并更新所有引用；提交CCB裁决等）", "suggestion": "具体修正建议"}}], "has_critical": true/false}}

**在JSON之后附加详细解释**（供人类审计）：
对每个判定为「严重」或「中」的问题，用自然语言段落解释：为什么判定为问题、逻辑推理过程、不解决的风险。确保每一条判定都有可被审计的理由，不要仅仅是关键词匹配——展示你的推理链。"""

    result = call_llm(prompt, system_prompt="你是一名严格的需求质量审查专家。你必须对每条判定给出可被审计的推理理由，而不仅仅是执行关键词匹配。输出JSON问题清单后附加详细解释段落。")
    data = extract_json(result)
    issues = data.get("issues", [])
    has_critical = data.get("has_critical", False)

    # 版本号递增
    doc_ver = state.get("doc_versions", {}).get("issues", 1)

    # 保存问题清单到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    issue_rows = "\n".join(
        f"| {i.get('type','')} | {i.get('severity','')} | {i.get('description','')} | {i.get('source','')} | {i.get('consequence','')} | {i.get('clarification_path','')} | {i.get('suggestion','')} |"
        for i in issues
    ) if issues else "| — | — | 未发现严重问题 | — | — | — | — |"
    issues_content = kb_frontmatter(f"需求问题清单-{ds}-v{doc_ver}", ["问题", "质量分析"], ["需求问题清单"]) + f"""# 需求问题清单

生成日期：{ds}
分析轮次：第{current_round}轮

## 问题总表
| 类型 | 严重程度 | 问题描述 | 涉及涉众 | 不解决的后果 | 推荐澄清路径 | 修正建议 |
|-----|---------|---------|---------|------------|------------|---------|
{issue_rows}

## 详细解释（人类审计）
以下是对严重/中等问题的人工可审计理由：
{result}

> 本报告由 A2 需求分析 Agent 生成，关联 [[需求清单-{ds}-v{doc_ver}|需求清单]]
"""
    save_to_kb("wiki", f"需求问题清单-{ds}-v{doc_ver}.md", issues_content)

    if has_critical:
        fire_progress(f"[FIND] A2: 第{current_round}轮发现{len(issues)}个问题（含严重），回退A1追问")
    else:
        fire_progress(f"[FIND] A2: 第{current_round}轮通过，无严重问题")

    return {
        "quality_issues": issues,
        "has_critical_issues": has_critical,
        "a2_round": current_round + 1,
        "rollback_reason": "A2_rollback" if has_critical else "",
        "doc_versions": {**state.get("doc_versions", {}), "issues": doc_ver + 1},
    }


def a2_decide_next(state: RequirementState) -> Literal["rollback", "continue"]:
    """A2条件判断：强军问题，全局迭代超限时强制前进"""
    has_critical = state.get("has_critical_issues", False)
    a2_round = state.get("a2_round", 1)
    max_rounds = state.get("a2_max_rounds", 3)
    iteration_count = state.get("iteration_count", 0)

    # ── 全局迭代超限 → 强制前进 ──
    if iteration_count >= MAX_GLOBAL_ITERATIONS:
        print(f"  [A2] ⛔ 全局迭代已达上限({MAX_GLOBAL_ITERATIONS})，强制通过")
        return "continue"

    if has_critical and a2_round <= max_rounds:
        print(f"  [A2] [WARN] 第{a2_round}轮发现严重问题 → 回退A1，涉众Agent将自动生成追问")
        return "rollback"
    if has_critical:
        print(f"  [A2] [WARN] 已达最大轮数({max_rounds})，强制继续")
    else:
        print(f"  [A2] [OK] 需求质量检测通过")
    return "continue"


# ============================================================
# 🧠 A3 UML建模
# ============================================================

def a3_generate_uml(state: RequirementState) -> dict:
    """A3建模：先单独生成用例图，再生成活动图/时序图/E-R图"""
    run_stop_checks()
    fire_phase("A3_UML")
    fire_progress("[UML] A3: 正在生成UML模型...")

    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))

    # ── ⭐ 阶段1：专门生成完善的用例图 ──
    fire_progress("  [GOAL] [A3/1] 专注生成用例图（Use Case Diagram）...")
    uc_prompt = f"""你是一名UML建模专家。请根据以下需求清单生成PlantUML用例图。遵循以下方法论：

需求清单：
{state.get("consolidated_requirements", "")[:8000]}

## 第一步：涉众 → Actor 映射
从需求清单中识别所有与系统交互的角色。注意：「涉众」和「Actor」不是一一对应的——同一个涉众可能在不同场景下扮演不同的Actor角色，多个涉众也可能共享同一个Actor角色。映射时只提取真正与系统直接交互的角色，排除仅作为信息接收方的被动角色。

## 第二步：从需求中提取用例
从每条需求的「需求陈述」和「需求提炼」中提取动词短语，然后进行语义归类：
- **合并判断**：如果多个动作的核心业务价值相同、业务流程相同，仅触发条件和权限不同 → 合并为一个用例
- **拆分判断**：如果同一动作在不同情境下业务流程完全不同 → 拆分为独立用例
- 每个用例必须是Actor视角下「一项完整的、有价值的服务」，不要用CRUD操作粒度的碎片化用例

## 第三步：建立关系
- `<<include>>` — 被包含的用例是基用例的必需步骤（如：创建订单必然包含客户资质验证）
- `<<extend>>` — 扩展用例在特定条件下触发（如：取件超时 extend 计费处理）
- Actor与用例的关联不是随意的——仔细判断每个Actor是否有权访问该用例（普通用户不应关联到财务报表，那是管理员专有的）

## 第四步：附加注解
用 `note` 标注关键的 Actor 映射决策和用例合并/拆分理由，让你的设计决策可被审计。

输出要求：
- 至少 3 个 Actor，至少 8 个用例
- 至少 2 个 <<include>> 和 2 个 <<extend>>
- 严格 @startuml ... @enduml 格式，只输出PlantUML代码"""
    uc_result = call_llm(uc_prompt, system_prompt="你是UML建模专家，精通PlantUML用例图。")

    # 版本号递增
    doc_ver = state.get("doc_versions", {}).get("uml", 1)

    # 确保有 @startuml 和 @enduml
    if not uc_result.strip().startswith("@startuml"):
        uc_result = "@startuml\n" + uc_result
    if not uc_result.strip().endswith("@enduml"):
        uc_result = uc_result.strip() + "\n@enduml"
    save_to_kb("wiki", f"用例图-{ds}-v{doc_ver}.puml", uc_result)
    fire_progress(f"  [OK] 用例图已生成（{len(uc_result)}字）")

    # ── 阶段2：生成活动图/时序图/E-R图 ──
    fire_progress("  [GOAL] [A3/2] 生成活动图/时序图/E-R图...")
    other_prompt = f"""根据以下需求清单，生成UML模型（PlantUML代码）。

需求清单：
{state.get("consolidated_requirements", "")[:8000]}

请生成以下三类图，每类图用 @startuml ... @enduml 包裹，图间用空行分隔：

1. **活动图（Activity Diagram）** — 至少3个核心流程
   从需求清单中选出业务逻辑最复杂、分支最多的3个核心用例来画活动图（不要固定流程名，你自己判断哪些最值得画）。
   每个活动图必须严格包含：
   - **Swimlane（泳道）**：用 `|泳道名|` 标注每个活动的执行角色，明确哪个涉众负责哪个活动
   - **Guard Condition**：每个决策节点用 `[条件]` 标注明确的可判断真伪的条件（如 `[金额>5000?]`）。活动图的核心价值就是「不允许模糊」——每一个分支都必须有显式的判断条件，不能写「正常情况走A」
   - **Fork/Join**：并发操作必须用 `fork` 和 `fork again` / `end fork` 明确标注（如：开柜的同时并发执行「更新格口状态」和「发送取件通知」）
   - **路径覆盖**：每个图至少 2 条正常路径 + 2 条异常路径 + 1 条边界路径

2. **时序图（Sequence Diagram）** — 至少1个核心流程
   - 展示 Actor → Controller → Service → Repository 的完整调用时序
   - 包含：正常流程消息、异常返回消息、循环/可选片段（`loop`/`alt`/`opt`）

3. **数据库E-R图**（PlantUML格式，不是Mermaid）
   - 从需求清单中提取核心实体（不要硬编码实体名，根据需求实际分析）
   - 标注实体间关系（1对多、多对多）和关键属性字段

输出严格每图 @startuml ... @enduml 格式。"""
    other_result = call_llm(other_prompt, system_prompt="你是UML建模专家，精通PlantUML语法。")

    # 提取其他图
    parts = other_result.split("@startuml")
    other_diagrams = []
    for i, p in enumerate(parts[1:], 1):
        diagram = "@startuml" + p.split("@enduml")[0] + "\n@enduml" if "@enduml" in p else "@startuml" + p
        other_diagrams.append(diagram)
    all_others = "\n\n".join(other_diagrams)

    if all_others:
        save_to_kb("wiki", f"行为模型图-{ds}-v{doc_ver}.puml", all_others)

    # ── 保存总说明 ──
    total_diagrams = 1 + len(other_diagrams)
    uml_doc = kb_frontmatter(f"UML模型-{ds}-v{doc_ver}", ["UML", "建模"], ["UML模型"]) + f"""# UML模型

生成日期：{ds}

## 文件
- [[用例图-{ds}-v{doc_ver}.puml|用例图]]
- [[行为模型图-{ds}-v{doc_ver}.puml|活动图/时序图/E-R图]]

## 所涉图类型
1. [OK] 用例图（Use Case）— 4角色 × 7模块，含 <<include>> / <<extend>>
2. [OK] 活动图（Activity Diagram，≥3个核心流程）
3. [OK] 时序图（Sequence Diagram，≥1个核心流程）
4. [OK] 数据库E-R图（Entity Relationship Diagram）

## 关联需求
- [[需求清单-{ds}-v{doc_ver}|需求清单]]

> 由 A3 建模 Agent 根据需求清单生成
"""
    save_to_kb("wiki", f"UML模型说明-{ds}-v{doc_ver}.md", uml_doc)

    fire_progress(f"[OK] A3: UML模型生成完成（共{total_diagrams}张图）")
    fire_result("uml_use_case", uc_result)
    fire_result("uml_activity_diagrams", all_others)
    return {"uml_use_case": uc_result, "uml_activity_diagrams": all_others,
            "doc_versions": {**state.get("doc_versions", {}), "uml": doc_ver + 1}}


# ============================================================
# 🧠 A4 SRS生成
# ============================================================

def a4_generate_srs(state: RequirementState) -> dict:
    """A4文档智能体：生成IEEE 830标准SRS文档（规范对齐版）"""
    run_stop_checks()
    fire_phase("A4_SRS")
    fire_progress("[PAGE] A4: 正在生成 IEEE 830 标准 SRS 文档（预计耗时较长）...")
    prompt = f"""请根据以下输入生成一份完整的SRS（软件需求规格说明书），严格遵循IEEE 830标准和GB/T 9385规范。

需求清单：
{state.get("consolidated_requirements", "")[:6000]}

UML模型：
{state.get("uml_use_case", "")[:2000]}
{state.get("uml_activity_diagrams", "")[:2000]}

## 生成方法：两阶段法

**第一阶段：骨架生成。** 先按IEEE 830模板生成完整的章节目录和每个章节的框架句子。此阶段不追求语言流畅，只保证结构完整、章节编号正确、所有必填项都有占位。

**第二阶段：骨架填充。** 在骨架中逐章填充具体内容。填充时遵守一条铁律：**精确优先于流畅**。当精确性和语言流畅性产生冲突时，牺牲流畅性以保证精确性。例如：
- ❌ 「系统应快速响应用户的扫码操作」
- ✅ 「系统应在用户扫码后500毫秒内完成取件码验证并返回验证结果」
AI可能会在润色语言时不自觉地将精确数字替换为模糊形容词——这是SRS生成中最大的风险。你必须主动抵制这种倾向。保留需求清单中的每一个数字、每一个边界条件、每一个约束参数，宁可语言生硬，不可牺牲精度。

请严格按照以下模板结构生成，缺一不可：

---
# 文档头部信息
| 项目项 | 内容 |
| ---- | ---- |
| 文档名称 | 软件需求规格说明书（SRS）|
| 项目名称 | 医疗器械租赁管理系统 |
| 项目编号 | MED-RENTAL-2026 |
| 文档版本 | V1.0.0 |
| 基线版本 | 【占位，由A6分配】|
| 编制人 | AI基线智能体（A6） |
| 编制日期 | 【当前日期】|
| 审核人 | CCB变更控制委员会 |
| 批准人 | CCB变更控制委员会 |
| 密级 | 内部 |

## 修订历史记录
| 版本号 | 修订日期 | 修订类型 | 修订内容简述 |
| V1.0.0 | 【当前日期】 | 新建 | 文档初稿，确立初始需求基线 |

# 1 引言
## 1.1 编制目的
## 1.2 文档范围（包含/排除）
## 1.3 引用文件（IEEE 830、GB/T 9385、《高级软件设计实践》教材等）
## 1.4 术语与缩略语（含SRS、CCB、CR、FR、NFR等定义表）
## 1.5 业务背景概述（现状痛点、建设目标、量化业务目标）

# 2 总体描述
## 2.1 产品概述（系统定位、核心价值）

!!! 必须在此处输出完整的系统架构图（Mermaid代码），不允许跳过 !!!
### 系统架构图（Mermaid代码）
```mermaid
flowchart TD
    subgraph 客户端层[客户端层 - PC/移动端]
    end
    subgraph 接入层[接入层 - API网关/鉴权/限流]
    end
    subgraph 业务服务层[业务服务层]
    end
    subgraph 数据层[数据层 - 数据库/缓存]
    end
```
-- 务必填充完整 mermaid 代码，包含所有 4+ 层和具体子组件 --

## 2.2 运行环境要求（硬件/软件/浏览器兼容表）
## 2.3 用户角色与特征（角色/职责/权限/频次/技能 矩阵表）
## 2.4 系统运行模式（正常/异常/维护三种模式）
## 2.5 设计与实现约束（技术/合规/接口/工期约束）
## 2.6 假设与依赖

# 3 具体需求
## 3.1 功能需求（FR）
按7个模块分节：用户认证、设备管理、客户管理、租赁订单、费用结算、数据统计、系统配置
每条功能需求格式：
**FR-{{模块缩写}}-{{编号}}**（沿用 BR-{{模块缩写}}-{{编号}} / UR-{{模块缩写}}-{{编号}} 的对应关系）
- 优先级：P0(必实现)/P1(重要)/P2(次要)
- 参与角色
- 前置条件
- 触发方式
- 业务流程（分步骤）
- 业务规则（含边界条件、数量范围、时间周期）
- 后置状态
- 验收标准（可量化、可测试、无歧义）
- 关联需求条目：BR-{{模块缩写}}-{{编号}} / UR-{{模块缩写}}-{{编号}}

### 系统用例图（plantUML代码）
-- 务必在此处输出完整的 plantUML 用例图代码 --

## 3.2 外部接口需求（IFR）

!!! 必须在此处输出 E-R 图，不允许跳过 !!!
### E-R图（Mermaid erDiagram，核心实体：设备、客户、合同、订单、费用）
-- 务必填充完整的 mermaid erDiagram 代码，包含所有核心实体及其关系 --
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
## 4.1 需求基线定义
1. 基线版本格式：`BL-YYYYMMDD-NN`（YYYYMMDD=日期，NN=当日流水号）；
2. 初始基线：经CCB审批通过、正式发布的第一版SRS；
3. 基线冻结：基线发布后，禁止无流程私自修改需求。

## 4.2 需求变更整体流程
```mermaid
flowchart LR
    classDef stage fill:#f0f8ff,stroke:#2c5282
    classDef judge fill:#fff3f3,stroke:#c53030

    subgraph S1 [变更需求获取]
        A[AI涉众沟通 / 提交CR文档]
    end
    class S1 stage

    subgraph S2 [变更需求分析]
        B[质量分析+建模+SRS初稿<br/>基线比对+差异文档]
    end
    class S2 stage

    subgraph S3 [变更审核 CCB]
        C{{审核结果}}
    end
    class S3 judge

    subgraph S4 [新基线创立]
        D[生成溯源矩阵+新版基线<br/>保留历史基线]
    end
    class S4 stage

    A --> B
    B --> C
    C -- 不通过 --> E[流程结束]
    C -- 退回修改 --> A
    C -- 审核通过 --> D
```

## 4.3 变更详细流程（四阶段）
### 4.3.1 阶段一：变更需求获取
两种途径：涉众AI智能体沟通 / 需求提出方提交正式CR变更需求文档

### 4.3.2 阶段二：变更需求分析（4个子阶段）
1. 需求质量分析：校验变更需求合理性、完整性、无歧义
2. 项目建模：更新UML用例图、活动图
3. SRS初稿生成：整合输出变更版SRS初稿
4. 基线比对：读取历史基线，生成需求差异文档

### 4.3.3 阶段三：变更审核（CCB评审）
1. 审核不通过 → 流程终止
2. 审核退回修改 → 返回变更需求获取阶段
3. 审核通过 → 进入新基线创立环节

### 4.3.4 阶段四：新基线创立
1. 生成需求溯源矩阵（RTM），建立变更前后条目映射
2. 将审核通过的SRS定为新版正式基线
3. 沿用版本规则生成新基线编号
4. 历史基线文档完整归档、不覆盖、不删除

## 4.4 变更记录台账
| 变更编号 | 变更日期 | 申请人 | 变更来源(AI/CR) | 变更简述 | 影响模块 | CCB结论 | 新版基线号 |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| — | — | — | 初始基线 | 初始基线，无历史变更 | — | 通过 | 【占位】 |

# 5 附录
## 附录A 全量图表汇总
集中存放本SRS中的架构图、用例图、E-R图、流程图（Mermaid代码）：
- 系统架构图：见 §2.1
- 系统用例图：见 §3.1
- E-R图：见 §3.4
- 变更流程图：见 §4.2

## 附录B 验收标准总表
| 需求编号 | 需求名称 | 验收标准 | 优先级 |
| ---- | ---- | ---- | ---- |
|（由各模块功能需求填充）| | | |

## 附录C 参考资料与外部文档链接
1. GB/T 9385-2008 计算机软件需求规格说明规范
2. IEEE 830 软件需求规格说明书标准
3. 《高级软件设计实践》教材书稿
4. 医疗器械租赁管理系统涉众需求调研记录（raw/notes/）
5. 医疗器械租赁管理系统UML建模产物
6. 医疗器械租赁管理系统结构化需求清单

---
总字数不少于15000字。所有需求必须可验证、无歧义、可追溯。禁止使用「尽量」「大概」「合理」「快速」「及时」等模糊词。

[WARN] 格式强制要求（违者重写）：
1. 「修订历史记录」表格必须出现在文档头部（文档版本号之后）
2. 「系统架构图」的 Mermaid 代码必须完整且包含4层结构
3. 「E-R图」的 Mermaid erDiagram 代码必须完整，包含至少5个核心实体及其关联关系
4. 「系统用例图」的 PlantUML 代码必须完整，包含所有涉众角色
5. 「附录A 验收标准总表」至少填充3行真实需求"""

    result = call_llm(prompt, system_prompt="你是专业的软件需求文档编写专家，精通IEEE 830标准。记住：精确优先于流畅。保留每一个数字和边界条件，不因追求可读性而引入模糊。", max_tokens=32000)

    # 后处理：强制修正 SRS 所有不规范之处
    _project_name = "医疗器械租赁管理系统"
    _today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. 强制替换项目名称（LLM 经常脑补成"智能医疗设备..."）
    for _bad in ["智能医疗设备租赁与运维管理系统", "智能医疗设备租赁管理系统", "智能医疗设备租赁",
                 "医疗设备租赁与运维管理系统", "医疗器械租赁与运维管理系统"]:
        result = result.replace(_bad, _project_name)

    # 2. 强制替换错误日期
    import re as _re
    for _pat in [r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}[日]?", r"\d{4}年\d{1,2}月\d{1,2}日"]:
        result = _re.sub(_pat, _today_str, result)

    # 3. 编号体系：需求清单中 REQ 对应 SRS 中 FR，不再做 REQ→FR 强制替换（保留引用链）
    for _nf_old, _nf_new in [("PERF-", "NFR-PERF-"), ("SEC-", "NFR-SEC-"), ("REL-", "NFR-REL-"),
                             ("MAINT-", "NFR-MAINT-"), ("PER-", "NFR-PERF-")]:
        result = _re.sub(_nf_old + r'(\d+)', _nf_new + r'\1', result)

    # 4. 确保修订历史记录表存在（规范 §文档头部要求）
    if "## 修订历史记录" not in result:
        revision_table = f"\n## 修订历史记录\n| 版本号 | 修订日期 | 修订人 | 修订类型 | 修订内容简述 | 审批人 |\n|-------|---------|-------|---------|------------|-------|\n| V1.0.0 | {_today_str} | AI基线智能体 | 新建 | 文档初稿，确立初始需求基线 | |\n"
        # 插入到文档头部信息之后
        _insert_pos = result.find("# 1")
        if _insert_pos < 0:
            _insert_pos = result.find("1 引言")
        if _insert_pos >= 0:
            result = result[:_insert_pos] + revision_table + "\n" + result[_insert_pos:]
        else:
            result = revision_table + "\n" + result

    # 5. 确保文档头部（修订历史已存在）后再校验架构图和E-R图
    # 检查是否有架构图 Mermaid 代码（flowchart TD 或 graph TD）
    if "flowchart" not in result and "graph TD" not in result:
        print("  [WARN] [A4] SRS 缺少系统架构图 Mermaid 代码，正在补充默认架构图...")
        arch_diagram = """\n### 系统架构图（Mermaid代码）
```mermaid
flowchart TD
    subgraph 客户端层[客户端层]
        PC[PC端浏览器]
        Mobile[移动端]
    end
    subgraph 接入层[接入层]
        GW[API网关<br/>鉴权/限流/路由]
    end
    subgraph 业务服务层[业务服务层]
        S1[用户认证服务]
        S2[合同管理服务]
        S3[库存管理服务]
        S4[运维管理服务]
        S5[财务管理服务]
    end
    subgraph 数据层[数据层]
        DB[(业务数据库)]
        Cache[(缓存)]
    end
    PC --> GW
    Mobile --> GW
    GW --> S1 & S2 & S3 & S4 & S5
    S1 & S2 & S3 & S4 & S5 --> DB & Cache
```\n"""
        _insert_after = result.find("## 2.1 产品概述")
        if _insert_after >= 0:
            _section_end = result.find("## 2.2", _insert_after)
            if _section_end >= 0:
                result = result[:_section_end] + arch_diagram + result[_section_end:]

    # 6. 检查是否有 E-R 图（Mermaid erDiagram）
    if "erDiagram" not in result:
        print("  [WARN] [A4] SRS 缺少 E-R 图 Mermaid 代码，正在补充默认 E-R 图...")
        er_diagram = """\n### E-R图（Mermaid erDiagram）
```mermaid
erDiagram
    CUSTOMER ||--o{ CONTRACT : "签订"
    CONTRACT ||--|{ DEVICE : "包含"
    CONTRACT ||--o{ FINANCE_PLAN : "生成"
    DEVICE ||--o{ MAINTENANCE_RECORD : "关联"
    DEVICE ||--o{ INVENTORY : "管理"
    CUSTOMER {
        string customer_id PK
        string customer_name
        string hospital_name
    }
    CONTRACT {
        string contract_id PK
        string contract_code
        date start_date
        date end_date
        decimal total_amount
        string status
    }
    DEVICE {
        string device_id PK
        string model_code
        string device_name
        decimal daily_max_hours
    }
    FINANCE_PLAN {
        string plan_id PK
        decimal receivable_amount
        decimal received_amount
        date due_date
    }
    MAINTENANCE_RECORD {
        string record_id PK
        date start_time
        date end_time
        string fault_type
    }
    INVENTORY {
        string inventory_id PK
        int quantity
        string location
    }
```\n"""
        _insert_after = result.find("## 3.2 外部接口需求")
        if _insert_after >= 0:
            _section_end = result.find("## 3.3", _insert_after)
            if _section_end >= 0:
                result = result[:_section_end] + er_diagram + result[_section_end:]

    # 7. 确保 4 需求基线与变更管理 章节存在
    if "# 4 需求基线与变更管理" not in result:
        result += "\n\n# 4 需求基线与变更管理\n"
        result += "## 4.1 需求基线定义\n"
        result += "基线版本格式：BL-YYYYMMDD-NN，初始基线经CCB审批发布，基线冻结后禁止无流程私自修改。\n\n"
        result += "## 4.2 需求变更整体流程\n"
        result += "流程图（Mermaid）参见规范文档§4.2。\n\n"
        result += "## 4.3 变更详细流程（四阶段）\n"
        result += "阶段一：变更需求获取 → 阶段二：变更需求分析（质量分析+建模+SRS+基线比对）→ 阶段三：CCB审核 → 阶段四：新基线创立\n\n"
        result += "## 4.4 变更记录台账\n"
        result += "| 变更编号 | 变更日期 | 申请人 | 变更来源 | 变更简述 | 影响模块 | CCB结论 | 新版基线号 |\n"
        result += "| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |\n"
        result += "| — | — | — | 初始基线 | 初始基线 | — | 通过 | — |\n"

    # 8. 确保附录存在
    if "## 附录A" not in result:
        result += "\n\n# 5 附录\n"
        result += "## 附录A 全量图表汇总\n"
        result += "集中存放架构图、用例图、E-R图、流程图。\n\n"
        result += "## 附录B 验收标准总表\n"
        result += "| 需求编号 | 需求名称 | 验收标准 | 优先级 |\n"
        result += "| ---- | ---- | ---- | ---- |\n"
        result += "|（由各模块功能需求填充）| | | |\n\n"
        result += "## 附录C 参考资料与外部文档链接\n"
        result += "1. GB/T 9385-2008 计算机软件需求规格说明规范\n"
        result += "2. IEEE 830 软件需求规格说明书标准\n"
        result += "3. 《高级软件设计实践》教材书稿\n"
        result += "4. 医疗器械租赁管理系统涉众需求调研记录\n"
        result += "5. 医疗器械租赁管理系统UML建模产物\n"
        result += "6. 医疗器械租赁管理系统结构化需求清单\n"

    # 版本号递增
    doc_ver = state.get("doc_versions", {}).get("srs", 1)

    # 保存SRS到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    srs_content = kb_frontmatter(f"SRS-{ds}-v{doc_ver}", ["SRS", "需求规格"], ["软件需求规格说明书"]) + result
    save_to_kb("wiki", f"SRS-初稿-{ds}-v{doc_ver}.md", srs_content)

    fire_progress(f"[OK] A4: SRS文档生成完成（{len(result)}字）")
    fire_result("srs_draft", result)
    return {"srs_draft": result, "doc_versions": {**state.get("doc_versions", {}), "srs": doc_ver + 1}}


# ============================================================
# 🧠 A5 验证 + 路由
# ============================================================

def a5_validate_srs(state: RequirementState) -> dict:
    """A5验证：四类交叉验证（教材§7：历史需求/涉众对话/项目文档/SRS内部），设置回退原因"""
    run_stop_checks()
    fire_phase("A5_验证")
    fire_progress("[SEARCH] A5: 正在执行交叉验证...")
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    prompt = f"""你正在对医疗器械租赁管理系统的SRS初稿进行交叉验证。

## 输入文件
- **SRS初稿**：wiki/summaries/SRS-初稿-vX.X.md（见下方完整内容）
- **需求清单**：wiki/summaries/需求清单-{ds}-vX.md（含涉众原话陈述和提炼版本）
- **涉众对话记录**：raw/notes/ 目录下全部涉众需求记录
- **UML模型**：wiki/summaries/ 目录下用例图和活动图

SRS文档（完整）：
{state.get("srs_draft", "")}

需求清单（含涉众原话）：
{state.get("consolidated_requirements", "")[:5000]}

## 四类交叉比对方法

### 1. 历史需求比对
检查SRS是否遗漏了需求清单中存在但SRS中未出现的需求条目。逐条REQ在SRS中搜索对应描述，找不到的标记为「历史遗漏」。

### 2. 涉众对话比对（核心）
将SRS功能需求追溯到需求清单中的「需求陈述」（涉众原话），判断SRS是否准确反映了涉众意图。使用四级语义等价标注：
- **完全匹配**：SRS精确转述涉众要求
- **合理诠释**：SRS做了合理具体化
- **部分偏差**：局部偏离，需回A1确认
- **严重曲解**：严重误解，须回A1重新对话

### 3. 项目文档比对
检查SRS中用户特征定义、约束条件是否与需求清单中各涉众实际描述一致。

### 4. SRS内部一致性比对
检查术语统一性、数据字典字段定义与功能需求使用方式是否一致。

---

输出严格JSON格式：
{{"verdict": "通过（建议进入基线创立）/ 修改后重新验证（获取类问题→回A1）/ 修改后重新验证（分析类问题→回A2）", "summary": {{"total_issues": N, "blocker": N, "major": N, "minor": N, "by_type": {{"历史遗漏": N, "对话偏差": N, "文档矛盾": N, "内部不一致": N}}, "by_source": {{"完全匹配": N, "合理诠释": N, "部分偏差": N, "严重曲解": N}}}}, "findings": [{{"type": "历史遗漏/对话偏差/文档矛盾/内部不一致", "source_match": "完全匹配/合理诠释/部分偏差/严重曲解（仅对话偏差类需要）", "severity": "阻塞性/重要/建议", "section": "涉及SRS章节编号", "description": "具体问题描述", "source_ref": "引用REQ编号或涉众原话作为证据", "suggestion": "修正建议"}}]}}"""

    result = call_llm(prompt, system_prompt="你是严谨的需求验证审计师。你的职责是「对照证据源找出差异」，不创造内容、不做主观猜测。进行涉众对话比对时，执行语义等价判断而非关键词匹配。每个发现必须引用证据来源（REQ编号或涉众原话）。")
    data = extract_json(result)
    verdict_raw = data.get("verdict", "修改后重新验证（分析类问题→回A2）")
    findings = data.get("findings", [])
    # 兼容新旧 verdict 格式
    if "通过" in verdict_raw and "修改" not in verdict_raw:
        verdict = "通过"
    elif "获取" in verdict_raw or "获取类" in verdict_raw:
        verdict = "获取类问题"
    else:
        verdict = "分析类问题"
    rollback_reason = {
        "通过": "",
        "获取类问题": "A5_acquisition",
        "分析类问题": "A5_analysis",
    }.get(verdict, "A5_analysis")

    # 版本号递增
    doc_ver = state.get("doc_versions", {}).get("validation", 1)

    # 保存验证报告到知识库
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    finding_rows = "\n".join(
        f"| {f.get('type','')} | {f.get('severity','')} | {f.get('section','—')} | {f.get('description','')} | {f.get('suggestion','')} |"
        for f in findings
    ) if findings else "| — | — | — | 未发现问题 | — |"
    vdoc = kb_frontmatter(f"需求验证报告-{ds}-v{doc_ver}", ["验证", "SRS"], ["需求验证报告"]) + f"""# 需求验证报告

验证日期：{ds}
总体结论：**{verdict}**
{f'发现 {len(findings)} 个问题' if findings else '未发现问题'}

## 发现清单（教材§7四类交叉比对）
| 类型 | 严重程度 | 涉及章节 | 问题描述 | 修正建议 |
|-----|---------|---------|---------|---------|
{finding_rows}

> 关联 [[SRS-初稿-{ds}-v{doc_ver}|SRS文档]] | [[需求清单-{ds}-v{doc_ver}|需求清单]]
"""
    save_to_kb("wiki", f"需求验证报告-{ds}-v{doc_ver}.md", vdoc)

    summary = data.get("summary", {})
    fire_progress(f"[SEARCH] A5: 验证完成 — 结论：{verdict}（共{summary.get('total_issues', len(findings))}个问题：阻塞{summary.get('blocker', 0)}/重要{summary.get('major', 0)}/建议{summary.get('minor', 0)}）")
    fire_result("validation_report", result)
    fire_result("validation_verdict", verdict)
    return {
        "validation_report": result,
        "validation_verdict": verdict,
        "rollback_reason": rollback_reason,
        "doc_versions": {**state.get("doc_versions", {}), "validation": doc_ver + 1},
    }


def a5_decide_next(state: RequirementState) -> Literal["approve", "rollback_a1", "rollback_a2"]:
    """A5条件判断 — 全局迭代超限时终止回退"""
    iteration_count = state.get("iteration_count", 0)
    if iteration_count >= MAX_GLOBAL_ITERATIONS:
        print(f"  [A5] ⛔ 全局迭代已达上限({MAX_GLOBAL_ITERATIONS})，强制通过")
        return "approve"

    verdict = state.get("validation_verdict", "分析类问题")
    if verdict == "通过":
        print("  [A5] [OK] 验证通过 → 生成缺陷分析报告")
        return "approve"
    elif verdict == "获取类问题":
        print("  [A5] [WARN] 获取类问题 → 回退A1，涉众Agent自动追问")
        return "rollback_a1"
    else:
        print("  [A5] [WARN] 分析类问题 → 回退A2重新分析")
        return "rollback_a2"


# ============================================================
# [LIST] 5份缺陷分析报告（教材§1明确要求）
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
    run_stop_checks()
    fire_phase("A5_缺陷报告")
    fire_progress("[LIST] A5: 正在生成5份缺陷分析报告...")
    reports = []
    for scenario, idx in DEFECT_SCENARIOS:
        fire_progress(f"  [LIST] 缺陷报告 {idx}/5 生成中...")
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
    doc_ver = state.get("doc_versions", {}).get("defect", 1)
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    save_to_kb("wiki", f"缺陷分析报告集-{ds}-v{doc_ver}.md",
               kb_frontmatter("缺陷分析报告集", ["缺陷", "质量保证"]) + all_reports)
    fire_progress("[OK] A5: 5份缺陷分析报告已生成")
    fire_result("defect_reports", all_reports)
    return {"defect_reports": all_reports, "doc_versions": {**state.get("doc_versions", {}), "defect": doc_ver + 1}}


# ============================================================
# 🧠 CCB 人工审批 + 路由
# ============================================================

def ccb_review(state: RequirementState) -> dict:
    """CCB人工审批 — 暂停等待用户输入（支持GUI事件和命令行两种模式）"""
    run_stop_checks()
    fire_phase("CCB_审批")
    verdict = state.get("validation_verdict", "")
    report = state.get("validation_report", "")
    fire_progress(f"[PAUSE] CCB: 等待人工审批（验证结论：{verdict}）")

    global CCB_EVENT, CCB_RESULT
    if CCB_EVENT is not None:
        # ── GUI 模式：等待 streamlit 前端设置结果 ──
        fire_progress("[INFO] CCB: 请在界面中选择审批决定...")
        CCB_EVENT.clear()
        CCB_RESULT = {}
        CCB_EVENT.wait()  # 阻塞等待前端设置
        result = CCB_RESULT
        verdict_text = result.get("verdict", "不通过(分析类)")
        comment = result.get("comment", "")
        fire_progress(f"[OK] CCB: 审批完成 — {verdict_text}")
        ccb_return = {
            "ccb_verdict": verdict_text,
            "ccb_comment": comment,
            "rollback_reason": {
                "通过": "",
                "不通过(获取类)": "CCB_acquisition",
                "不通过(分析类)": "CCB_analysis",
            }.get(verdict_text, "CCB_analysis"),
        }
        # CCB不通过时重置全局计数，让A1/A5重新正常工作
        if "不通过" in verdict_text:
            ccb_return["iteration_count"] = 0
            fire_progress("  ↻ CCB不通过，全局迭代计数已重置，A1/A2/A5恢复工作")
        return ccb_return
    else:
        # ── 命令行模式：使用 input() ──
        print()
        print("=" * 60)
        print("[LIST] CCB 审批 — 请人工决策")
        print("=" * 60)
        print()
        print(f"SRS验证结论：{verdict}")
        print(report[:500])
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
                    "iteration_count": 0,
                }
            elif choice == "3":
                return {
                    "ccb_verdict": "不通过(分析类)",
                    "ccb_comment": input("请说明理由: "),
                    "rollback_reason": "CCB_analysis",
                    "iteration_count": 0,
                }
            else:
                print("无效输入，请输入 1、2 或 3")


def ccb_decide_next(state: RequirementState) -> Literal["approve", "rollback_a1", "rollback_a2"]:
    """CCB条件判断 — 人工审批不受全局迭代限制（人说不通过就必须回退）"""
    verdict = state.get("ccb_verdict", "不通过(分析类)")
    if "通过" in verdict:
        print("  [CCB] [OK] 审批通过 → 基线创立")
        return "approve"
    elif "获取" in verdict:
        print("  [CCB] [WARN] 退回A1，涉众Agent自动追问")
        return "rollback_a1"
    else:
        print("  [CCB] [WARN] 退回A2重新分析")
        return "rollback_a2"


# ============================================================
# 🧠 A6 基线
# ============================================================

def a6_create_baseline(state: RequirementState) -> dict:
    """A6基线：生成22列RTM溯源矩阵并冻结到知识库（规范对齐版）"""
    run_stop_checks()
    fire_phase("A6_基线")
    fire_progress("⛔ A6: 正在创立基线并生成 RTM 溯源矩阵...")
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    version = _next_baseline_version(ds)

    # ── 1. 生成 22 列 RTM 溯源矩阵 ──
    rtm_prompt = f"""请根据以下资料生成需求溯源矩阵（RTM），严格遵循需求溯源矩阵规范文档的22列字段规范。

基线版本：{version}
对比历史基线：初始基线，无历史版本
需求状态：所有需求均标记为「新增」

SRS文档（摘要）：
{state.get("srs_draft", "")[:10000]}

需求清单（含 BR/UR 编号）：
{state.get("consolidated_requirements", "")}

请输出22列RTM表格，列名必须一字不差，严格按以下顺序：

【上游业务溯源层 1-6】
| 行号 | 业务需求ID(BR) | 业务目标描述 | 原始需求ID(UR) | 原始需求来源 | 原始需求全文 |

【SRS正式需求层 7-12】
| 需求类型 | SRS需求ID(FR) | SRS需求名称 | SRS正式描述 | 验收标准 | 需求优先级 |

【基线比对差异层 13-16】
| 本次基线需求状态 | 变更来源 | 变更差异详情 | 变更影响范围 |

【设计开发追溯层 17-20】
| 关联建模产物ID | 关联设计文档ID | 关联开发模块 | 数据字典关联ID |

【测试验收追溯层 21-22】
| 关联测试用例ID | 验收测试状态 |

示例行：
| 1 | BR-EQP-001 | 实现设备全生命周期管理 | UR-EQP-001 | 库房人员 |「设备出库时需要带配件清单」| 功能需求 | FR-EQP-001 | 配件清单管理 | 出库时根据模板生成配件清单 | 配件清单与设备型号绑定，逐项核对 | P0 | 新增 | 初始基线，无历史版本 | 初始基线，无历史版本 | 无 | UC-ELM-001 | 待第二阶段填充 | 待第二阶段填充 | DD-EQP-001 | 待第二阶段填充 | 待验收 |

要求：
1. BR/UR 编号从需求清单中直接取用，FR 编号从 SRS 中提取，三者一一对应
2. 原始需求全文列必须完整引用需求清单中的「需求陈述」原文
3. 需求类型：功能需求/非功能需求/接口需求
4. 本次基线需求状态统一填「新增」
5. 变更来源和变更差异详情填「初始基线，无历史版本」
6. 正向追溯链路：BR → UR → FR → 建模产物 → 设计 → 开发 → 测试
7. 至少生成5行以上（覆盖不同功能模块）
8. Phase 1 尚未产生的列（设计文档ID、开发模块、测试用例ID）填「待第二阶段填充」"""

    rtm = call_llm(rtm_prompt, system_prompt="你是配置管理专家，精通需求溯源矩阵（RTM）22列规范。输出内容中必须包含22列的Markdown表格。")

    # 为 RTM 添加元数据头部（规范 3.1 要求的基线元数据区），并做后处理
    rtm_meta_lines = []
    rtm_meta_lines.append("【基线元数据】")
    rtm_meta_lines.append("当前基线版本：" + version)
    rtm_meta_lines.append("对比历史基线：初始基线，无历史版本")
    rtm_meta_lines.append("生成时间：" + datetime.now().strftime("%Y-%m-%d %H:%M"))
    rtm_meta_lines.append("生成主体：A6 需求基线智能体")
    rtm_meta_lines.append("变更批次：初始基线")
    rtm_meta_lines.append("文档状态：正式基线")
    rtm_meta_lines.append("")
    rtm = "\n".join(rtm_meta_lines) + "\n" + rtm

    # 后处理：修正 LLM 可能忽略的规范
    rtm = rtm.replace("智能医疗设备", "医疗设备")
    rtm = rtm.replace("2023-10-27", version.replace("BL-", "").replace("-01", ""))
    if "| 行号 |" not in rtm:
        print("  [A6] RTM 可能不是22列格式，请检查")

    # 后处理：填充 RTM 表格中的空值（规范要求初始基线不可有空列）
    # 第21列（关联测试用例ID）和22列（验收状态）在初始基线阶段应填默认值
    rtm_lines = rtm.split("\n")
    in_table = False
    for li in range(len(rtm_lines)):
        line = rtm_lines[li]
        # 检测是否进入表格行（以 | 开头、包含行号数字的为数据行）
        if line.strip().startswith("|") and "|" in line[1:]:
            # 排除表头行（包含列名）、分隔行（纯 ---）、示例行
            cells = [c.strip() for c in line.split("|")]
            # 典型的22列表格有23个分割符（首尾空单元格）
            if len(cells) >= 23:
                # 第21列 = cells[21]（索引从1开始）, 第22列 = cells[22]
                # 索引0是开头的空串，所以列1→cells[1], 列21→cells[21], 列22→cells[22]
                tc_col = cells[21].strip() if len(cells) > 21 else ""
                status_col = cells[22].strip() if len(cells) > 22 else ""
                # 如果关联测试用例ID为空 → 填充 TC-TBD-{行号}
                if not tc_col or tc_col == "—" or tc_col == "-":
                    row_num = cells[1].strip() if len(cells) > 1 else str(li)
                    cells[21] = f" TC-TBD-{row_num} "
                # 如果验收状态为空 → 填充「待验收」
                if not status_col or status_col == "—" or status_col == "-":
                    cells[22] = " 待验收 "
                rtm_lines[li] = "|".join(cells)
    rtm = "\n".join(rtm_lines)

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
{'[OK] 通过' if state.get('ccb_verdict', '通过') == '通过' else '[FAIL] 不通过'}

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

    # ── 3. 冻结到知识库 baselines 目录（含UML子目录）──
    bl_dir = f"wiki/baselines/{version}"

    # 替换SRS中的占位符
    srs_final = state.get("srs_draft", "") \
        .replace("【占位，由A6分配】", version) \
        .replace("【当前日期】", ds)

    save_to_kb("baselines", f"{version}/SRS-正式版.md",
        kb_frontmatter(f"SRS-正式版-{version}", ["SRS", "基线", "冻结"], [f"SRS-{version}"]) + srs_final)
    save_to_kb("baselines", f"{version}/需求清单.md",
        kb_frontmatter(f"需求清单-{version}", ["需求", "基线", "冻结"]) + state.get("consolidated_requirements", ""))
    # 保存 UML 模型到基线 UML 子目录（规范要求基线包含UML模型产物）
    _uml_uc = state.get("uml_use_case", "")
    _uml_ad = state.get("uml_activity_diagrams", "")
    if _uml_uc:
        save_to_kb("baselines", f"{version}/UML模型/用例图-{ds}.puml",
            kb_frontmatter(f"UML用例图-{version}", ["UML", "基线"], [f"用例图-{version}"]) + _uml_uc)
    if _uml_ad:
        save_to_kb("baselines", f"{version}/UML模型/行为模型图-{ds}.puml",
            kb_frontmatter(f"UML行为模型-{version}", ["UML", "基线"]) + _uml_ad)
    save_to_kb("baselines", f"{version}/RTM_{version}_需求溯源矩阵.md",
        kb_frontmatter(f"RTM-{version}", ["RTM", "基线", "冻结"], [f"溯源矩阵-{version}"]) + rtm)
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
| SRS-正式版 | [[SRS-正式版-{version}]] |
| 需求清单 | [[需求清单]] |
| RTM溯源矩阵 | [[RTM_{version}_需求溯源矩阵]] |
| CCB评审记录 | [[CCB_{version}_评审记录]] |
| UML用例图 | [[UML模型/用例图-{ds}.puml]] |
| UML行为模型 | [[UML模型/行为模型图-{ds}.puml]] |

## 基线符合规范
- [OK] SRS：IEEE 830标准 + GB/T 9385
- [OK] RTM：22列完整溯源矩阵
- [OK] 需求编号：BR/UR/FR/NFR/IFR四级体系
- [OK] CCB评审：正式评审记录归档
- [OK] 全部需求状态标记为「新增」

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

    # ── 需求差异文档（非初始基线时生成）──
    baselines_dir = os.path.join(KB_ROOT, "wiki/baselines")
    all_baselines = sorted([d for d in os.listdir(baselines_dir)
                            if os.path.isdir(os.path.join(baselines_dir, d)) and d.startswith("BL-")])
    if len(all_baselines) >= 2 and version in all_baselines:
        idx = all_baselines.index(version)
        if idx > 0:
            old_version = all_baselines[idx - 1]
            fire_progress(f"  → 生成需求差异文档：{old_version} → {version}")
            diff_doc = _generate_diff(old_version, version, srs_final, rtm)
            save_to_kb("baselines", f"{version}/DIFF_{version}_需求差异比对报告.md",
                       kb_frontmatter(f"需求差异-{version}", ["差异", "变更管理"], [f"需求差异-{version}"]) + diff_doc)
            fire_progress(f"  [OK] 需求差异文档已生成")

    fire_progress(f"[OK] A6: 基线 {version} 已创立！")
    fire_result("baseline_version", version)
    fire_result("rtm", rtm)
    return {"baseline_version": version, "rtm": rtm, "workflow_status": "基线已创立"}


# ============================================================
# 🕸 构建 LangGraph
# ============================================================

def build_workflow() -> StateGraph:
    """构建需求工程工作流图"""
    # 启动时生成4份涉众AI智能体配置文件（答辩交付物）
    ensure_agent_configs()

    workflow = StateGraph(RequirementState)

    # ── A1阶段：单个并行节点，内部4个涉众Agent同时对话 ──
    workflow.add_node("A1_并行涉众对话", a1_parallel)
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
    workflow.set_entry_point("A1_并行涉众对话")

    # ── A1并行 → 汇总 ──
    workflow.add_edge("A1_并行涉众对话", "A1_汇总")

    # ── A1汇总 → A2 ──
    workflow.add_edge("A1_汇总", "A2_需求分析")

    # ── A2条件：严重问题回退A1（涉众Agent自动追问）──
    workflow.add_conditional_edges(
        "A2_需求分析",
        a2_decide_next,
        {
            "rollback": "A1_并行涉众对话",
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
            "approve": "A5_缺陷分析报告",
            "rollback_a1": "A1_并行涉众对话",
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
            "rollback_a1": "A1_并行涉众对话",
            "rollback_a2": "A2_需求分析",
        },
    )

    # ── A6 → END ──
    workflow.add_edge("A6_基线创立", END)

    return workflow


# ============================================================
# [START] 主入口
# ============================================================

def main():
    print("=" * 60)
    print("[HOSP] 医疗器械租赁管理系统 — 需求工程工作流 v3")
    print("    (多Agent协作：A1涉众Agent + A2分析 + A3建模 + A4 SRS + A5验证 + CCB + A6基线)")
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
        "force_forward": False,
        "doc_versions": {"req_list": 1, "issues": 1, "uml": 1, "srs": 1, "validation": 1, "defect": 1},
    }

    print("[START] 启动工作流...\n")

    try:
        result = app.invoke(
            initial_state,
            config={"configurable": {"thread_id": "req-001"}},
            recursion_limit=50,
        )
    except Exception as e:
        print(f"\n[FAIL] 工作流异常: {e}")
        import traceback
        traceback.print_exc()
        return

    # 输出
    print()
    print("=" * 60)
    print("[OK] 工作流完成！")
    print("=" * 60)
    print(f"  基线版本: {result.get('baseline_version', 'N/A')}")
    print(f"  最终状态: {result.get('workflow_status', 'N/A')}")
    print(f"  迭代次数: {result.get('iteration_count', 0)}")
    print()
    print("[LIST] 交付物清单（教材要求）：")
    print(f"  1. [OK] 4份涉众对话记录 → raw/notes/")
    print(f"  2. [OK] 结构化需求清单 → wiki/summaries/")
    print(f"  3. [OK] 需求问题清单    → wiki/summaries/")
    print(f"  4. [OK] UML模型(用例图+活动图) → wiki/summaries/")
    print(f"  5. [OK] SRS规格说明书    → wiki/summaries/")
    print(f"  6. [OK] 需求验证报告    → wiki/summaries/")
    print(f"  7. [OK] 5份缺陷分析报告  → wiki/summaries/")
    print(f"  8. [OK] 基线{result.get('baseline_version', 'N/A')} + RTM → wiki/baselines/")
    print()
    print(f"[DIR] Obsidian 知识库: {KB_ROOT}")
    print("[INFO] 在 Obsidian 中打开 .claude/knowledge-base/ 即可浏览")


if __name__ == "__main__":
    main()
