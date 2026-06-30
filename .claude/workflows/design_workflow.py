# 医疗器械租赁管理系统 — 软件设计 LangGraph 工作流 v1
#
# 核心设计：
#   Phase 2 六步工作流（B1-B6 + CCB），两个入口、两个暂停点
#   - 入口1: Phase 1 产出 → B1 完整运行
#   - 入口2: 老师给 CR → B6 变更模式
#   - 暂停点1: B5 漂移决策 👤
#   - 暂停点2: CCB 变更审批 👤
#
# 工作流：
#   B_入口路由 ──▶ B1 或 B6
#   B1 → B2 → B3 → B4 → B5 ──▶ BL01 → B6 ──▶ CCB → END
#                     ├─回B3  ├─回B4  └─继续
#                              B6: ──▶ B3(子循环) 或 CCB
#
# 使用方法：
#   1. pip install langgraph python-dotenv requests
#   2. 创建 .env 文件，填入 API Key
#   3. python design_workflow.py          # CLI 完整运行
#   4. python design_workflow.py change   # CLI 变更模式
#
# ════════════════════════════════════════════════════════════════════════════════
# 第二阶段 — 软件设计与体系结构 工作流流程图
# ════════════════════════════════════════════════════════════════════════════════
#
#                               ┌──────────────┐
#                               │  Phase 1 产出  │
#                               │ SRS + RTM     │
#                               └──────┬───────┘
#                                      │
#                                      ▼
#                               ┌──────────────┐
#                               │  B1 架构选型   │
#                               │ 五维评估+ADR001│
#                               │ + ASD         │
#                               └──────┬───────┘
#                                      │
#                                      ▼
#                               ┌──────────────┐
#                               │  B2 工程产物   │
#                               │ MDS+DTS+ADR   │
#                               │ +四维质量校验   │
#                               └──────┬───────┘
#                                      │
#                                      ▼
#                               ┌──────────────┐
#                               │  B3 约束契约   │
#                               │ TLCD + OAS    │
#                               └──────┬───────┘
#                                      │
#                                      ▼
#                               ┌──────────────┐
#                               │  B4 代码生成   │ ◄───────────┐
#                               │ 5 Pass分层生成 │             │
#                               │ 🧠 注入项目记忆 │             │
#                               └──────┬───────┘             │
#                                      │                      │
#                                      ▼                      │
#                               ┌──────────────┐             │
#                               │  B5 逆向校验   │             │
#                               │ RCR 4类漂移检测 │             │
#                               │ 🧠 注入项目记忆 │             │
#                               └──┬──┬───┬────┘             │
#                         路径一    │  │   │ 路径三            │
#                     修代码回B4    │  │   │ 标记临时(继续)    │
#                     ┌────────────┘  │   └────────┐         │
#                     ▼               ▼            ▼         │
#               ┌──────────┐  ┌──────────────┐  ┌──────┐    │
#               │ 回到 B4  │  │   路径二      │  │ 通过  │    │
#               │ 修改代码  │  │ 回 B3 改设计  │  │ 继续  │    │
#               └──────────┘  └──────┬───────┘  └──┬───┘    │
#                                    │              │         │
#                                    ▼              ▼         │
#                              ┌──────────┐  ┌──────────────┐ │
#                              │ 回到 B3  │  │  BL01 基线    │ │
#                              │ 更新约束  │  │ 冻结B1-B5产物 │ │
#                              └──────────┘  └──────┬───────┘ │
#                                                    │         │
#                                          老师给 CR─┘         │
#                                                    │         │
#                                                    ▼         │
#                                             ┌──────────────┐ │
#                                             │  B6 变更闭环  │ │
#                                             │ CR→CIA       │ │
#                                             │ 🧠 注入项目记忆 │ │
#                                             └──┬──┬───┬───┘ │
#                               更新约束/契约回B3│  │   │ 回归校验│
#                                             │  │   │ 回B5   │
#                               ┌─────────────┘  │   └───┐   │
#                               ▼                ▼       ▼   │
#                         ┌──────────┐   ┌──────────────┐ ┌──┐│
#                         │ 回到 B3  │   │  ADR-005     │ │回││
#                         │ 更新约束  │   │  新基线BL-02  │ │B5││
#                         └──────────┘   │  RTM补填+资产 │ └──┘│
#                                        └──────┬───────┘     │
#                                               │             │
#                                               ▼             │
#                                        ┌──────────────┐     │
#                                        │  CCB 变更审批  │     │
#                                        │ 👤 暂停等待     │     │
#                                        └──────┬───────┘     │
#                                               │              │
#                                               ▼              │
#                                        ┌──────────────┐     │
#                                        │   最终交付包   │     │
#                                        └──────────────┘     │
#                                                              │
#   B6 子循环: B6→B3→B4→B5→B6 (最多2轮) ──────────────────────┘
#   B5 漂移修复后: 回到 B4, 重跑 B4→B5 ───────────────────────┘
#
# 三个回退路径：
#   路径一 (B5 代码错误):  B5→👤决策→回 B4 修代码→重跑 B5 (RCR)
#   路径二 (B5 设计不合理): B5→👤决策→回 B3 改设计→更新 ADR+约束→重跑 B4→B5
#   路径三 (B5 临时偏差):  B5→👤决策→标记风险→当作通过, 继续 BL01→B6
#   B6 变更子循环:         B6→CIA→回 B3 更新约束→B4 重构 v2→B5 回归校验→回B6
#
# 两个入口 + 两个 👤 暂停点：
#   入口1: Phase 1 产出 → B1 进入（首次完整运行）
#   入口2: 老师给 CR → B6 进入（变更模式，跳过 B1-B5）
#   暂停点1: B5 漂移清单 → 👤 人逐条决策（路径一/二/三）
#   暂停点2: CCB 变更审批 → 👤 人审批（与 Phase 1 机制一致）
#
# 🧠 记忆注入 (教材§11):
#    ┌─ B1 产出 ASD ─┐
#    │ B2 产出 MDS+DTS│── state 中存在 ──┐
#    └─ B3 产出 TLCD+OAS ────────────────┘
#                                          │
#    B4 / B5 / B6 节点内部:                 ▼
#    每次要调 LLM 时, 不直接调 call_llm(), 而是调 call_llm_with_memory(task, state)
#         │
#         ├─→ inject_project_memory(task, state)   ← 从 state 取 B1-B3 拼成三层说明书
#         │        Layer 1: 项目背景 (不变)
#         │        Layer 2: ASD+TLCD+MDS+OAS (自动提取)
#         │        Layer 3: 当前任务 (每次不同)
#         │
#         └─→ call_llm(拼好的完整prompt)            ← 发给 DeepSeek
#
#    这样 B4(5次)/B5(1次)/B6(1次) 每次发给 LLM 的约束部分措辞完全一致。
#
# 安全约束：
#   - B5 漂移回退最多 3 轮, 超限记录风险后强制通过
#   - B6 内 B3→B4→B5 子循环最多 2 轮, 防止无限变更

import os
import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import json
import threading
from typing import TypedDict, List, Literal, Callable, Optional
from datetime import datetime

from dotenv import load_dotenv
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
load_dotenv(_env_path)

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# ============================================================
# 从 Phase 1 导入共享基础设施（与 SSE 服务器同路径，确保单例）
# ============================================================
import sys as _sys
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in _sys.path:
    _sys.path.insert(0, _THIS_DIR)

from requirement_workflow import (
    call_llm, save_to_kb, kb_frontmatter, extract_json,
    run_stop_checks, WorkflowStopped,
    fire_progress, fire_result, fire_phase,
    set_progress_callbacks, set_result_callbacks, set_phase_callbacks,
    PAUSE_REQUESTED, RESUME_SIGNAL, STOP_REQUESTED,
    set_pause_requested, set_resume_signal, set_stop_requested,
    MAX_GLOBAL_ITERATIONS,
    LLM_API_KEY, LLM_API_URL, LLM_MODEL, KB_ROOT,
    KB_DIRS,
    get_last_usage, get_session_stats,
)

# ============================================================
# ⚙ Phase 2 专属配置
# ============================================================

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# Phase 2 知识库目录
_DESIGN_ROOT = os.path.join(KB_ROOT, "wiki", "design")
KB_DIRS_DESIGN = {
    "adr": os.path.join(_DESIGN_ROOT, "adr"),
    "spec": os.path.join(_DESIGN_ROOT, "spec"),
    "contracts": os.path.join(_DESIGN_ROOT, "contracts"),
    "reports": os.path.join(_DESIGN_ROOT, "reports"),
    "baselines": os.path.join(KB_ROOT, "wiki", "baselines"),
}
# 确保目录存在
for _d in KB_DIRS_DESIGN.values():
    os.makedirs(_d, exist_ok=True)

# 代码生成目录
PROJECT_SRC = os.path.join(_PROJECT_ROOT, "project", "src")
os.makedirs(PROJECT_SRC, exist_ok=True)

# ============================================================
# Phase 2 暂停事件（独立于 Phase 1）
# ============================================================

# B5 漂移决策暂停
B5_DRIFT_EVENT: Optional[threading.Event] = None
B5_DRIFT_RESULT: dict = {}

def set_b5_event(event: threading.Event):
    global B5_DRIFT_EVENT
    B5_DRIFT_EVENT = event

def set_b5_result(decisions: list):
    global B5_DRIFT_RESULT
    B5_DRIFT_RESULT = {"decisions": decisions}
    if B5_DRIFT_EVENT:
        B5_DRIFT_EVENT.set()

# Phase 2 CCB 审批暂停
CCB2_EVENT: Optional[threading.Event] = None
CCB2_RESULT: dict = {}

def set_ccb2_event(event: threading.Event):
    global CCB2_EVENT
    CCB2_EVENT = event

def set_ccb2_result(verdict: str, comment: str = ""):
    global CCB2_RESULT
    CCB2_RESULT = {"verdict": verdict, "comment": comment}
    if CCB2_EVENT:
        CCB2_EVENT.set()

# ============================================================
# 📊 DesignState — Phase 2 全局状态
# ============================================================

class DesignState(TypedDict):
    # ---- 输入（来自 Phase 1） ----
    srs_input: str                      # SRS 全文（Phase 1 基线）
    rtm_input: str                      # RTM（Phase 1 基线）
    consolidated_requirements: str      # 需求清单
    semantic_model: str                 # B0: SRS语义提取结果 (JSON: entities/functionals/rules)

    # ---- 运行模式 ----
    entry_mode: str                     # "full_run" | "change_mode"

    # ---- 日期时间戳 ----
    date_str: str
    time_str: str

    # ---- B1: 架构选型 ----
    adr_001: str                        # ADR-001 架构选型决策
    asd: str                            # Architecture Style Declaration

    # ---- B2: 工程产物定义 ----
    mds: str                            # Module Decomposition Spec
    dts: str                            # Dependency Topology Spec
    adr_002_004: str                    # ADR-002~4 (技术栈/数据库/部署)

    # ---- B3: 约束与接口契约 ----
    tlcd: str                           # Three-Layer Constraint Design
    openapi_yaml: str                   # OpenAPI 3.0 YAML

    # ---- B4: AI代码生成 ----
    source_code_summary: str            # 生成摘要
    source_code_path: str               # 代码根目录
    generated_code: str                 # 全部生成代码（供实现记忆注入）
    forward_graph: str                  # 正向知识图谱 JSON (Component/Interface/Constraint)
    reverse_graph: str                  # 逆向知识图谱 JSON (从代码提取)

    # ---- B5: 逆向校验 ----
    drift_list_raw: str                 # LLM 原始输出
    drift_items: List[dict]             # 解析后的漂移条目
    drift_decisions: List[dict]         # 人工逐条决策
    drift_round: int                    # 当前轮次
    drift_resolved: bool                # 是否已全部解决

    # ---- B6: 变更闭环 ----
    cr_document: str                    # 变更需求文档（入口2传入）
    cia: str                            # 变更影响分析报告
    adr_005: str                        # ADR-005 变更决策
    asset_pack: str                     # 5层设计资产包摘要
    change_round: int                   # B6 子循环轮次

    # ---- 基线 ----
    bl_01_version: str                  # BL-01 版本号
    bl_02_version: str                  # BL-02 版本号

    # ---- CCB ----
    ccb_verdict: str
    ccb_comment: str

    # ---- 控制 ----
    iteration_count: int
    workflow_status: str

