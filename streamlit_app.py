"""医疗器械租赁管理系统 — 逐条实时对话工作流界面

核心设计：每 tick() 只执行一步（一个问题），立即 rerun 刷新界面
所以用户能实时看到每条 AI 提问和涉众回答
"""

import os
import sys
import json
import builtins
from datetime import datetime
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".claude", "workflows"))

from requirement_workflow import (
    call_llm,
    call_stakeholder,
    save_to_kb as _orig_save_kb,
    extract_json,
    kb_frontmatter,
    STAKEHOLDER_CONFIG,
    get_agent_definitions,
    ensure_agent_configs,
    DEFECT_SCENARIOS,
)
from concurrent.futures import ThreadPoolExecutor, as_completed

_original_print = builtins.print
def _safe_print(*args, **kwargs):
    # 合并所有参数为字符串，用 GBK 编码替换掉 emoji 等非法字符
    text = " ".join(str(a) for a in args)
    text = text.encode("gbk", errors="replace").decode("gbk", errors="replace")
    kwargs.setdefault("flush", True)
    _original_print(text, **kwargs)
builtins.print = _safe_print

def save_to_kb(subdir, filename, content):
    return _orig_save_kb(subdir, filename, content)


# ============================================================
# 💾 断点续跑：自动保存 / 恢复 / 清理
# ============================================================

CHECKPOINT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".claude", "workflow_checkpoint.json")


def save_checkpoint():
    """将当前工作流状态保存到本地文件，实现断点续跑"""
    try:
        serializable = {}
        for k, v in st.session_state.items():
            try:
                json.dumps(v, ensure_ascii=False)
                serializable[k] = v
            except (TypeError, ValueError):
                serializable[k] = str(v)
        os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _original_print(f"[checkpoint] 保存失败: {e}")


def load_checkpoint():
    """从本地文件恢复工作流状态"""
    try:
        if not os.path.exists(CHECKPOINT_PATH):
            return None
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _original_print(f"[checkpoint] 读取失败: {e}")
        return None


def clear_checkpoint():
    """删除工作流断点文件"""
    try:
        if os.path.exists(CHECKPOINT_PATH):
            os.remove(CHECKPOINT_PATH)
    except Exception as e:
        _original_print(f"[checkpoint] 删除失败: {e}")


STAKEHOLDERS = list(STAKEHOLDER_CONFIG.keys())
TOTAL_A1_STEPS = 0  # AI自主提问，不预设步数

# ============================================================
# 状态机阶段定义
# ============================================================
# idle
# a1_q:<stakeholder_idx>:<q_idx>  — 单个问题
# a1_save:<stakeholder>            — 保存知识库
# consolidate                      — A1汇总
# a2:<round>                       — A2单轮分析
# a2_followup:<stakeholder>:<q_idx> — A2回退追问
# a2_reconsolidate                 — 重新汇总
# a3                               — UML
# a4                               — SRS
# a5                               — 验证
# a5_defects                       — 缺陷报告
# ccb                              — CCB
# a6                               — 基线
# done                             — 完成