# ============================================================
# 工具函数
# ============================================================

def save_to_kb_design(subdir: str, filename: str, content: str) -> str:
    """保存文件到 Phase 2 知识库目录。"""
    base = KB_DIRS_DESIGN.get(subdir, os.path.join(_DESIGN_ROOT, subdir))
    full_path = os.path.join(base, filename)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return full_path


# ── ADR 生命周期管理（教材§11 第四小节 要点一）──────────────────────────
ADR_STATUSES = ("提议", "已接受", "已废弃", "已替代")


def kb_frontmatter_adr(title: str, status: str = "已接受", replaces: str = "", tags: list[str] | None = None) -> str:
    """生成 ADR 专用 frontmatter，含 status 字段和替代引用。

    Args:
        title: ADR 标题
        status: 决策状态，可选 提议/已接受/已废弃/已替代
        replaces: 被本 ADR 替代的旧 ADR 编号（如 "ADR-001"），无替代则为空
        tags: 额外标签
    """
    if status not in ADR_STATUSES:
        status = "已接受"
    t = ["ADR", "Phase2"]
    if tags:
        t.extend(tags)
    fm = f"---\ntitle: {title}\ntags: [{' '.join(t)}]\nadr_status: {status}\n"
    if replaces:
        fm += f"replaces: {replaces}\n"
    fm += "---\n\n"
    return fm


def update_adr_status(adr_filename: str, new_status: str, replaced_by: str = "") -> str | None:
    """更新 ADR 文档的 frontmatter status 字段（废弃不删除原则）。

    将旧 ADR 的 status 从 '已接受' 改为 '已废弃' 或 '已替代'，
    并可选填写 replaced_by 指向替代它的新 ADR。

    Args:
        adr_filename: ADR 文件名（不含路径，如 "ADR-001-架构选型决策-20260630.md"）
        new_status: 新状态
        replaced_by: 替代本 ADR 的新 ADR 编号（如 "ADR-005"）

    Returns:
        更新后的文件路径，文件不存在则返回 None
    """
    adr_dir = KB_DIRS_DESIGN["adr"]
    full_path = os.path.join(adr_dir, adr_filename)
    if not os.path.exists(full_path):
        return None
    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()
    # 修改 frontmatter 中的 status
    import re
    content = re.sub(r"adr_status:\s*\S+", f"adr_status: {new_status}", content)
    if replaced_by and "replaced_by:" not in content:
        # 在 adr_status 行后插入 replaced_by
        content = re.sub(r"(adr_status:.*\n)", rf"\1replaced_by: {replaced_by}\n", content)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return full_path


# ── Stats 数据趋势监控（教材§11 第四小节 要点三）──────────────────────────
_STATS_LOG = []  # 内存累积，每 N 次写一次磁盘


def save_stats_snapshot(ds: str, force: bool = False) -> str | None:
    """将当前会话的 Stats 快照写入 wiki/summaries/。

    缓存命中率 = cache_read / (cache_read + non_cache_prompt) * 100%
    """
    stats = get_session_stats()
    if stats["total_calls"] == 0:
        return None
    # 默认每 10 次调用写一次，force=True 时强制写入
    if not force and stats["total_calls"] % 10 != 0:
        return None
    hit_rate = stats.get("cache_hit_rate", 0)
    content = f"""---
title: Stats-缓存命中率趋势
tags: [Stats, 记忆管理, 效率]
date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---

## Stats 快照 ({ds})

| 指标 | 值 |
|------|-----|
| 累计调用次数 | {stats['total_calls']} |
| 总 Prompt Token | {stats['total_prompt_tokens']:,} |
| 总 Completion Token | {stats['total_completion_tokens']:,} |
| 缓存写入 Token | {stats['total_cache_creation']:,} |
| 缓存命中读取 Token | {stats['total_cache_read']:,} |
| **缓存命中率** | **{hit_rate}%** |

## 健康评估
"""
    if hit_rate >= 80:
        content += f"- ✅ 缓存命中率 {hit_rate}% ≥ 80%，记忆管理健康。\n"
    elif hit_rate >= 60:
        content += f"- ⚠️ 缓存命中率 {hit_rate}% 在 60-80% 之间，建议检查固定记忆层是否频繁修改。\n"
    elif hit_rate > 0:
        content += f"- ❌ 缓存命中率 {hit_rate}% < 60%，可能存在知识图谱覆盖度不足或约束文件频繁变动。\n"
    else:
        content += "- ⏳ 缓存命中率数据尚未可用（API 未返回缓存明细）。\n"
    content += f"\n> 教材§11: Stats数据是记忆管理健康的「体检指标」。命中率异常下降 > 回溯分析固定记忆层变更 > 定位根因 > 调整记忆结构。\n"
    path = save_to_kb_design("reports", f"stats-缓存命中率趋势-{ds}.md", content)
    return path


# ── 设计-实现差距追踪（教材§11 第四小节 要点二）──────────────────────────
def save_design_gap_report(state: DesignState, ds: str) -> str | None:
    """B6 逆向校验后，将设计记忆与实现记忆的差距持久化。

    对比 state 中的设计产物（ASD/MDS/DTS/TLCD/OAS）与 B6 的 RCR 结果，
    生成结构化的「设计-实现差距清单」，供后续漂移修复追踪。
    """
    rcr = state.get("rcr_result", "")
    if not rcr:
        return None
    # 提取设计产物的关键约束计数
    asd = state.get("asd", "")
    mds = state.get("mds", "")
    tlcd = state.get("tlcd", "")
    oas = state.get("openapi_yaml", "")
    # 粗略统计设计约束条数
    import re
    asd_rules = len(re.findall(r"L-\d{2}", asd))
    c_arch = len(re.findall(r"C-ARCH-\d{3}", tlcd))
    c_mod = len(re.findall(r"C-MOD-\d{3}", tlcd))
    c_code = len(re.findall(r"C-CODE-\d{3}", tlcd))
    # 从 RCR 结果中提取漂移统计
    drifts_found = rcr.count("violated_rule") if "violated_rule" in rcr else -1
    content = f"""---
title: 设计-实现差距报告
tags: [差距追踪, 记忆管理, Phase2]
date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---

## 设计记忆（正向图谱）概览

| 产物 | 约束/定义数量 |
|------|-------------|
| ASD 架构约束 | {asd_rules} 条 |
| TLCD C-ARCH | {c_arch} 条 |
| TLCD C-MOD | {c_mod} 条 |
| TLCD C-CODE | {c_code} 条 |
| OAS 契约 | {'已生成' if oas else '未生成'} |

## 实现记忆（逆向图谱）比对

| 指标 | 状态 |
|------|------|
| RCR 逆向校验 | {'已完成' if rcr else '未执行'} |
| 检测到的漂移条目 | {drifts_found if drifts_found >= 0 else '统计中...'} |

## 差距分析

"""
    if drifts_found == 0:
        content += "- ✅ 设计记忆与实现记忆一致，无设计漂移。\n"
    elif drifts_found > 0:
        content += f"- ⚠️ 检测到 {drifts_found} 处漂移，需逐项修复或更新设计约束。\n"
        content += "- 修复路径: 漂移→RCR重检→确认消除→记录ADR（如设计变更）。\n"
    else:
        content += "- ⏳ RCR 结果解析中，漂移统计待确认。\n"
    content += f"\n> 教材§11: 设计记忆和实现记忆之间的差距在第14节逆向校验中被自动检测。它不是错误，而是一个被显式追踪的工作项。\n"
    path = save_to_kb_design("reports", f"设计-实现差距报告-{ds}.md", content)
    return path


# ============================================================
# 🧠 三段式记忆注入（教材§11: Reasonix 的 LangGraph 替代实现）
# ============================================================
# 核心思想: 每次 LLM 调用前, 自动从 DesignState 中提取项目约束,
# 拼接为统一格式的「固定约束块」注入 prompt 头部,
# 确保 AI 在每次生成时看到的约束措辞完全一致。

def inject_project_memory(task_prompt: str, state: DesignState, sections: list[str] | None = None) -> str:
    """将项目记忆三段注入任务 prompt。默认注入全部, 可通过 sections 精简。

    三段结构:
      Layer 1 (元记忆): 项目背景 + 角色定义 — 最稳定
      Layer 2 (项目记忆): ASD/TLCD/MDS/OAS/ADR + 已有代码 — 每次自动加载同一套约束
      Layer 3 (会话记忆): 当前具体任务 — 每次不同
    """

    # ── Layer 1: 元记忆 (稳定, 不变) ──
    meta = f"""## 项目背景
你是「医疗器械租赁管理系统」的开发AI, 代码必须严格遵守以下设计约束。
这些约束来自已评审的架构决策, 不可自行修改或忽略。

"""

    # ── Layer 2: 项目记忆 (从 state 提取, 每次自动加载) ──
    # 注入顺序遵循「固定前缀 + 变动后缀」缓存优化:
    #   固定前缀: C-ARCH + C-CODE — 所有模块相同，高缓存命中
    #   变动后缀: C-MOD + OAS + MDS — 每个模块不同，按需注入
    #   参考背景: ADR + 已有代码 — 不参与缓存但提供决策上下文

    project_blocks = []

    tlcd = state.get("tlcd", "")
    asd = state.get("asd", "")

    # ── 固定前缀 ①: C-ARCH 架构层全局禁止规则 ──
    c_arch_from_asd = _extract_by_prefix(asd, r"L-\d{2,3}") if asd else ""
    c_arch_from_tlcd = _extract_by_prefix(tlcd, r"C-ARCH-\d{3}") if tlcd else ""
    c_arch = (c_arch_from_asd + "\n" + c_arch_from_tlcd).strip()
    if c_arch:
        project_blocks.append(f"## C-ARCH 架构层约束（全局禁止规则 — 所有模块强制遵守）\n{c_arch[:2000]}")

    # ── 固定前缀 ②: C-CODE 代码层禁止规则 ──
    c_code = _extract_by_prefix(tlcd, r"C-CODE-\d{3}") if tlcd else ""
    if c_code:
        project_blocks.append(f"## C-CODE 代码层约束（全局编码禁止规则 — 所有模块强制遵守）\n{c_code[:1500]}")

    # ── 变动后缀 ①: C-MOD 当前模块的禁止规则 + 依赖白名单 ──
    c_mod = _extract_by_prefix(tlcd, r"C-MOD-\d{3}") if tlcd else ""
    if c_mod:
        project_blocks.append(f"## C-MOD 模块层约束（模块禁止规则 + 依赖白名单 — 按模块变化）\n{c_mod[:2000]}")

    # ── 变动后缀 ②: OAS 接口契约 ──
    oas = state.get("openapi_yaml", "")
    if oas and (sections is None or "oas" in sections):
        oas_block = f"## 接口契约 (OpenAPI)\n{oas[:2000]}"
        # 契约演化约束：检测是否有旧版本遗留字段
        import re as _re_oas
        has_deprecated = _re_oas.search(r'deprecated:\s*true', oas)
        if has_deprecated or "x-changelog" in oas:
            oas_block += ("\n\n### 契约演化约束（向后兼容）\n"
                          "- 新增字段不得修改已有字段的 type、format 或语义\n"
                          "- 标记 `deprecated: true` 的字段仍须保留在响应中，调用方可能仍在使用\n"
                          "- 移除已废弃字段前须检查 x-changelog 确认距标记废弃已过一个版本周期\n"
                          "- 向后兼容是强制性约束——违反本条将导致已有调用方运行时崩溃")
        project_blocks.append(oas_block)

    # ── 变动后缀 ③: MDS 模块职责 ──
    mds = state.get("mds", "")
    if mds and (sections is None or "mds" in sections):
        project_blocks.append(f"## 模块划分方案 (MDS)\n{mds[:1500]}")

    # ── 参考背景: ADR 决策记忆 ──
    adr_001 = state.get("adr_001", "")
    adr_002_004 = state.get("adr_002_004", "")
    if adr_001 or adr_002_004:
        adr_block = "## 架构决策记录 (ADR)\n"
        if adr_001:
            adr_block += f"### ADR-001 (架构选型)\n{adr_001[:1000]}\n"
        if adr_002_004:
            adr_block += f"### ADR-002~4 (技术栈/数据库/部署)\n{adr_002_004[:1000]}\n"
        project_blocks.append(adr_block)

    # ── 参考背景: 实现记忆（已有代码结构）──
    existing_code = state.get("generated_code", "")
    if existing_code and (sections is None or "code" in sections):
        code_summary = _summarize_code_structure(existing_code)
        project_blocks.append(f"## 已有代码结构 (实现记忆)\n{code_summary}")

    project = "\n\n".join(project_blocks) if project_blocks else "(项目记忆尚未生成)"

    # ── Layer 3: 会话记忆 (任务特定, 每次不同) ──
    session = f"\n\n## 当前任务\n{task_prompt}"

    return meta + project + session


def _extract_by_prefix(text: str, prefix_pattern: str, max_len: int = 2000) -> str:
    """从约束文档中按前缀模式提取约束条款。

    例如 prefix_pattern=r"C-ARCH-\\d{3}" 只提取架构层约束，
    prefix_pattern=r"C-MOD-\\d{3}" 只提取模块层约束。

    这使得固定前缀（C-ARCH + C-CODE）和变动后缀（C-MOD + OAS）
    可以独立控制，最大化缓存命中率。
    """
    import re
    constraints = re.findall(rf'(?:^|\n)({prefix_pattern}[^\n]*)', text[:max_len * 2])
    if constraints:
        return "\n".join(constraints[:30])[:max_len]
    return ""


def _extract_constraints(text: str, max_len: int = 2000) -> str:
    """从文档中提取所有约束条款（L-xx / C-xxx-xxx 格式），保留向后兼容。"""
    import re
    constraints = re.findall(r'(?:^|\n)([CL]-\d{2,3}[^\n]*)', text[:max_len * 2])
    if constraints:
        return "\n".join(constraints[:30])[:max_len]
    return text[:max_len]


def _summarize_code_structure(code: str, max_len: int = 1500) -> str:
    """从代码中提取结构摘要（类名/方法签名/包名），作为实现记忆注入。

    不注入完整源码，只注入结构骨架——足够让 AI 理解已有代码的模块边界和方法签名，
    避免在增量代码生成时与已有实现产生命名冲突或职责重叠。
    """
    import re
    lines = []
    # 提取 package / import（仅首部）
    pkg_match = re.search(r'^package\s+(\S+);', code, re.MULTILINE)
    if pkg_match:
        lines.append(f"包: {pkg_match.group(1)}")
    # 提取类/接口定义
    for m in re.finditer(r'(?:public\s+)?(class|interface|enum)\s+(\w+)', code):
        lines.append(f"{m.group(1)} {m.group(2)}")
    # 提取方法签名
    for m in re.finditer(r'(?:public|private|protected)\s+\w+\s+(\w+)\s*\(', code):
        lines.append(f"  方法: {m.group(1)}()")
    if not lines:
        # 回退：取前 max_len 字符
        return code[:max_len]
    summary = "\n".join(lines[:40])
    return summary[:max_len] if len(summary) > max_len else summary


# ── SRS 语义提取（教材§12 第二阶段: 架构分析智能体）─────────────────────
def extract_srs_semantics(srs_text: str, reqs_text: str) -> dict:
    """从 SRS 中提取三层语义信息，输出结构化的需求语义模型。

    返回 JSON-serializable dict:
      {entities: [...], functionals: [...], rules: [...]}
    """
    prompt = f"""你是需求分析专家。读取以下 SRS，提取三层语义信息。

## SRS 文档
{srs_text[:10000]}

## 需求清单
{reqs_text[:4000]}

## 要求
提取以下三层信息，输出严格 JSON:

### 实体层
识别所有业务实体，每个含: name, attributes (属性列表), description

### 功能层
识别所有功能需求，每个含: id, description, 归属的实体名称

### 规则层
识别所有业务规则和约束条件，每个含: id, description, type (业务规则|约束条件)

## 输出格式（严格JSON）
{{
  "entities": [
    {{"name": "Order", "attributes": ["orderId", "status", "createTime"], "description": "租赁订单"}}
  ],
  "functionals": [
    {{"id": "F-001", "description": "用户扫码取件", "entity": "Order"}}
  ],
  "rules": [
    {{"id": "R-001", "description": "取件码有效期为24小时", "type": "业务规则"}},
    {{"id": "R-002", "description": "系统应支持至少1000个格口的同时管理", "type": "约束条件"}}
  ]
}}

只输出 JSON。"""

    result = call_llm(prompt, system_prompt="你是资深需求分析专家，精通SRS语义提取和领域建模。", max_tokens=4000)
    json_result = extract_json(result)
    if not json_result:
        json_result = {"entities": [], "functionals": [], "rules": []}
    return json_result


# ── 正向知识图谱构建（教材§12 第二阶段: 基于设计产物构建图谱）───────────
def build_forward_graph(state: DesignState) -> str:
    """从四种设计产物构建正向知识图谱（设计意图图谱）。

    节点类型: Component / Interface / Constraint
    边类型:   provides / complies / depends

    返回 JSON 字符串。
    """
    import re
    graph = {"nodes": [], "edges": []}

    # ── Component 节点: 从 MDS 提取 ──
    mds = state.get("mds", "")
    if mds:
        try:
            mds_json = extract_json(mds)
        except Exception:
            mds_json = {}
        modules = mds_json.get("modules", []) if isinstance(mds_json, dict) else []
        for mod in modules:
            if isinstance(mod, dict):
                graph["nodes"].append({
                    "id": mod.get("name", "Unknown"),
                    "type": "Component",
                    "responsibility": mod.get("responsibility", ""),
                    "layer": _infer_layer(mod.get("name", "")),
                    "interfaces": mod.get("interfaces", []),
                })

    # ── Interface 节点: 从 OAS 提取 ──
    oas = state.get("openapi_yaml", "")
    if oas:
        paths = re.findall(r'(POST|GET|PUT|DELETE)\s+(/\S+)', oas)
        for method, path in paths:
            node_id = f"IF-{method}-{path.replace('/', '_')}"
            graph["nodes"].append({
                "id": node_id,
                "type": "Interface",
                "method": method,
                "path": path,
            })
            # provides 边: 从路径推断所属 Component
            comp_name = _infer_component_from_path(path)
            if comp_name:
                graph["edges"].append({
                    "from": comp_name,
                    "to": node_id,
                    "type": "provides",
                })

    # ── Constraint 节点: 从 ASD + TLCD 提取 ──
    asd = state.get("asd", "")
    tlcd = state.get("tlcd", "")
    combined = asd + "\n" + tlcd
    l_constraints = re.findall(r'(L-\d{2,3})[：:]\s*([^\n]+)', combined)
    for cid, desc in l_constraints[:20]:
        graph["nodes"].append({
            "id": cid,
            "type": "Constraint",
            "description": desc.strip()[:200],
            "scope": "arch",
        })
    c_constraints = re.findall(r'(C-(?:ARCH|MOD|CODE)-\d{3})[：:]\s*([^\n]+)', combined)
    for cid, desc in c_constraints[:30]:
        graph["nodes"].append({
            "id": cid,
            "type": "Constraint",
            "description": desc.strip()[:200],
            "scope": "code",
        })

    # ── depends 边: 从 MDS depends_on 和 DTS 提取 ──
    for mod in modules:
        name = mod.get("name", "") if isinstance(mod, dict) else ""
        deps = mod.get("depends_on", []) if isinstance(mod, dict) else []
        for dep in deps:
            graph["edges"].append({"from": name, "to": dep, "type": "depends"})

    # 从 DTS 补充禁止依赖（标记为 forbidden）
    dts = state.get("dts", "")
    if dts:
        try:
            dts_json = extract_json(dts)
        except Exception:
            dts_json = {}
        forbidden = dts_json.get("forbidden_dependencies", []) if isinstance(dts_json, dict) else []
        for fb in forbidden:
            if isinstance(fb, dict):
                graph["edges"].append({
                    "from": fb.get("from", ""),
                    "to": fb.get("to", ""),
                    "type": "forbidden",
                    "reason": fb.get("reason", ""),
                })

    return json.dumps(graph, ensure_ascii=False, indent=2)


def _infer_layer(module_name: str) -> str:
    """从模块名推断所属架构层。"""
    n = module_name.lower()
    if "controller" in n:
        return "presentation"
    if "service" in n:
        return "business"
    if "repository" in n or "dao" in n:
        return "data"
    if "gateway" in n or "client" in n or "config" in n:
        return "infrastructure"
    return "business"


def _infer_component_from_path(path: str) -> str:
    """从 API 路径推断所属 Component。"""
    parts = [p for p in path.split("/") if p]
    if "cells" in parts:
        return "CellService"
    if "orders" in parts:
        return "OrderService"
    if "notifications" in parts:
        return "NotificationService"
    if "users" in parts:
        return "UserService"
    if "couriers" in parts:
        return "CourierService"
    return ""