def init_state():
    defaults = {
        "wf_phase": "idle",
        "step_logs": [],
        "dialogs": {s: [] for s in STAKEHOLDERS},
        "consolidated": "",
        "quality_issues": [],
        "a2_round": 1,
        "max_rounds": 3,
        "uml_use_case": "",
        "uml_activities": "",
        "srs": "",
        "validation_report": "",
        "validation_verdict": "",
        "defect_reports": "",
        "baseline_version": "",
        "rtm": "",
        "date_str": datetime.now().strftime("%Y%m%d"),
        "time_str": datetime.now().strftime("%H%M"),
        # 用于追踪 A2 是否通过了（避免 rerun 循环）
        "a2_passed": False,
        "a2_force_continue": False,
        # AI 自主提问相关
        "agent_done": {s: False for s in STAKEHOLDERS},  # 每个 Agent 是否认为问够了
        "agent_pending_questions": {},  # {stakeholder: "生成的问题"}
        "a1_max_rounds": 5,  # 最大对话轮数
        "a1_current_round": 0,
        # 断点续跑标志
        "checkpoint_loaded": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def add_log(msg: str):
    st.session_state.step_logs.append((datetime.now().strftime("%H:%M:%S"), msg))


def tick():
    """每次 Streamlit rerun 调用一次，执行当前阶段的一小步"""
    phase = st.session_state.wf_phase
    if phase == "idle" or phase == "done":
        return

    # ── A1: AI 自主提问（每轮 4 个涉众并行） ──
    if phase == "a1_think":
        # 每个涉众 Agent 根据已有对话历史，自动生成下一个问题
        add_log("AI 正在思考下一个问题...")
        tasks = []
        agent_done_snapshot = dict(st.session_state.agent_done)  # 快照，传进线程
        for s in STAKEHOLDERS:
            if agent_done_snapshot[s]:
                continue
            dialog = st.session_state.dialogs[s]
            dialog_history = ""
            for i, m in enumerate(dialog):
                dialog_history += f"问{i+1}: {m['q']}\n答{i+1}: {m['a']}\n\n"
            prompt = f"""你是一名资深的软件需求分析工程师，正在访谈医疗设备租赁系统的「{s}」角色。

你已经问了以下问题，得到了这些回答：

{dialog_history}

请根据已有对话，判断你是否已经充分了解该角色的需求。
- 如果已经了解清楚（覆盖了工作流程、痛点、异常场景、核心需求），输出：{{"done": true}}
- 如果还需要追问，生成下一个自然、具体的问题，要求：
  1. 不要问已经问过的话题
  2. 追问之前回答中模糊的细节（「很多」「大概」「经常」等模糊表述）
  3. 探索异常情况（「如果…出错了怎么办」）
  4. 每个问题只聚焦一个方面
  输出：{{"done": false, "question": "你的追问问题"}}"""
            tasks.append((s, prompt))

        def think(args):
            s, p = args
            # 不访问 st.session_state，数据通过参数传入
            r = call_llm(p, system_prompt="你是专业的需求分析专家，擅长通过对话获取详细需求。")
            data = extract_json(r)
            done = data.get("done", False) if r else False
            q = data.get("question", "") if r else ""
            return s, done, q

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(think, tasks))
        for s, done, q in results:
            st.session_state.agent_done[s] = done
            if not done and q:
                st.session_state.agent_pending_questions[s] = q
                add_log(f"[{s}] 生成追问: {q[:30]}...")
            else:
                st.session_state.agent_pending_questions[s] = ""
                add_log(f"[{s}] 已了解充分")

        st.session_state.wf_phase = "a1_ask"
        st.rerun()
        return

    if phase == "a1_ask":
        # 并行向所有尚有问题的涉众提问
        tasks = []
        for s in STAKEHOLDERS:
            q = st.session_state.agent_pending_questions.get(s, "")
            if q and not st.session_state.agent_done[s]:
                tasks.append((s, q))

        if not tasks:
            # 所有人都问完了
            st.session_state.wf_phase = "a1_save:0"
            st.rerun()
            return

        def ask_sth(args):
            s, q = args
            return s, q, call_stakeholder(s, q)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(ask_sth, tasks))
        for s, q, a in results:
            st.session_state.dialogs[s].append({"q": q, "a": a})
            add_log(f"[{s}] 回答完成")

        # 检查轮次上限
        st.session_state.a1_current_round += 1
        if st.session_state.a1_current_round >= st.session_state.a1_max_rounds:
            add_log("已达到最大对话轮数，结束 A1")
            st.session_state.wf_phase = "a1_save:0"
        else:
            st.session_state.wf_phase = "a1_think"  # 继续思考下一轮问题
        st.rerun()
        return

    # ── A1: 保存到知识库（逐涉众） ──
    if phase.startswith("a1_save:"):
        idx = int(phase.split(":")[1])
        stakeholder = STAKEHOLDERS[idx]
        dialog = st.session_state.dialogs[stakeholder]
        lines = [f"# {stakeholder} — 需求获取记录\n", f"日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
        for i, item in enumerate(dialog, 1):
            lines.append(f"## 第{i}问\n**问：** {item['q']}\n\n**答：** {item['a']}\n")
        content = kb_frontmatter(f"{stakeholder}需求记录", ["涉众对话", stakeholder], [stakeholder]) + "".join(lines)
        save_to_kb("raw_notes", f"{st.session_state.date_str}-{st.session_state.time_str}-{stakeholder}-需求记录.md", content)
        add_log(f"已保存 {stakeholder} 对话记录")
        if idx + 1 < len(STAKEHOLDERS):
            st.session_state.wf_phase = f"a1_save:{idx+1}"
        else:
            st.session_state.wf_phase = "consolidate"
        st.rerun()
        return

    # ── A1 汇总 ──
    if phase == "consolidate":
        add_log("正在汇总需求清单...")
        all_dialogs = {s: st.session_state.dialogs[s] for s in STAKEHOLDERS}
        prompt = f"""你作为需求分析员，将以下四个涉众的对话记录整理为结构化的需求清单。

对话记录：
{json.dumps(all_dialogs, ensure_ascii=False, indent=2)}

请按以下格式输出：
1. 按功能模块分类（用户认证、设备管理、客户管理、租赁订单、费用结算、数据统计、系统配置）
2. 每条需求格式：REQ-{{模块缩写}}-{{编号}} | 涉众来源 | 需求描述 | 优先级
3. 标注每条需求的边界条件（如数量范围、时间周期、金额限制）"""
        result = call_llm(prompt, system_prompt="你是一名资深需求分析工程师，擅长将涉众对话整理为结构化需求。")
        st.session_state.consolidated = result
        content = kb_frontmatter(f"需求清单-{st.session_state.date_str}", ["需求", "汇总"], ["需求清单"]) + result
        save_to_kb("wiki", f"需求清单-{st.session_state.date_str}-v1.0.md", content)
        add_log("需求清单汇总完成")
        st.session_state.wf_phase = "a2"
        st.rerun()
        return

    # ── A2: 单轮分析 ──
    if phase == "a2":
        current = st.session_state.a2_round
        add_log(f"A2 第 {current} 轮分析...")
        prompt = f"""对以下需求清单进行四维度质量检测（第{current}轮/最多{st.session_state.max_rounds}轮）：

{st.session_state.consolidated}

检测维度：
1. **模糊** - 是否包含「尽量」「大概」「合理」「快速」等不可量化词语
2. **不一致** - 同一术语在不同地方是否有不同定义
3. **矛盾** - 两条需求是否在逻辑上无法同时成立
4. **冲突** - 不同涉众的互斥期望

输出严格JSON格式：
{{"issues": [{{"type": "模糊/不一致/矛盾/冲突", "severity": "严重/中/低", "description": "问题描述", "source": "涉及涉众", "suggestion": "修正建议"}}], "has_critical": true/false}}"""
        r = call_llm(prompt, system_prompt="你是一名严格的需求质量审查专家。请精确输出JSON。")
        data = extract_json(r)
        issues = data.get("issues", [])
        has_critical = data.get("has_critical", False)
        st.session_state.quality_issues = issues

        # 保存问题清单
        issue_rows = "\n".join(
            f"| {i.get('type','')} | {i.get('severity','')} | {i.get('description','')} | {i.get('source','')} | {i.get('suggestion','')} |"
            for i in issues
        ) if issues else "| — | — | 未发现严重问题 | — | — |"
        issues_content = kb_frontmatter(f"需求问题清单-{st.session_state.date_str}", ["问题", "质量分析"], ["需求问题清单"]) + f"""# 需求问题清单
生成日期：{st.session_state.date_str}
分析轮次：第{current}轮

| 类型 | 严重程度 | 问题描述 | 涉及涉众 | 修正建议 |
|-----|---------|---------|---------|---------|
{issue_rows}
"""
        save_to_kb("wiki", f"需求问题清单-{st.session_state.date_str}-v1.0.md", issues_content)

        if has_critical and current < st.session_state.max_rounds:
            add_log("发现严重问题，自动追问中...")
            # 找第一个有相关问题的涉众开始追问
            st.session_state.a2_passed = False
            st.session_state.wf_phase = "a2_followup:0"
        elif has_critical:
            add_log("已达最大分析轮数，强制继续")
            st.session_state.a2_force_continue = True
            st.session_state.wf_phase = "a3"
        else:
            add_log("需求质量分析通过")
            st.session_state.a2_passed = True
            st.session_state.wf_phase = "a3"
        st.rerun()
        return

    # ── A2 回退追问（逐涉众） ──
    if phase.startswith("a2_followup:"):
        idx = int(phase.split(":")[1])
        if idx >= len(STAKEHOLDERS):
            # 所有涉众追问完毕，重新汇总
            st.session_state.wf_phase = "a2_reconsolidate"
            st.rerun()
            return
        stakeholder = STAKEHOLDERS[idx]
        issues = st.session_state.quality_issues
        p = f"""你是一名涉众对话Agent，角色是「{stakeholder}」。

已有对话记录：
{json.dumps(st.session_state.dialogs[stakeholder], ensure_ascii=False, indent=2)}

需求质量分析发现以下问题：
{json.dumps(issues, ensure_ascii=False, indent=2)}

请判断：
1. 哪些问题与"{stakeholder}"这个角色的工作直接相关？
2. 对每个相关问题，生成1句自然语言的追问问题

输出严格JSON格式：
{{"relevant": true/false, "follow_ups": ["追问句1", "追问句2"]}}"""
        rr = call_llm(p, system_prompt="你是专业的涉众需求分析师，擅长根据问题生成自然追问。")
        d = extract_json(rr)
        if d.get("relevant") and d.get("follow_ups"):
            # 先记下追问列表，逐条执行
            st.session_state["pending_followups"] = d["follow_ups"]
            st.session_state["followup_stakeholder"] = stakeholder
            st.session_state["followup_q_idx"] = 0
            st.session_state.wf_phase = "a2_followup_q"
        else:
            st.session_state.wf_phase = f"a2_followup:{idx+1}"
        st.rerun()
        return

    # ── A2 回退追问：逐条提问 ──
    if phase == "a2_followup_q":
        stakeholder = st.session_state["followup_stakeholder"]
        qs = st.session_state["pending_followups"]
        qi = st.session_state["followup_q_idx"]
        if qi >= len(qs):
            st.session_state.wf_phase = f"a2_followup:{STAKEHOLDERS.index(stakeholder)+1}"
            st.rerun()
            return
        q = qs[qi]
        answer = call_stakeholder(stakeholder, q)
        st.session_state.dialogs[stakeholder].append({"q": q, "a": answer})
        add_log(f"[{stakeholder}] 追问: {q[:35]}...")
        st.session_state["followup_q_idx"] = qi + 1
        st.rerun()
        return

    # ── A2 重新汇总 ──
    if phase == "a2_reconsolidate":
        add_log("重新汇总需求清单...")
        all_dialogs = {s: st.session_state.dialogs[s] for s in STAKEHOLDERS}
        p = f"""你作为需求分析员，将以下四个涉众的对话记录整理为结构化的需求清单。
对话记录：
{json.dumps(all_dialogs, ensure_ascii=False, indent=2)}
请按以下格式输出：
1. 按功能模块分类
2. 每条需求格式：REQ-{{模块缩写}}-{{编号}} | 涉众来源 | 需求描述 | 优先级
3. 标注每条需求的边界条件"""
        st.session_state.consolidated = call_llm(p, system_prompt="你是一名资深需求分析工程师。")
        save_to_kb("wiki", f"需求清单-{st.session_state.date_str}-v2.0.md",
            kb_frontmatter(f"需求清单-{st.session_state.date_str}", ["需求", "汇总"], ["需求清单"]) + st.session_state.consolidated)
        st.session_state.a2_round += 1
        st.session_state.wf_phase = "a2"
        st.rerun()
        return

    # ── A3: UML ──
    if phase == "a3":
        add_log("正在生成 UML 模型...")
        prompt = f"""根据以下需求清单，生成UML模型（PlantUML代码）：
需求清单：
{st.session_state.consolidated[:8000]}
请生成：
1. **用例图**（@startuml ... @enduml）- Actor：4种角色，Use Case：所有系统功能
2. **活动图**（至少3个核心流程）：租赁订单流程、设备入库流程、设备维修流程"""
        result = call_llm(prompt, system_prompt="你是UML建模专家，精通PlantUML语法。")
        parts = result.split("@startuml")
        st.session_state.uml_use_case = "@startuml" + parts[1] if len(parts) > 1 else result
        st.session_state.uml_activities = "@startuml" + "@startuml".join(parts[2:]) if len(parts) > 2 else ""
        if st.session_state.uml_use_case:
            save_to_kb("wiki", f"用例图-{st.session_state.date_str}-v1.0.puml", st.session_state.uml_use_case)
        if st.session_state.uml_activities:
            save_to_kb("wiki", f"活动图-{st.session_state.date_str}-v1.0.puml", st.session_state.uml_activities)
        add_log("UML 模型生成完成")
        st.session_state.wf_phase = "a4"
        st.rerun()
        return

    # ── A4: SRS ──
    if phase == "a4":
        add_log("正在生成 SRS 文档...")
        prompt = f"""请根据以下输入生成一份完整的SRS（软件需求规格说明书），遵循IEEE 830标准。
需求清单：
{st.session_state.consolidated[:6000]}
UML模型：
{st.session_state.uml_use_case[:2000]}
{st.session_state.uml_activities[:2000]}
要求：
1. 严格遵循IEEE 830结构
2. 具体需求按7个模块分节
3. 每条功能需求包含：编号+描述+输入/输出+验收标准
4. **禁止使用模糊词**
5. 总字数不少于10000字
6. 包含数据字典"""
        st.session_state.srs = call_llm(prompt, system_prompt="你是专业的软件需求文档编写专家，精通IEEE 830标准。")
        srs_content = kb_frontmatter("SRS-正式版", ["SRS", "需求规格"], ["软件需求规格说明书"]) + st.session_state.srs
        save_to_kb("wiki", f"SRS-初稿-{st.session_state.date_str}-v1.0.md", srs_content)
        add_log(f"SRS 文档生成完成（{len(st.session_state.srs)} 字）")
        st.session_state.wf_phase = "a5"
        st.rerun()
        return

    # ── A5: 验证 ──
    if phase == "a5":
        add_log("正在执行交叉验证...")
        prompt = f"""对以下SRS文档进行交叉验证。
SRS文档：
{st.session_state.srs[:10000]}
验证方法：
1. 历史需求比对
2. 涉众对话比对
3. 项目文档比对
4. SRS内部一致性比对
输出严格JSON格式：
{{"verdict": "通过/获取类问题/分析类问题", "findings": [{{"type": "...", "severity": "严重/中/低", "section": "...", "description": "...", "suggestion": "..."}}]}}"""
        st.session_state.validation_report = call_llm(prompt, system_prompt="你是严谨的需求验证审计师。请精确输出JSON。")
        data = extract_json(st.session_state.validation_report)
        st.session_state.validation_verdict = data.get("verdict", "分析类问题")
        findings = data.get("findings", [])
        finding_rows = "\n".join(
            f"| {f.get('type','')} | {f.get('severity','')} | {f.get('section','—')} | {f.get('description','')} | {f.get('suggestion','')} |"
            for f in findings
        ) if findings else "| — | — | — | 未发现问题 | — |"
        vdoc = kb_frontmatter(f"需求验证报告-{st.session_state.date_str}", ["验证", "SRS"], ["需求验证报告"]) + f"""# 需求验证报告
验证日期：{st.session_state.date_str}
总体结论：**{st.session_state.validation_verdict}**
{f'发现 {len(findings)} 个问题' if findings else '未发现问题'}
| 类型 | 严重程度 | 涉及章节 | 问题描述 | 修正建议 |
|-----|---------|---------|---------|---------|
{finding_rows}
"""
        save_to_kb("wiki", f"需求验证报告-{st.session_state.date_str}-v1.0.md", vdoc)
        add_log(f"验证结论：{st.session_state.validation_verdict}")
        st.session_state.wf_phase = "a5_defects"
        st.rerun()
        return

    # ── A5: 缺陷报告 ──
    if phase == "a5_defects":
        add_log("正在生成 5 份缺陷分析报告...")
        reports = []
        for scenario, idx in DEFECT_SCENARIOS:
            p = f"""你正在编写一份"缺陷分析报告"。这是 报告{idx}/5。
项目背景：医疗器械租赁管理系统，涉及4种角色、7个核心模块。
场景：{scenario}
按以下格式输出：
# 缺陷分析报告 [#{idx}/5]
## 缺陷描述
## 缺陷类型
## 发现阶段
## 根因分析
## 影响范围
## 修复方案
## 防止复发措施"""
            reports.append(call_llm(p, system_prompt="你是一名软件质量保证专家，擅长缺陷分析。"))
        st.session_state.defect_reports = "\n\n---\n\n".join(reports)
        save_to_kb("wiki", f"缺陷分析报告集-{st.session_state.date_str}-v1.0.md",
                   kb_frontmatter("缺陷分析报告集", ["缺陷", "质量保证"]) + st.session_state.defect_reports)
        add_log("5 份缺陷分析报告已生成")
        st.session_state.wf_phase = "ccb"
        st.rerun()
        return

    # ── CCB: 由用户按钮触发，这里不做处理 ──
    if phase == "ccb":
        return

    # ── A6: 基线 ──
    if phase == "a6":
        version = f"BL-{st.session_state.date_str}-01"
        add_log(f"正在生成 RTM 并创立基线 {version}...")
        prompt = f"""请根据SRS文档生成需求溯源矩阵（RTM）。
SRS文档（摘要）：
{st.session_state.srs[:5000]}
需求清单：
{st.session_state.consolidated[:3000]}
基线版本：{version}
输出RTM表格格式：
| 需求编号 | 需求描述 | 来源涉众 | 模块 | 接口 | 测试要点 | 优先级 |"""
        st.session_state.rtm = call_llm(prompt, system_prompt="你是配置管理专家，精通需求溯源矩阵。")
        st.session_state.baseline_version = version
        save_to_kb("baselines", f"{version}/SRS-正式版.md",
                   kb_frontmatter(f"SRS-正式版-{version}", ["SRS", "基线", "冻结"], [f"SRS-{version}"]) + st.session_state.srs)
        save_to_kb("baselines", f"{version}/需求清单.md",
                   kb_frontmatter(f"需求清单-{version}", ["需求", "基线", "冻结"]) + st.session_state.consolidated)
        save_to_kb("baselines", f"{version}/溯源矩阵.md",
                   kb_frontmatter(f"溯源矩阵-{version}", ["RTM", "基线", "冻结"]) + st.session_state.rtm)
        save_to_kb("baselines", f"{version}/基线报告.md",
                   kb_frontmatter("基线报告", ["基线", "配置管理"]) + f"""# 基线创立报告
**基线版本：** {version}
**状态：** 已冻结
## 包含文档
- SRS-正式版
- 需求清单
- 溯源矩阵
""")
        add_log(f"基线 {version} 已创立！")
        st.session_state.wf_phase = "done"
        st.rerun()
        return


# ============================================================
# 🖥️ 页面
# ============================================================

st.set_page_config(page_title="医疗器械租赁管理系统", page_icon="🏥", layout="wide")
init_state()

st.title("🏥 医疗器械租赁管理系统")
st.markdown("**需求工程全流程自动化** — 逐条实时展示 AI 与涉众对话")

# ── 断点续跑：检测上一次中断的工作流 ──
saved_state = load_checkpoint()
if saved_state and not st.session_state.checkpoint_loaded:
    if st.session_state.wf_phase == "idle":
        # 每次页面加载时检查一次，用 st.session_state 的 flag 避免重复弹窗
        st.session_state["_resume_candidate"] = saved_state
        st.info("🔄 检测到上次中断的工作流", icon="💾")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("▶️ 继续运行", type="primary", use_container_width=True):
                # 恢复所有状态
                for k, v in saved_state.items():
                    st.session_state[k] = v
                st.session_state.checkpoint_loaded = True
                # 确保 step_logs 类型正确
                if not isinstance(st.session_state.step_logs, list):
                    st.session_state.step_logs = list(st.session_state.step_logs) if hasattr(st.session_state.step_logs, '__iter__') else []
                # 如果 phase 是 done，重置到 idle
                if st.session_state.wf_phase == "done":
                    st.session_state.wf_phase = "idle"
                add_log("🔄 从断点恢复工作流")
                st.rerun()
        with col2:
            if st.button("🗑️ 重新开始", use_container_width=True):
                clear_checkpoint()
                for k in list(st.session_state.keys()):
                    if k.startswith("_resume"):
                        continue
                    del st.session_state[k]
                init_state()
                st.rerun()

# ── 进度条 ──
phase = st.session_state.wf_phase
phase_progress = {
    "idle": 0.0, "done": 1.0,
}
# A1 进度
if phase in ("a1_think", "a1_ask"):
    r = st.session_state.a1_current_round
    max_r = st.session_state.a1_max_rounds
    phase_progress["a1"] = 0.05 + 0.17 * min(r / max_r, 1.0)
elif phase.startswith("a1_save:"):
    save_idx = int(phase.split(":")[1])
    phase_progress["a1"] = 0.20 + 0.02 * min(save_idx / len(STAKEHOLDERS), 1.0)

if phase in ("consolidate",):
    phase_progress[phase] = 0.22
if phase.startswith("a2"):
    phase_progress[phase] = 0.30
if phase == "a3":
    phase_progress[phase] = 0.50
if phase == "a4":
    phase_progress[phase] = 0.65
if phase == "a5" or phase == "a5_defects":
    phase_progress[phase] = 0.80
if phase == "ccb":
    phase_progress[phase] = 0.93
if phase == "a6":
    phase_progress[phase] = 0.95

pvalue = phase_progress.get(phase, 0.0)
if phase != "idle":
    st.progress(pvalue)

# ── 布局 ──
col_side, col_main = st.columns([1, 2.5])

with col_side:
    st.markdown("### 工作流进度")
    steps = [
        ("a1", "A1: 涉众对话"),
        ("consolidate", "A1: 需求汇总"),
        ("a2", "A2: 需求分析"),
        ("a3", "A3: UML 建模"),
        ("a4", "A4: SRS 生成"),
        ("a5", "A5: 验证/缺陷"),
        ("ccb", "CCB: 审批"),
        ("a6", "A6: 基线创立"),
        ("done", "完成"),
    ]
    order = {p: i for i, (p, _) in enumerate(steps)}
    # 把 a1_think/a1_ask/a1_save 映射到 a1
    phase_key = phase.split(":")[0]
    if phase_key in ("a1_think", "a1_ask", "a1_save"):
        phase_key = "a1"
    current_idx = order.get(phase_key, 0)
    for i, (p, label) in enumerate(steps):
        if phase == "done":
            icon = "✅"
        elif i < current_idx:
            icon = "✅"
        elif i == current_idx:
            icon = "🔵"
        else:
            icon = "⚪"
        st.markdown(f"{icon} {label}")

    st.markdown("---")

    if phase == "idle":
        if st.button("🚀 一键运行全流程", type="primary", use_container_width=True):
            # 生成4份涉众AI智能体配置文件（答辩交付物）
            ensure_agent_configs()
            # 清除断点，全新开始
            clear_checkpoint()
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            init_state()
            st.session_state.wf_phase = "a1_think"
            st.rerun()
    elif phase == "done":
        st.success(f"✅ 工作流完成！基线：{st.session_state.baseline_version}" if st.session_state.baseline_version else "✅ 工作流已结束")
        if st.button("🔄 重新运行", use_container_width=True):
            clear_checkpoint()
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            init_state()
            st.rerun()
    elif phase == "ccb":
        st.markdown("### 📋 CCB 审批")
        st.markdown(f"验证结论：**{st.session_state.validation_verdict}**")
        with st.form("ccb_form"):
            ccb_choice = st.selectbox("审批决定", ["通过", "不通过(获取类)", "不通过(分析类)"])
            ccb_comment = st.text_area("意见（可选）")
            if st.form_submit_button("提交审批", type="primary", use_container_width=True):
                st.session_state.ccb_verdict = ccb_choice
                st.session_state.ccb_comment = ccb_comment
                if ccb_choice == "通过":
                    st.session_state.wf_phase = "a6"
                else:
                    add_log(f"CCB 不通过（{ccb_choice}）：{ccb_comment}")
                    st.session_state.wf_phase = "done"  # 不通过时结束流程，日志保留
                st.rerun()
    elif phase != "idle":
        st.caption(f"当前: {phase.split(':')[0]}")

    st.markdown("---")
    st.markdown("### 📋 运行日志")
    for ts, msg in st.session_state.step_logs[-20:]:
        st.caption(f"{ts} {msg}")

# ── 主内容 ──
with col_main:
    tabs = st.tabs(["💬 对话记录", "📋 需求清单", "📐 UML 模型", "📄 SRS 文档", "🔎 验证/缺陷", "⛔ 基线/RTM"])

    # —— 对话记录 Tab ——
    with tabs[0]:
        has_dialogs = any(st.session_state.dialogs[s] for s in STAKEHOLDERS)
        if has_dialogs:
            sub_tabs = st.tabs([f"🧑‍💼 {s}" for s in STAKEHOLDERS])
            for si, stakeholder in enumerate(STAKEHOLDERS):
                with sub_tabs[si]:
                    dialog = st.session_state.dialogs[stakeholder]
                    if not dialog:
                        st.caption("暂无对话")
                    for i, msg in enumerate(dialog):
                        st.markdown(f"""<div style="background:#e8f4fd;padding:8px 14px;margin:6px 0;border-radius:12px 12px 12px 4px;max-width:85%;border-left:4px solid #2196F3"><small style="color:#666;">🤖 Q{i+1}：</small><br>{msg["q"]}</div>""", unsafe_allow_html=True)
                        st.markdown(f"""<div style="background:#f0f7e8;padding:8px 14px;margin:6px 0 12px 0;border-radius:12px 12px 4px 12px;max-width:85%;margin-left:auto;border-right:4px solid #4CAF50"><small style="color:#666;">👤 {stakeholder} 答：</small><br>{msg["a"]}</div>""", unsafe_allow_html=True)
        else:
            st.info("点击「一键运行全流程」后，每条对话将实时显示在这里")
        if phase in ("a1_think", "a1_ask"):
            r = st.session_state.a1_current_round
            max_r = st.session_state.a1_max_rounds
            st.caption(f"⏳ AI 自主对话中（第 {r+1}/{max_r} 轮）...")
        elif phase.startswith("a1_save:"):
            st.caption("⏳ 正在保存对话记录到知识库...")

    # ── 其他 Tab ──
    with tabs[1]:
        if st.session_state.consolidated:
            st.markdown(st.session_state.consolidated)
        else:
            st.info("待生成")

    with tabs[2]:
        if st.session_state.uml_use_case:
            with st.expander("用例图", expanded=True):
                st.code(st.session_state.uml_use_case, language="puml")
        if st.session_state.uml_activities:
            with st.expander("活动图"):
                st.code(st.session_state.uml_activities[:3000], language="puml")
        if not st.session_state.uml_use_case and not st.session_state.uml_activities:
            st.info("待生成")

    with tabs[3]:
        if st.session_state.srs:
            st.caption(f"字数：{len(st.session_state.srs)}")
            st.markdown(st.session_state.srs)
        else:
            st.info("待生成")

    with tabs[4]:
        if st.session_state.validation_report:
            st.markdown(f"**结论：** {st.session_state.validation_verdict}")
            data = extract_json(st.session_state.validation_report)
            for f in data.get("findings", []):
                sev = f.get("severity", "")
                icon = {"严重": "🔴", "中": "🟡", "低": "🟢"}.get(sev, "⚪")
                st.markdown(f"{icon} **[{f.get('type','')}]** {f.get('description','')}")
                st.caption(f"{f.get('section','')} | {f.get('suggestion','')}")
        if st.session_state.defect_reports:
            with st.expander("5 份缺陷分析报告"):
                st.markdown(st.session_state.defect_reports)
        if not st.session_state.validation_report and not st.session_state.defect_reports:
            st.info("待生成")

    with tabs[5]:
        if st.session_state.baseline_version:
            st.success(f"✅ 基线 {st.session_state.baseline_version}")
            with st.expander("RTM 溯源矩阵", expanded=True):
                st.markdown(st.session_state.rtm)
        else:
            st.info("待生成")

st.markdown("---")
st.caption(f"知识库：{os.path.join(os.path.dirname(os.path.abspath(__file__)), '.claude', 'knowledge-base')}")

# ============================================================
# 🚀 自动调度（每次 rerun 执行一步）
# ============================================================
tick()
save_checkpoint()  # 每步执行后自动保存断点