# ── CodeGraph 逆向图谱提取（教材§12 第三阶段: tree-sitter 的 Python 替代实现）──
def extract_reverse_graph(source_dir: str) -> str:
    """从生成的 Java 源码中提取逆向知识图谱（实现事实图谱）。

    解析 Java 文件，提取:
      - 包结构 + 类定义 (Component 节点)
      - 方法签名 (Interface 节点)
      - import 依赖关系 (depends 边)
      - 注解信息

    返回 JSON 字符串，格式与正向图谱一致，可直接比对。
    """
    import re
    graph = {"nodes": [], "edges": []}
    if not os.path.isdir(source_dir):
        return json.dumps(graph, ensure_ascii=False)

    java_files = []
    for root, _, files in os.walk(source_dir):
        for f in files:
            if f.endswith(".java"):
                java_files.append(os.path.join(root, f))

    for filepath in java_files:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()

        # 提取 package
        pkg_match = re.search(r'package\s+([\w.]+);', code)
        pkg = pkg_match.group(1) if pkg_match else ""

        # 提取类/接口定义
        class_matches = re.findall(
            r'(?:@\w+\s*)*\s*(?:public\s+)?(class|interface|enum)\s+(\w+)',
            code
        )
        for cls_type, cls_name in class_matches:
            node_id = f"{pkg}.{cls_name}" if pkg else cls_name
            graph["nodes"].append({
                "id": node_id,
                "type": "Component",
                "source_type": cls_type,
                "file": os.path.relpath(filepath, source_dir),
            })

            # 提取方法签名 → Interface 代理节点
            method_matches = re.findall(
                r'(?:@\w+\s*)*\s*(?:public|private|protected)\s+(?:static\s+)?(?:<\w+>\s+)?(\w+)\s+(\w+)\s*\(([^)]*)\)',
                code
            )
            for ret_type, method_name, params in method_matches:
                if method_name in ("main", "equals", "hashCode", "toString"):
                    continue
                if_node_id = f"{node_id}.{method_name}"
                graph["nodes"].append({
                    "id": if_node_id,
                    "type": "Interface",
                    "return_type": ret_type,
                    "params": params.strip()[:100],
                })
                graph["edges"].append({
                    "from": node_id,
                    "to": if_node_id,
                    "type": "provides",
                })

        # 提取 import 依赖
        import_matches = re.findall(r'import\s+([\w.]+);', code)
        for imp in import_matches:
            # 仅记录项目内部依赖（com.medical.device.rental 包）
            if "com.medical.device.rental" in imp:
                target_simple = imp.split(".")[-1]
                for node in graph["nodes"]:
                    if node["id"].endswith("." + target_simple) or node["id"] == target_simple:
                        for cm in class_matches:
                            src_id = f"{pkg}.{cm[1]}" if pkg else cm[1]
                            graph["edges"].append({
                                "from": src_id,
                                "to": node["id"],
                                "type": "depends",
                            })

    # 去重边
    seen = set()
    unique_edges = []
    for e in graph["edges"]:
        key = (e["from"], e["to"], e["type"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(e)
    graph["edges"] = unique_edges

    return json.dumps(graph, ensure_ascii=False, indent=2)


def _compare_graphs(forward_json: str, reverse_json: str) -> str:
    """比对正向图谱（设计意图）和逆向图谱（实现事实）。

    教材§14 RCR: 检查逆向图谱中的每一条依赖边是否在正向图谱中有合法对应，
    检查逆向图谱中的每一个接口是否与正向图谱中的接口契约一致。

    Returns:
        差异报告字符串，无差异则返回空字符串。
    """
    try:
        fg = json.loads(forward_json)
        rg = json.loads(reverse_json)
    except Exception:
        return ""

    lines = []

    # ── 1. 组件级比对: 设计定义了但代码中缺失的 Component ──
    fg_comp = {n["id"] for n in fg.get("nodes", []) if n["type"] == "Component"}
    rg_comp = {n["id"] for n in rg.get("nodes", []) if n["type"] == "Component"}
    missing_comp = fg_comp - rg_comp
    extra_comp = rg_comp - fg_comp
    if missing_comp:
        lines.append(f"- **组件缺失**: 设计定义了但代码中未找到: {', '.join(sorted(missing_comp)[:10])}")
    if extra_comp:
        lines.append(f"- **额外组件**: 代码中存在但设计中未定义: {', '.join(sorted(extra_comp)[:10])}")

    # ── 2. 依赖边比对: 逆向图谱的每条 depends 边 ──
    fg_legal_edges = set()
    fg_forbidden_edges = {}  # from→to → reason
    for e in fg.get("edges", []):
        if e.get("type") in ("depends",):
            fg_legal_edges.add((e["from"], e["to"]))
        elif e.get("type") == "forbidden":
            fg_forbidden_edges[(e["from"], e["to"])] = e.get("reason", "")

    rg_depends = [(e["from"], e["to"]) for e in rg.get("edges", []) if e.get("type") == "depends"]

    illegal_deps = []
    for src, tgt in rg_depends:
        if (src, tgt) in fg_forbidden_edges:
            reason = fg_forbidden_edges[(src, tgt)]
            illegal_deps.append(f"  - {src} → {tgt}: 违反禁止依赖 ({reason})")
        elif (src, tgt) not in fg_legal_edges and fg_legal_edges:
            # 不在合法列表中（但有合法列表时才报告）
            illegal_deps.append(f"  - {src} → {tgt}: 依赖未在设计拓扑中声明")
    if illegal_deps:
        lines.append(f"- **非法依赖边** ({len(illegal_deps)} 条):")
        lines.extend(illegal_deps[:15])

    # ── 3. 接口比对: 逆向图谱的 Interface 节点 vs 正向图谱 ──
    fg_if = {n["id"] for n in fg.get("nodes", []) if n["type"] == "Interface"}
    rg_if = {n["id"] for n in rg.get("nodes", []) if n["type"] == "Interface"}
    # 正向图谱的 Interface 来自 OAS 路径（如 IF-POST-/api/v1/cells/allocate）
    # 逆向图谱的 Interface 来自方法签名（如 com.xxx.CellService.allocateCell）
    # 做模糊匹配而非精确匹配
    fg_if_simple = {iid.split("/")[-1].lower() for iid in fg_if}
    rg_if_simple = set()
    for iid in rg_if:
        # 提取方法名部分
        parts = iid.split(".")
        if len(parts) >= 2:
            rg_if_simple.add(parts[-1].lower())
    missing_if = fg_if_simple - rg_if_simple
    if missing_if and len(fg_if_simple) > 0 and len(rg_if_simple) > 0:
        lines.append(f"- **接口完整性**: 契约定义但代码未实现的方法/端点: {', '.join(sorted(missing_if)[:10])}")

    # ── 4. 总体评估 ──
    if not lines:
        return ""
    return "\n".join(lines)


def call_llm_with_memory(task_prompt: str, state: DesignState, system_prompt: str = "", max_tokens: int = 8000, sections: list[str] | None = None) -> str:
    """带项目记忆注入的 LLM 调用 —— 等同于 Reasonix 的自动化约束注入。

    每次调用后自动累积 Stats 数据（教材§11 第四小节 要点三），
    每 10 次调用自动写入磁盘快照。
    """
    full_prompt = inject_project_memory(task_prompt, state, sections)
    result = call_llm(full_prompt, system_prompt=system_prompt, max_tokens=max_tokens)
    # 自动累积 Stats
    ds = state.get("date_str", datetime.now().strftime("%Y%m%d"))
    save_stats_snapshot(ds)
    return result

# ============================================================
# B_入口路由 — 根据 entry_mode 分发到 B1 或 B6
# ============================================================

def b_entry_router(state: DesignState) -> dict:
    """入口路由器，设置初始时间戳并分发。"""
    run_stop_checks()
    mode = state.get("entry_mode", "full_run")
    ds = datetime.now().strftime("%Y%m%d")
    ts = datetime.now().strftime("%H%M")
    fire_phase("入口路由")
    fire_progress(f"[入口] Phase 2 工作流启动, 模式: {mode}")
    return {"date_str": ds, "time_str": ts, "iteration_count": 1}


def b_route_after_entry(state: DesignState) -> Literal["B1", "B6"]:
    mode = state.get("entry_mode", "full_run")
    if mode == "change_mode":
        return "B6"
    return "B1"

# ============================================================
# 占位节点函数（后续步骤实现）
# ============================================================

def b1_architecture_selection(state: DesignState) -> dict:
    """B1: 架构选型 — 五维评估 + ADR-001 + ASD"""
    run_stop_checks()
    fire_phase("B1_架构选型")
    fire_progress("[B1] 开始五维度架构选型评估...")

    ds = state["date_str"]
    srs = state.get("srs_input", "")
    reqs = state.get("consolidated_requirements", "")

    # ── Step 0: SRS 语义提取（架构分析智能体）──
    fire_progress("  [B1] Step 0/3: SRS语义提取（架构分析智能体）...")
    semantic = extract_srs_semantics(srs, reqs)
    fire_progress(f"    [语义模型] 实体 {len(semantic.get('entities',[]))} 个, 功能 {len(semantic.get('functionals',[]))} 条, 规则 {len(semantic.get('rules',[]))} 条")
    fire_result("semantic_model", json.dumps(semantic, ensure_ascii=False, indent=2))

    # ── Step 1: 五维评估 ──
    fire_progress("  [B1] Step 1/3: 五维度架构选型评估...")
    eval_prompt = f"""你正在为「医疗器械租赁管理系统」进行架构选型评估。你的输入是经过评审确认的SRS。

## 系统概述 (来自SRS)
{srs[:8000] if srs else 'SRS未提供，请根据需求清单评估'}

## 需求清单
{reqs[:3000] if reqs else '未提供'}

## 评估要求
请按照五维度评估框架对四种候选架构风格（单体、分层、事件驱动、微服务）进行系统化评估。

**一票否决机制**: 每个维度都可以构成一票否决——如果某一维度的条件极端不适合某种风格（score ≤ 1），则无论其他维度得分如何，该风格必须被排除，并在 analysis 中显式标注「一票否决」及理由。

### 评估步骤
1. 从SRS中提取每个维度的关键信息
2. 对每个维度分别打分（1-5分），并给出文本论证。若某风格 score ≤ 1，标记为「一票否决」并排除
3. 排除被否决的风格后，综合剩余维度的评估结果，给出推荐架构风格

### 输出格式（严格JSON）
{{
  "dimensions": {{
    "functional_complexity": {{"score": 1-5, "analysis": "...", "preference": "单体|分层|事件驱动|微服务"}},
    "concurrency_performance": {{"score": 1-5, "analysis": "...", "preference": "..."}},
    "scalability": {{"score": 1-5, "analysis": "...", "preference": "..."}},
    "team_size": {{"score": 1-5, "analysis": "...", "preference": "..."}},
    "ops_capability": {{"score": 1-5, "analysis": "...", "preference": "..."}}
  }},
  "recommendation": {{
    "primary_style": "分层|事件驱动|微服务|单体",
    "hybrid_notes": "是否需要混合风格，如何混合",
    "key_reasons": ["理由1", "理由2", "理由3"],
    "risks": ["风险1", "风险2"]
  }}
}}

只输出JSON，不要其他文字。"""

    eval_result = call_llm(eval_prompt, system_prompt="你是资深软件架构师，精通架构选型评估。严格按照五维度框架进行系统化分析。", max_tokens=8000)
    eval_json = extract_json(eval_result)
    fire_result("b1_evaluation", json.dumps(eval_json, ensure_ascii=False, indent=2))
    fire_progress("  [B1] 五维评估完成")

    # ── Step 2: 生成 ADR-001 + ASD ──
    fire_progress("  [B1] Step 2/3: 生成 ADR-001 + 架构风格声明(ASD)...")

    recommendation = eval_json.get("recommendation", {})
    primary_style = recommendation.get("primary_style", "分层")
    key_reasons = recommendation.get("key_reasons", [])
    risks = recommendation.get("risks", [])
    dims = eval_json.get("dimensions", {})

    adr_prompt = f"""你是资深软件架构师。基于以下五维评估结果，生成 ADR-001（架构选型决策记录）和 ASD（架构风格声明）。

## 五维评估结果
{json.dumps(eval_json, ensure_ascii=False, indent=2)}

## 系统背景
SRS摘要: {srs[:2000] if srs else '参见Phase 1基线'}

## ADR-001 要求（严格按以下模板输出）
### ADR-001: 架构选型决策记录

**状态**: 已接受
**日期**: {datetime.now().strftime('%Y-%m-%d')}
**决策者**: AI架构师 + 人工评审

**背景**: [系统面临的核心架构挑战]
**决策**: 采用{primary_style}架构风格。{chr(10).join(['- ' + r for r in key_reasons])}

**备选方案**:
- 单体架构: [分析]
- 分层架构: [分析]
- 事件驱动架构: [分析]
- 微服务架构: [分析]

**后果**:
- 正面: [列出3-5条]
- 负面: {chr(10).join(['- ' + r for r in risks])}
- 风险缓解: [列出缓解措施]

**重新审视条件**:
1. [触发条件1，如团队规模变化]
2. [触发条件2，如并发量变化]
3. [触发条件3，如运维能力变化]

## ASD 要求（可检查的编号约束条款）
### ASD: 架构风格声明

1. **架构风格名称**: {primary_style}
2. **核心组织逻辑**: [该风格如何组织代码和通信]
3. **层次/组件定义**: [各层/组件职责]
4. **依赖规则**: [组件间合法依赖关系，区分「调用依赖」和「事件依赖」]
5. **通信机制**: [组件间通信方式，同步/异步]
6. **数据管理策略**: [数据如何分布和访问]
7. **部署拓扑**: [部署结构]
8. **关键技术选型**: [框架/中间件]
9. **约束与禁止事项**: [至少5条编号约束，格式如 L-01: ... L-02: ...]
   每条约束包含：唯一编号、可检查的判断标准、违反后果。
   例如: "L-01：表示层不得直接调用数据访问层。检查标准：controller包不得import repository包。违反后果：层次穿透，破坏变更隔离。"

请输出完整的ADR-001和ASD（Markdown格式）。"""

    adr_and_asd = call_llm(adr_prompt, system_prompt="你是资深软件架构师，精通ADR（架构决策记录）编写。严格按照模板输出完整的ADR-001和ASD。", max_tokens=10000)

    # 分离 ADR-001 和 ASD
    adr_part = ""
    asd_part = ""
    if "### ASD" in adr_and_asd:
        parts = adr_and_asd.split("### ASD", 1)
        adr_part = parts[0].strip()
        asd_part = "### ASD" + parts[1].strip()
    else:
        adr_part = adr_and_asd

    # ── 保存到 KB ──
    adr_path = save_to_kb_design("adr", f"ADR-001-架构选型决策-{ds}.md",
        kb_frontmatter_adr("ADR-001: 架构选型决策", status="已接受") + adr_part)
    asd_path = save_to_kb_design("spec", f"ASD-架构风格声明-{ds}.md",
        kb_frontmatter("ASD: 架构风格声明", ["ASD", "架构风格", "Phase2"]) + asd_part)

    fire_progress(f"[B1] ADR-001 已保存: {adr_path}")
    fire_progress(f"[B1] ASD 已保存: {asd_path}")
    fire_result("adr_001", adr_part)
    fire_result("asd", asd_part)

    return {
        "adr_001": adr_part,
        "asd": asd_part,
        "semantic_model": json.dumps(semantic, ensure_ascii=False),
        "workflow_status": "B1完成",
    }


def b2_engineering_products(state: DesignState) -> dict:
    """B2: 工程产物定义 — MDS + DTS + ADR-002~4"""
    run_stop_checks()
    fire_phase("B2_工程产物")
    fire_progress("[B2] 开始工程产物定义...")

    ds = state["date_str"]
    srs = state.get("srs_input", "")
    adr_001 = state.get("adr_001", "")
    asd = state.get("asd", "")

    # ── Step 1: MDS 模块划分 ──
    fire_progress("  [B2] Step 1/4: 模块划分方案 (MDS)...")
    mds_prompt = f"""你是资深软件架构师。基于以下输入，为「医疗器械租赁管理系统」设计模块划分方案（MDS）。

## 架构决策
{adr_001[:3000] if adr_001 else '参见 ADR-001'}

## 架构风格声明
{asd[:3000] if asd else '参见 ASD'}

## SRS 需求摘要
{srs[:3000] if srs else '参见 Phase 1 基线'}

## 要求
1. 将系统拆分为 6-10 个职责单一的模块
2. **关键质量规则**: 每个模块的 responsibility 描述中**不得出现「和」字**——一个模块只承担一项单一职责。出现「和」意味着两个职责被合并，违反了单一职责原则
3. 每个模块包含：模块名称、职责描述（不含和字）、对外接口、依赖的其他模块、核心实体
4. 模块命名遵循统一术语表（如 OrderService, CellService, NotificationService）

## 输出格式（严格JSON）
{{
  "modules": [
    {{
      "name": "OrderService",
      "responsibility": "负责取件订单的完整生命周期管理——创建、状态流转、取件码验证、超时计费",
      "interfaces": ["createOrder", "updateStatus", "validatePickupCode"],
      "depends_on": ["CellService", "UserService"],
      "domain_entities": ["Order", "PickupCode", "OrderStatus"]
    }}
  ],
  "design_rationale": "划分理由"
}}

只输出JSON。"""

    mds_result = call_llm(mds_prompt, system_prompt="你是资深软件架构师，精通领域驱动设计（DDD）和模块化架构。严格按照要求输出。", max_tokens=8000)
    mds_json = extract_json(mds_result)
    fire_result("mds", json.dumps(mds_json, ensure_ascii=False, indent=2))

    # ── Step 2: DTS 依赖拓扑 ──
    fire_progress("  [B2] Step 2/4: 依赖拓扑 (DTS)...")
    modules = mds_json.get("modules", [])

    dts_prompt = f"""基于以下模块划分，生成模块依赖拓扑规范（DTS）。

## 已定义的模块
{json.dumps(modules, ensure_ascii=False, indent=2)}

## 架构风格
{asd[:2000] if asd else '参见 ASD'}

## 要求
1. 定义每个模块的合法依赖关系，区分两种边:
   - **调用依赖** (sync): 模块A直接调用模块B的API，同步等待返回——被调用方不可用会导致调用方失败
   - **事件依赖** (async): 模块A发布事件，模块B订阅——发布方不感知订阅方存在，弱耦合
2. 明确禁止的依赖关系（循环依赖、跨层穿透等）
3. 依赖规则必须与 ASD 的约束条款一致

## 输出格式（严格JSON）
{{
  "dependency_graph": {{
    "OrderService": [{{"target": "CellService", "type": "sync", "reason": "创建订单时查询可用格口"}}]
  }},
  "forbidden_dependencies": [
    {{"from": "Controller", "to": "Repository", "reason": "违反ASD L-01: 表示层不得直接调用数据访问层"}}
  ],
  "event_flow": [
    {{"publisher": "OrderService", "event": "OrderCreated", "subscribers": ["NotificationService"]}}
  ],
  "topology_validation": "无循环依赖/存在需要注意的依赖"
}}

只输出JSON。"""

    dts_result = call_llm(dts_prompt, system_prompt="你是软件架构师，精通依赖管理和模块化设计。", max_tokens=6000)
    dts_json = extract_json(dts_result)
    fire_result("dts", json.dumps(dts_json, ensure_ascii=False, indent=2))

    # ── Step 3: ADR-002~4 ──
    fire_progress("  [B2] Step 3/4: 生成 ADR-002~4...")
    adr_prompt = f"""基于以下架构信息，生成 ADR-002（技术栈选型）、ADR-003（数据库选型）、ADR-004（部署架构）。
每份 ADR 必须包含完整的 10 个字段。

## 架构选型 (ADR-001)
{adr_001[:2000]}

## 模块划分 (MDS)
{json.dumps(modules, ensure_ascii=False, indent=2)[:3000]}

## 依赖拓扑 (DTS)
{json.dumps(dts_json, ensure_ascii=False, indent=2)[:2000]}

## 每份 ADR 的 10 字段模板
**标题**: ADR-00X: [决策标题]
**状态**: 已接受
**上下文**: [决策面临的约束条件和问题背景]
**决策**: [明确陈述我们决定了什么]
**备选方案**: [列出所有考虑过的替代方案及其优缺点]
**后果**: [做出该决策后，系统将变得更容易做的事和更难做的事]
**重新审视条件**: [在什么条件下该决策应被重新打开讨论]
**关联ADR**: [链接到被此决策影响或影响此决策的其他ADR]
**创建日期**: {datetime.now().strftime('%Y-%m-%d')}
**审批人**: AI架构师 + 人工评审

## 三份 ADR 的内容
- ADR-002: 技术栈选型（如 Spring Boot 3.x + Java 17 vs Django + Python vs FastAPI）
- ADR-003: 数据库选型（如 MySQL vs PostgreSQL vs MongoDB，需说明数据模型适合性）
- ADR-004: 部署架构（如单体部署 vs Docker Compose vs K8s，需与维度五运维能力一致）

输出三份完整的 10 字段 ADR（Markdown 格式）。"""

    adr_002_004 = call_llm(adr_prompt, system_prompt="你是资深软件架构师，精通ADR编写。严格按照模板输出完整的ADR-002、ADR-003、ADR-004。", max_tokens=10000)

    # ── 保存到 KB ──
    mds_doc = f"# 模块划分方案 (MDS)\n\n" + json.dumps(mds_json, ensure_ascii=False, indent=2)
    dts_doc = f"# 依赖拓扑规范 (DTS)\n\n" + json.dumps(dts_json, ensure_ascii=False, indent=2)

    mds_path = save_to_kb_design("spec", f"MDS-模块划分方案-{ds}.md",
        kb_frontmatter("MDS: 模块划分方案", ["MDS", "模块", "Phase2"]) + mds_doc)
    dts_path = save_to_kb_design("spec", f"DTS-依赖拓扑规范-{ds}.md",
        kb_frontmatter("DTS: 依赖拓扑规范", ["DTS", "依赖", "Phase2"]) + dts_doc)
    adr_path = save_to_kb_design("adr", f"ADR-002~4-技术栈数据库部署决策-{ds}.md",
        kb_frontmatter_adr("ADR-002~4: 技术栈/数据库/部署决策", status="已接受", tags=["技术栈"]) + adr_002_004)

    fire_progress(f"[B2] MDS 已保存: {mds_path}")
    fire_progress(f"[B2] DTS 已保存: {dts_path}")
    fire_progress(f"[B2] ADR-002~4 已保存: {adr_path}")
    fire_result("mds_doc", mds_doc)
    fire_result("dts_doc", dts_doc)
    fire_result("adr_002_004", adr_002_004)

    # ── Step 4: 四维度交叉校验 ──
    fire_progress("  [B2] Step 4/4: 四维度质量校验 (完备性/正确性/一致性/有效性)...")
    quality_prompt = f"""你是工程产物质量审计师。对以下四种产物执行四维度交叉校验。

## 产物一: ASD (架构风格声明)
{asd[:3000] if asd else '无'}

## 产物二: MDS (模块划分方案)
{json.dumps(mds_json, ensure_ascii=False, indent=2)[:3000]}

## 产物三: DTS (依赖拓扑)
{json.dumps(dts_json, ensure_ascii=False, indent=2)[:3000]}

## 产物四: ADR-002~4
{adr_002_004[:3000]}

## 四维度校验清单

### 完备性 (是否有遗漏?)
1. SRS中每个功能需求是否在 MDS 中有对应模块承担?
2. MDS 中每个模块的每项职责是否在 DTS 中有合法依赖边支撑?
3. ADR 是否覆盖了所有关键技术决策?

### 正确性 (是否准确?)
1. ASD 约束条款是否使用了精确的、无歧义的语言?
2. MDS 模块职责描述是否真正做到了单一职责(不含「和」字)?
3. DTS 每条边的类型是否正确定义(sync vs async)?

### 一致性 (产物间是否有矛盾?)
1. ASD 分层规则与 DTS 依赖边是否一致?
2. MDS 定义的模块是否在 DTS 中都有对应节点?
3. ADR 决策内容是否与 ASD/MDS/DTS 中的相应内容一致?

### 有效性 (能否驱动AI代码生成?)
1. 每条约束是否可判定(不含「应该」「尽量」「建议」等模糊词)?
2. 四种产物是否在典型代码生成场景中提供了足够约束信息?

## 输出格式（严格JSON）
{{
  "completeness": {{"issues": [{{"desc":"...","severity":"高|中|低"}}], "score": "通过|需修复"}},
  "correctness": {{"issues": [...], "score": "通过|需修复"}},
  "consistency": {{"issues": [...], "score": "通过|需修复"}},
  "effectiveness": {{"issues": [...], "score": "通过|需修复"}},
  "overall": "通过|需修复",
  "summary": "整体评价"
}}

只输出 JSON。"""

    quality_result = call_llm(quality_prompt, system_prompt="你是工程产物质量审计师，严格按四维度框架逐条校验。发现任何问题都如实报告。", max_tokens=6000)
    quality_json = extract_json(quality_result)
    fire_result("b2_quality_check", json.dumps(quality_json, ensure_ascii=False, indent=2))

    # 保存质量报告
    quality_doc = f"# B2 工程产物质量校验报告\n\n" + json.dumps(quality_json, ensure_ascii=False, indent=2)
    save_to_kb_design("reports", f"B2-四维度质量校验-{ds}.md",
        kb_frontmatter("B2: 四维度质量校验", ["质量校验", "Phase2"]) + quality_doc)

    overall = quality_json.get("overall", "需修复")
    fire_progress(f"  [B2] 四维质量校验: {overall}")
    if overall != "通过":
        for dim in ["completeness", "correctness", "consistency", "effectiveness"]:
            issues = quality_json.get(dim, {}).get("issues", [])
            if issues:
                fire_progress(f"  [B2] {dim}: {len(issues)} 个问题")

    return {
        "mds": mds_doc,
        "dts": dts_doc,
        "adr_002_004": adr_002_004,
        "workflow_status": "B2完成",
    }


def b3_constraints_and_contracts(state: DesignState) -> dict:
    """B3: 约束与接口契约 — TLCD + OpenAPI YAML"""
    run_stop_checks()
    fire_phase("B3_约束契约")
    fire_progress("[B3] 开始生成约束与接口契约...")

    ds = state["date_str"]
    asd = state.get("asd", "")
    mds = state.get("mds", "")
    dts = state.get("dts", "")
    adr_all = state.get("adr_001", "") + "\n" + state.get("adr_002_004", "")
    srs = state.get("srs_input", "")

    # ── Step 1: TLCD 三层约束设计 ──
    fire_progress("  [B3] Step 1/2: 三层约束设计 (TLCD)...")
    tlcd_prompt = f"""你是资深软件架构师。基于以下设计产物，生成三层约束设计文档（TLCD）。

## 架构风格声明
{asd[:3000] if asd else '参见 ASD'}

## 模块划分 (MDS)
{mds[:3000] if mds else '参见 MDS'}

## 依赖拓扑 (DTS)
{dts[:3000] if dts else '参见 DTS'}

## 架构决策
{adr_all[:3000]}

## 要求
生成三层约束，每层至少5条约束。**每条约束必须以「禁止」「不得」「不允许」的负面形式书写**——明确告诉AI什么不能做，而非什么应该做。正面描述划定职责下限，负面约束划定职责上限，AI需要的是上限。

### C-ARCH: 架构级约束（全局禁止规则）
- 禁止的跨层依赖（如表示层→数据访问层直接调用）
- 禁止的通信模式（如模块间禁止同步HTTP直调实现异步通知）
- 禁止的部署耦合（如业务模块禁止直接依赖外部中间件）

### C-MOD: 模块级约束（模块禁止规则）
- 模块间禁止的调用链（如 CellService 禁止同步调用 OrderService，必须通过事件协作）
- 模块职责禁区（什么功能禁止在哪个模块实现——如通知发送禁止在 OrderService 中实现）
- 禁止的共享依赖（如多个模块禁止共享同一个数据库表）

### C-CODE: 代码级约束（代码禁止规则）
- 禁止的命名模式（如 Service 层禁止使用 DAO 后缀，禁止使用拼音命名）
- 禁止的异常处理方式（如禁止吞掉异常不记录日志，禁止在循环内捕获异常后继续）
- 禁止的日志行为（如禁止在循环内打印日志，禁止使用 System.out）
- 禁止的类型使用（如金额禁止使用 float/double，日期禁止使用字符串拼接）

格式: C-ARCH-001, C-MOD-001, C-CODE-001 等编号，每条约束包含：ID、标题、禁止内容、违反后果。

输出完整 TLCD 文档（Markdown）。"""

    tlcd = call_llm(tlcd_prompt, system_prompt="你是资深软件架构师，精通架构约束设计和代码规范制定。输出完整、可执行的约束文档。", max_tokens=8000)
    fire_result("tlcd", tlcd)

    # ── Step 2: OpenAPI YAML ──
    fire_progress("  [B3] Step 2/2: 生成 OpenAPI 3.0 YAML 接口契约...")
    oas_prompt = f"""你是资深API设计师。基于以下设计产物，为「医疗器械租赁管理系统」生成完整的 OpenAPI 3.0.3 YAML 规范。

## 模块划分
{mds[:3000] if mds else '参见 MDS'}

## 约束设计摘要
{tlcd[:3000]}

## SRS 功能需求
{srs[:3000] if srs else '参见 SRS'}

## 已知业务模块（医疗器械租赁系统）
系统包含但不限于：设备管理、租赁订单、库存管理、客户管理、财务结算、运维维修、报表统计

## 要求
1. 为每个模块至少定义 2-3 个 REST 端点
2. **字段级精度（强制）**: 每个 schema 的每个字段必须包含:
   - `type` + `format`（如 type: string, format: date-time）
   - `description`（业务语义描述，如"用户的手机号码"，而非技术描述"String类型字段"）
   - `example`（真实的业务示例值，AI 从示例值推断字段格式）
   - `nullable` 标注
   - 必填字段在 `required` 数组中列出
3. **错误码三层语义（强制）**: 每个端点 ≥2 种错误响应，每种错误包含:
   - 第一层: HTTP 状态码（400/401/403/404/409/500）
   - 第二层: 应用错误码（如 INVALID_ORDER、CELL_UNAVAILABLE）
   - 第三层: 调用方处理策略（如"调用方应向用户展示'暂无可用的格口，请稍后重试'"）
4. **向后兼容（强制）**: 在 OpenAPI info 节点中包含:
   - `x-version: "v1"`（契约版本号，每次变更递增）
   - `x-changelog`（变更日志数组，记录每次变更的日期、变更内容、影响范围）
5. 定义所有 schemas（DTO、Entity、Error），每个 schema 的 description 写清楚业务含义
6. 使用统一命名规范（如 /api/v1/equipment）
7. 包含 securitySchemes（Bearer JWT）
8. 输出纯 YAML（不要用 Markdown 代码块包裹，直接输出 YAML）

输出完整的 OpenAPI 3.0 YAML 文档。"""

    openapi_yaml = call_llm(oas_prompt, system_prompt="你是资深API设计师，精通OpenAPI 3.0规范。输出严格符合OAS 3.0.3标准的YAML。", max_tokens=12000)

    # 清理可能的 Markdown 包裹
    if openapi_yaml.strip().startswith("```"):
        openapi_yaml = openapi_yaml.strip().strip("```yaml").strip("```").strip()

    fire_result("openapi_yaml", openapi_yaml[:5000] + ("..." if len(openapi_yaml) > 5000 else ""))

    # ── 保存到 KB ──
    tlcd_path = save_to_kb_design("contracts", f"TLCD-三层约束设计-{ds}.md",
        kb_frontmatter("TLCD: 三层约束设计", ["TLCD", "约束", "Phase2"]) + tlcd)
    oas_path = save_to_kb_design("contracts", f"openapi-{ds}.yaml", openapi_yaml)

    fire_progress(f"[B3] TLCD 已保存: {tlcd_path}")
    fire_progress(f"[B3] OpenAPI YAML 已保存: {oas_path} ({len(openapi_yaml)} 字符)")

    return {
        "tlcd": tlcd,
        "openapi_yaml": openapi_yaml,
        "workflow_status": "B3完成",
    }


def b4_code_generation(state: DesignState) -> dict:
    """B4: AI代码生成 — 分层生成 v1 源码"""
    run_stop_checks()
    fire_phase("B4_代码生成")
    fire_progress("[B4] 开始 AI 分层代码生成...")

    ds = state["date_str"]
    tlcd = state.get("tlcd", "")
    oas = state.get("openapi_yaml", "")
    mds = state.get("mds", "")

    # 代码输出根目录
    project_dir = PROJECT_SRC
    os.makedirs(project_dir, exist_ok=True)

    # 定义生成 Pass 列表
    passes = [
        ("Pass 1/5: 项目骨架 + Entity", "生成项目骨架和实体类", 8000),
        ("Pass 2/5: Repository 数据访问层", "生成 Repository 和 DAO", 10000),
        ("Pass 3/5: Service 业务逻辑层", "生成 Service 层", 12000),
        ("Pass 4/5: Controller REST API 层", "生成 Controller 层", 12000),
        ("Pass 5/5: Config + Exception + 测试", "生成配置、异常处理和测试", 10000),
    ]

    generated_files = []
    all_code = ""       # 累积全部生成代码，供实现记忆注入
    context_summary = ""

    for pass_label, pass_desc, max_tok in passes:
        fire_progress(f"  [B4] {pass_label}...")

        task = f"""生成{pass_desc}的完整 Java 源代码。

## 前面已生成的代码摘要
{context_summary if context_summary else '(这是第一个 Pass)'}

## 要求
1. 代码严格遵循上述架构约束和 OpenAPI 契约
2. Spring Boot 3.x + Java 17, Maven 项目
3. 包结构: com.medical.device.rental
4. 每个文件用 `// FILE: path/to/file.java` 标记
5. 每个文件含完整 import、类定义、注解、方法实现
6. 驼峰命名, 领域驱动命名, RESTful API

输出完整的 Java 源文件。用 `// FILE:` 标记每个文件。"""

        code = call_llm_with_memory(task, state,
            system_prompt="你是资深 Java Spring Boot 开发专家。生成生产级代码，严格遵循上下文中的架构约束。",
            max_tokens=max_tok)

        all_code += code + "\n"

        # 解析并写入文件
        import re
        pattern = r'//\s*FILE:\s*(\S+)\n(.*?)(?=//\s*FILE:|\Z)'
        matches = re.findall(pattern, code, re.DOTALL)
        for filepath, content in matches:
            filepath = filepath.strip()
            full_path = os.path.join(project_dir, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content.strip())
            generated_files.append(full_path)
            fire_progress(f"    [FILE] {filepath}")

        # 更新上下文摘要
        context_summary = f"已生成 {len(generated_files)} 个文件: " + ", ".join(
            [os.path.basename(f) for f in generated_files[-5:]]
        ) + ("..." if len(generated_files) > 5 else "")

    summary = f"共生成 {len(generated_files)} 个源文件到 {project_dir}"
    fire_progress(f"[B4] {summary}")
    fire_result("source_code_summary", summary)
    fire_result("source_code_path", project_dir)

    # 保存代码生成报告到 KB
    report = f"# 代码生成报告\n\n- 生成时间: {datetime.now()}\n- 文件数: {len(generated_files)}\n- 输出目录: {project_dir}\n\n## 文件列表\n"
    for f in generated_files:
        report += f"- `{os.path.relpath(f, project_dir)}`\n"
    save_to_kb_design("reports", f"代码生成报告-{ds}.md",
        kb_frontmatter("代码生成报告", ["代码生成", "Phase2"]) + report)

    # 构建正向知识图谱（设计意图图谱）
    fire_progress("  [B4] 构建正向知识图谱...")
    forward_graph = build_forward_graph(state)
    fg_path = save_to_kb_design("reports", f"正向知识图谱-{ds}.json", forward_graph)
    fire_progress(f"  [B4] 正向图谱已保存: {fg_path}")

    return {
        "source_code_summary": summary,
        "source_code_path": project_dir,
        "generated_code": all_code,
        "forward_graph": forward_graph,
        "workflow_status": "B4完成",
    }


def b5_rcr_check(state: DesignState) -> dict:
    """B5: 逆向校验 — RCR 漂移检测 + 👤人工决策"""
    run_stop_checks()
    fire_phase("B5_逆向校验")
    current_round = state.get("drift_round", 0) + 1
    fire_progress(f"[B5] RCR 逆向校验 Round {current_round}/3...")

    ds = state["date_str"]
    tlcd = state.get("tlcd", "")
    oas = state.get("openapi_yaml", "")
    mds = state.get("mds", "")
    dts = state.get("dts", "")
    source_path = state.get("source_code_path", PROJECT_SRC)

    # 收集已生成代码的摘要
    code_summary = ""
    if os.path.exists(source_path):
        java_files = []
        for root, dirs, files in os.walk(source_path):
            for f in files:
                if f.endswith(".java"):
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            content = fh.read()
                        java_files.append(f"### {os.path.relpath(fpath, source_path)}\n{content[:500]}")
                    except:
                        pass
        code_summary = "\n\n".join(java_files[:20])

    # ── CodeGraph 逆向图谱提取 ──
    fire_progress(f"  [B5] CodeGraph 逆向图谱提取... ({len(java_files) if java_files else 0} 个文件)")
    reverse_graph = extract_reverse_graph(source_path)
    rg_path = save_to_kb_design("reports", f"逆向知识图谱-round{current_round}-{ds}.json", reverse_graph)
    fire_progress(f"  [B5] 逆向图谱已保存: {rg_path}")

    # ── 图谱 diff（正向 vs 逆向）──
    # 教材§14: RCR = 检查逆向图谱每条边是否在正向图谱中有合法对应
    graph_diff_report = _compare_graphs(state.get("forward_graph", "{}"), reverse_graph)
    if graph_diff_report:
        fire_progress(f"  [B5] 图谱 diff: {graph_diff_report}")
        # 将程序化 diff 结果注入 LLM prompt 辅助分析
        graph_diff_context = f"\n\n## 程序化图谱 Diff 结果（辅助参考）\n{graph_diff_report}\n"
    else:
        fire_progress(f"  [B5] 图谱 diff: 正向/逆向一致 ✓")
        graph_diff_context = ""

    # ── RCR 检测 ──
    fire_progress(f"  [B5] 扫描代码 vs 设计约束... ({len(java_files) if java_files else 0} 个文件)")
    rcr_task = f"""你是 CodeGraph 逆向一致性校验 (RCR) 分析师。比对上文中自动注入的「设计规范」和以下「代码实现」，检测 4 类设计漂移。
{graph_diff_context}
## 代码实现
{code_summary[:6000] if code_summary else '(代码未生成或数量不足)'}

## 4 类漂移检测
1. **依赖合规性漂移**: 代码中的依赖是否违反 DTS 约束? 参照程序化 diff 中的「非法依赖边」列表
2. **层次穿透漂移**: 是否存在跨层调用 (Controller→Repository 跳过 Service)?
3. **接口完整性漂移**: 代码接口是否与 OpenAPI 契约一致? 参照程序化 diff 中的接口比对
4. **命名一致性漂移**: 命名是否与 MDS 术语表一致?

## 输出格式（严格JSON）
{{
  "drift_items": [
    {{
      "id": "DRIFT-0001",
      "type": "依赖合规性漂移|层次穿透漂移|接口完整性漂移|命名一致性漂移",
      "severity": "严重|中|轻微",
      "description": "漂移描述",
      "file": "涉及文件路径",
      "violated_rule": "违反的约束编号 (如 C-ARCH-003)",
      "suggested_path": "fix_code|fix_design|mark_temporary",
      "suggested_action": "具体修复建议"
    }}
  ],
  "summary": "整体评估"
}}

只输出 JSON。无漂移则输出空数组。"""

    rcr_result = call_llm_with_memory(rcr_task, state,
        system_prompt="你是 CodeGraph 逆向一致性校验专家。严格按 4 类漂移检测规范对比代码与设计约束。",
        max_tokens=8000)
    rcr_json = extract_json(rcr_result)
    drift_items = rcr_json.get("drift_items", [])
    fire_result("drift_list_raw", json.dumps(rcr_json, ensure_ascii=False, indent=2))

    # 保存 RCR 报告
    rcr_doc = f"# RCR 逆向校验报告 (Round {current_round})\n\n" + json.dumps(rcr_json, ensure_ascii=False, indent=2)
    save_to_kb_design("reports", f"RCR-逆向校验报告-round{current_round}-{ds}.md",
        kb_frontmatter(f"RCR 逆向校验报告 Round {current_round}", ["RCR", "漂移", "Phase2"]) + rcr_doc)

    # 保存设计-实现差距报告（教材§11 第四小节 要点二）
    gap_path = save_design_gap_report(state, ds)
    if gap_path:
        fire_progress(f"[B5] 设计-实现差距报告已保存: {gap_path}")

    if not drift_items:
        fire_progress(f"[B5] 未检测到漂移，通过!")
        return {"drift_list_raw": rcr_result, "drift_items": [], "drift_resolved": True, "drift_round": current_round, "reverse_graph": reverse_graph, "workflow_status": "B5通过"}

    fire_progress(f"[B5] 检测到 {len(drift_items)} 项漂移，等待人工决策...")
    fire_result("b5_drift_items", json.dumps(drift_items, ensure_ascii=False))

    # ── 暂停: 等待人工漂移决策 ──
    _wait_for_b5_decisions(state, drift_items, current_round)

    # 从 B5_DRIFT_RESULT 获取人工决策
    decisions = B5_DRIFT_RESULT.get("decisions", [])
    fire_progress(f"[B5] 收到 {len(decisions)} 条漂移决策")
    fire_result("drift_decisions", json.dumps(decisions, ensure_ascii=False))

    return {
        "drift_list_raw": rcr_result,
        "drift_items": drift_items,
        "drift_decisions": decisions,
        "drift_round": current_round,
        "drift_resolved": False,
        "workflow_status": f"B5完成-发现{len(drift_items)}项漂移",
    }


def _wait_for_b5_decisions(state: DesignState, drift_items: list, round_num: int):
    """B5 暂停: 等待人工逐条决策。GUI 模式用 B5_DRIFT_EVENT, CLI 模式用终端交互。"""
    fire_progress(f"\n{'='*50}")
    fire_progress(f"  🔍 B5 逆向校验 — 发现 {len(drift_items)} 项漂移 (Round {round_num}/3)")
    fire_progress(f"{'='*50}")
    for item in drift_items:
        fire_progress(f"  [{item['id']}] {item['type']} | {item['severity']} | {item.get('file', 'N/A')}")
        fire_progress(f"    描述: {item.get('description', '')[:100]}")
        fire_progress(f"    建议: {item.get('suggested_path', 'fix_code')} — {item.get('suggested_action', '')[:100]}")
        fire_progress("")

    if B5_DRIFT_EVENT is not None:
        # GUI 模式: 暂停等待 SSE 前端提交决策
        fire_progress("[PAUSE] 等待前端 B5 漂移决策...")
        B5_DRIFT_EVENT.wait()
        B5_DRIFT_EVENT.clear()
    else:
        # CLI 模式: 交互式逐条决策
        decisions = []
        for item in drift_items:
            print(f"\n[{item['id']}] {item['type']} ({item['severity']})")
            print(f"  描述: {item.get('description', '')[:200]}")
            print(f"  建议: {item.get('suggested_path', 'fix_code')}")
            print("  选择: [1] fix_code (修代码) [2] fix_design (改设计) [3] mark_temporary (标记临时)")
            choice = input("  输入 1/2/3: ").strip()
            action_map = {"1": "fix_code", "2": "fix_design", "3": "mark_temporary"}
            decisions.append({
                "item_id": item["id"],
                "action": action_map.get(choice, "mark_temporary"),
                "comment": "",
            })
        B5_DRIFT_RESULT["decisions"] = decisions
        fire_progress(f"[CLI] B5 决策完成: {len(decisions)} 条")


def b5_decide_next(state: DesignState) -> Literal["B3", "B4", "continue"]:
    """B5 路由: 根据漂移决策决定下一步"""
    decisions = state.get("drift_decisions", [])
    drift_round = state.get("drift_round", 1)
    if drift_round > 3:
        fire_progress("[B5] 漂移回退已达上限(3轮), 强制通过")
        return "continue"
    has_design_fix = any(d.get("action") == "fix_design" for d in decisions)
    has_code_fix = any(d.get("action") == "fix_code" for d in decisions)
    if has_design_fix:
        return "B3"
    elif has_code_fix:
        return "B4"
    else:
        return "continue"


def bl01_create_baseline(state: DesignState) -> dict:
    """BL01: 设计基线创立 — 冻结 B1-B5 产物"""
    run_stop_checks()
    fire_phase("BL01_设计基线")
    fire_progress("[BL01] 创立设计基线 BL-01...")

    ds = state["date_str"]
    bl_ver = f"BL-{ds}-01"
    bl_dir = os.path.join(KB_DIRS_DESIGN["baselines"], bl_ver)
    os.makedirs(bl_dir, exist_ok=True)

    artifacts = {
        "ADR-001": state.get("adr_001", ""), "ASD": state.get("asd", ""),
        "MDS": state.get("mds", ""), "DTS": state.get("dts", ""),
        "ADR-002~4": state.get("adr_002_004", ""), "TLCD": state.get("tlcd", ""),
        "OpenAPI YAML": state.get("openapi_yaml", ""),
    }

    baseline_report = f"# 设计基线 {bl_ver}\n\n创立时间: {datetime.now()}\n\n"
    for name, content in artifacts.items():
        if content:
            fname = name.replace(" ", "_").replace("~", "-")
            with open(os.path.join(bl_dir, f"{fname}.md"), "w", encoding="utf-8") as f:
                f.write(f"# {name}\n\n{content}")
            baseline_report += f"- ✅ {name}: `{fname}.md` ({len(content)} 字符)\n"

    source_path = state.get("source_code_path", PROJECT_SRC)
    file_count = sum(len([f for f in files if f.endswith(".java")])
                     for root, dirs, files in os.walk(source_path)) if os.path.exists(source_path) else 0
    baseline_report += f"\n## 源代码\n- 路径: {source_path}\n- Java 文件: {file_count}\n"

    save_to_kb_design("baselines", f"{bl_ver}/基线报告.md",
        kb_frontmatter(f"设计基线 {bl_ver}", ["基线", "Phase2", bl_ver]) + baseline_report)

    fire_progress(f"[BL01] 基线 {bl_ver} 创立完成 ({file_count} 源文件, {len(artifacts)} 文档)")
    fire_result("bl_01_version", bl_ver)
    # 强制保存最终 Stats 快照
    save_stats_snapshot(ds, force=True)
    return {"bl_01_version": bl_ver, "workflow_status": "BL01完成"}


def b6_change_closure(state: DesignState) -> dict:
    """B6: 变更闭环 — CR→CIA→子循环→ADR-005→新基线→资产包"""
    run_stop_checks()
    fire_phase("B6_变更闭环")
    change_round = state.get("change_round", 0) + 1
    fire_progress(f"[B6] 变更闭环 Round {change_round}/2...")
    ds, cr = state["date_str"], state.get("cr_document", "")

    if change_round == 1 and cr:
        fire_progress("  [B6] 生成 CIA 变更影响分析...")
        cia = call_llm(
            f"""你是CIA专家。基于CR文档和当前设计产物，执行9维度变更影响分析。

## CR（变更请求）
{cr[:4000]}

## 当前设计产物
### MDS
{state.get('mds','')[:2000]}
### TLCD
{state.get('tlcd','')[:2000]}

## 9维度评估（逐项标注受影响程度：无/轻微/中等/严重）
1. **需求层面**: SRS中哪些需求条目受影响? 是否需更新RTM?
2. **架构层面**: 是否影响ASD架构约束或分层规则?
3. **模块层面**: 涉及哪些模块的修改? 模块间依赖是否变化?
4. **契约层面**: OpenAPI接口的请求体/响应体/错误码是否变化?
5. **代码层面**: 需要修改哪些源文件? 新增哪些文件?
6. **数据层面**: 数据库Schema/索引/缓存策略是否需要变更?
7. **兼容性层面**: 旧版本客户端是否仍能正常工作? 是否需要API版本升级?
8. **测试层面**: 哪些已有测试用例会受影响? 需新增哪些测试?
9. **部署运维层面**: CI/CD配置/监控告警/部署顺序是否需要调整?

## 输出格式
生成完整的CIA报告，包含:
- 9维度评估表（每维度: 受影响程度 + 具体说明）
- 变更实施建议（推荐的实施顺序和风险缓解措施）""",
            system_prompt="你是变更影响分析专家。系统化评估变更影响。", max_tokens=8000)
        fire_result("cia", cia)
        save_to_kb_design("reports", f"CIA-变更影响分析-{ds}.md",
            kb_frontmatter("CIA: 变更影响分析", ["CIA", "变更", "Phase2"]) + cia)
        return {"cia": cia, "change_round": change_round, "workflow_status": "B6-CIA完成"}

    fire_progress("  [B6] 生成 ADR-005 + BL-02 + 5层资产包...")
    final_doc = call_llm(
        f"基于CIA结果生成ADR-005变更决策+5层设计资产包摘要。\n## CIA\n{state.get('cia','')[:4000]}\n## CR\n{cr[:2000]}",
        system_prompt="你是资深架构师，精通ADR-005和资产打包。", max_tokens=10000)
    adr_005 = final_doc[:5000] if len(final_doc) > 5000 else final_doc
    asset_pack = final_doc[5000:] if len(final_doc) > 5000 else ""

    bl_ver = f"BL-{ds}-02"
    bl_dir = os.path.join(KB_DIRS_DESIGN["baselines"], bl_ver)
    os.makedirs(bl_dir, exist_ok=True)
    save_to_kb_design("baselines", f"{bl_ver}/ADR-005-变更决策.md",
        kb_frontmatter_adr("ADR-005: 变更决策", status="已接受", replaces="ADR-001", tags=["变更", bl_ver]) + adr_005)
    save_to_kb_design("baselines", f"{bl_ver}/设计资产包.md",
        kb_frontmatter("5层设计资产包", ["资产", bl_ver]) + asset_pack)

    # ── 生成需求差异文档（BL-01 → BL-02）──
    bl_01_ver = state.get("bl_01_version", f"BL-{ds}-01")
    diff_doc = f"""---
title: 需求差异-BL01-BL02
tags: [差异, 基线, 变更]
---

# 需求差异比对: {bl_01_ver} → {bl_ver}

## CR 变更来源
{cr[:2000] if cr else '参见 CR 文档'}

## CIA 影响摘要
{state.get('cia', '')[:2000]}

## 设计变更
- **ADR-005**: {adr_005[:500] if adr_005 else '参见 ADR-005'}
- **契约更新**: 接口变更见 CIA 第4维度
- **依赖拓扑更新**: 模块依赖变更见 CIA 第3维度

## 基线对比
| 维度 | BL-01 | BL-02 | 变更类型 |
|------|-------|-------|---------|
| 架构风格 | 未变更 | 同 BL-01 | 无变更 |
| 模块划分 | 未变更 | 同 BL-01 | 无变更 |
| 接口契约 | v1 | v2 | 修改 |
| 代码实现 | v1 | v2 | 修改 |
| 设计约束 | TLCD v1 | TLCD v2 | 修改 |
"""
    save_to_kb_design("baselines", f"{bl_ver}/DIFF_BL01-BL02_需求差异比对.md", diff_doc)

    fire_progress(f"[B6] ADR-005 + BL-02 {bl_ver} + DIFF 完成")
    fire_result("adr_005", adr_005); fire_result("asset_pack", asset_pack); fire_result("bl_02_version", bl_ver)
    # 强制保存最终 Stats 快照 + 设计-实现差距报告
    save_stats_snapshot(ds, force=True)
    save_design_gap_report(state, ds)
    return {"adr_005": adr_005, "asset_pack": asset_pack, "bl_02_version": bl_ver, "change_round": change_round, "workflow_status": "变更闭环完成"}


def b6_decide_next(state: DesignState) -> Literal["B3", "CCB"]:
    """B6 路由: 继续子循环还是进入 CCB"""
    change_round = state.get("change_round", 1)
    cr = state.get("cr_document", "")
    if cr and change_round <= 2:
        return "B3"
    return "CCB"


def ccb_design_review(state: DesignState) -> dict:
    """CCB: 变更审批 — 👤人工审批"""
    run_stop_checks()
    fire_phase("CCB_变更审批")
    fire_progress("[CCB] 等待人工审批...")
    bl_ver = state.get("bl_02_version", "BL-XX-02")

    fire_progress(f"\n{'='*50}\n  🔍 CCB 变更审批 — {bl_ver}\n{'='*50}")
    if CCB2_EVENT is not None:
        fire_progress("[PAUSE] 等待前端 CCB 审批...")
        CCB2_EVENT.wait(); CCB2_EVENT.clear()
        verdict = CCB2_RESULT.get("verdict", "通过"); comment = CCB2_RESULT.get("comment", "")
    else:
        print(f"\n待审批: {bl_ver}\n[1] 通过 [2] 不通过(获取类) [3] 不通过(分析类)")
        ver_map = {"1":"通过","2":"不通过(获取类)","3":"不通过(分析类)"}
        verdict = ver_map.get(input("输入: ").strip(), "通过")
        comment = input("审批意见: ").strip()

    fire_progress(f"[CCB] 审批: {verdict}")
    fire_result("ccb_verdict", verdict)
    return {"ccb_verdict": verdict, "ccb_comment": comment, "workflow_status": f"审批: {verdict}"}


def ccb_decide_next(state: DesignState) -> Literal["end"]:
    return "end"

# ============================================================
# 🏗 build_design_workflow — 构建 Phase 2 状态图
# ============================================================

def build_design_workflow(entry_mode: str = "full_run") -> StateGraph:
    """构建并返回 Phase 2 设计工作流 StateGraph。"""
    workflow = StateGraph(DesignState)

    # 注册所有节点
    workflow.add_node("B_入口路由", b_entry_router)
    workflow.add_node("B1_架构选型", b1_architecture_selection)
    workflow.add_node("B2_工程产物", b2_engineering_products)
    workflow.add_node("B3_约束契约", b3_constraints_and_contracts)
    workflow.add_node("B4_代码生成", b4_code_generation)
    workflow.add_node("B5_逆向校验", b5_rcr_check)
    workflow.add_node("BL01_设计基线", bl01_create_baseline)
    workflow.add_node("B6_变更闭环", b6_change_closure)
    workflow.add_node("CCB_变更审批", ccb_design_review)

    # 入口路由
    workflow.set_entry_point("B_入口路由")
    workflow.add_conditional_edges(
        "B_入口路由", b_route_after_entry,
        {"B1": "B1_架构选型", "B6": "B6_变更闭环"},
    )

    # 完整运行链路: B1 → B2 → B3 → B4 → B5
    workflow.add_edge("B1_架构选型", "B2_工程产物")
    workflow.add_edge("B2_工程产物", "B3_约束契约")
    workflow.add_edge("B3_约束契约", "B4_代码生成")
    workflow.add_edge("B4_代码生成", "B5_逆向校验")

    # B5 条件边: 漂移回退 或 继续
    workflow.add_conditional_edges(
        "B5_逆向校验", b5_decide_next,
        {"B3": "B3_约束契约", "B4": "B4_代码生成", "continue": "BL01_设计基线"},
    )

    # BL01 → B6
    workflow.add_edge("BL01_设计基线", "B6_变更闭环")

    # B6 条件边: 子循环回 B3 或 进入 CCB
    workflow.add_conditional_edges(
        "B6_变更闭环", b6_decide_next,
        {"B3": "B3_约束契约", "CCB": "CCB_变更审批"},
    )

    # CCB 条件边
    workflow.add_conditional_edges(
        "CCB_变更审批", ccb_decide_next,
        {"end": END},
    )

    return workflow

# ============================================================
# 🚀 main — CLI 入口
# ============================================================

def main():
    entry_mode = "full_run"
    if len(sys.argv) > 1 and sys.argv[1] == "change":
        entry_mode = "change_mode"

    print(f"\n{'='*60}")
    print(f"  第二阶段 — 软件设计与体系结构工作流")
    print(f"  模式: {entry_mode}")
    print(f"{'='*60}\n")

    graph = build_design_workflow(entry_mode)
    app = graph.compile(checkpointer=MemorySaver())

    # 加载 Phase 1 基线产物
    bl_dirs = sorted([
        d for d in os.listdir(os.path.join(KB_ROOT, "wiki", "baselines"))
        if d.startswith("BL-")
    ], reverse=True)

    srs_text = ""
    rtm_text = ""
    req_text = ""

    if bl_dirs:
        latest_bl = os.path.join(KB_ROOT, "wiki", "baselines", bl_dirs[0])
        srs_path = os.path.join(latest_bl, "SRS-正式版.md")
        rtm_path = os.path.join(latest_bl, [
            f for f in os.listdir(latest_bl) if f.startswith("RTM_")
        ][0] if any(f.startswith("RTM_") for f in os.listdir(latest_bl)) else "RTM.md")

        if os.path.exists(srs_path):
            with open(srs_path, "r", encoding="utf-8") as f:
                srs_text = f.read()
            print(f"[OK] 加载 SRS: {srs_path} ({len(srs_text)} 字符)")

        if os.path.exists(rtm_path):
            with open(rtm_path, "r", encoding="utf-8") as f:
                rtm_text = f.read()
            print(f"[OK] 加载 RTM: {rtm_path} ({len(rtm_text)} 字符)")

        req_path = os.path.join(latest_bl, "需求清单.md")
        if os.path.exists(req_path):
            with open(req_path, "r", encoding="utf-8") as f:
                req_text = f.read()

    cr_text = ""
    if entry_mode == "change_mode":
        cr_path = os.path.join(KB_ROOT, "wiki", "design", "reports", "CR.md")
        if os.path.exists(cr_path):
            with open(cr_path, "r", encoding="utf-8") as f:
                cr_text = f.read()
            print(f"[OK] 加载 CR: {cr_path} ({len(cr_text)} 字符)")
        else:
            print("[WARN] 变更模式需要 CR.md，请先创建 wiki/design/reports/CR.md")

    initial_state: DesignState = {
        "srs_input": srs_text,
        "rtm_input": rtm_text,
        "consolidated_requirements": req_text,
        "semantic_model": "",
        "entry_mode": entry_mode,
        "date_str": datetime.now().strftime("%Y%m%d"),
        "time_str": datetime.now().strftime("%H%M"),
        "adr_001": "",
        "asd": "",
        "mds": "",
        "dts": "",
        "adr_002_004": "",
        "tlcd": "",
        "openapi_yaml": "",
        "source_code_summary": "",
        "source_code_path": "",
        "generated_code": "",
        "forward_graph": "",
        "reverse_graph": "",
        "drift_list_raw": "",
        "drift_items": [],
        "drift_decisions": [],
        "drift_round": 0,
        "drift_resolved": False,
        "cr_document": cr_text,
        "cia": "",
        "adr_005": "",
        "asset_pack": "",
        "change_round": 0,
        "bl_01_version": "",
        "bl_02_version": "",
        "ccb_verdict": "",
        "ccb_comment": "",
        "iteration_count": 0,
        "workflow_status": "启动",
    }

    print("\n[开始] 执行工作流...\n")
    config = {"configurable": {"thread_id": f"design-{entry_mode}"}}
    try:
        result = app.invoke(initial_state, config, recursion_limit=100)
        print(f"\n{'='*60}")
        print(f"  工作流执行完成!")
        print(f"  状态: {result.get('workflow_status', '未知')}")
        print(f"  基线: {result.get('bl_02_version', result.get('bl_01_version', 'N/A'))}")
        print(f"{'='*60}")
    except Exception as e:
        print(f"\n[ERROR] 工作流执行失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
