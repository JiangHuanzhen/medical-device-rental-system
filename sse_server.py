"""医疗器械租赁管理系统 — FastAPI + SSE 实时工作流服务器"""

import os
import sys
import json
import queue
import threading
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".claude", "workflows"))

from requirement_workflow import (
    build_workflow,
    RequirementState,
    STAKEHOLDER_CONFIG,
    set_progress_callbacks,
    set_dialog_callbacks,
    set_result_callbacks,
    set_phase_callbacks,
    set_ccb_event,
    set_ccb_result,
    set_pause_requested,
    set_resume_signal,
    set_stop_requested,
    fire_progress,
    MAX_GLOBAL_ITERATIONS,
    WorkflowStopped,
)

from langgraph.checkpoint.memory import MemorySaver

# ── 全局状态 ──
STAKEHOLDERS = list(STAKEHOLDER_CONFIG.keys())

_wf_running = False
_wf_done_event = threading.Event()
_wf_error = ""

# SSE 通道
_sse_queues: list[queue.Queue] = []
_sse_lock = threading.Lock()

def _sse_broadcast(event: str, data: dict):
    payload = json.dumps(data, ensure_ascii=False)
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put_nowait((event, payload))
            except:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)

def _on_progress(msg: str):
    _sse_broadcast("progress", {"msg": msg})

def _on_dialog(stakeholder: str, question: str, answer: str):
    _sse_broadcast("dialog", {"stakeholder": stakeholder, "question": question, "answer": answer})

def _on_result(key: str, value):
    _sse_broadcast("result", {"key": key, "value": value})

def _on_phase(phase: str):
    _sse_broadcast("phase", {"phase": phase})

def _register_callbacks():
    set_progress_callbacks([_on_progress])
    set_dialog_callbacks([_on_dialog])
    set_result_callbacks([_on_result])
    set_phase_callbacks([_on_phase])

def _run_workflow():
    global _wf_running, _wf_error
    try:
        _register_callbacks()

        ccb_event = threading.Event()
        set_ccb_event(ccb_event)

        workflow = build_workflow()
        memory = MemorySaver()
        app = workflow.compile(checkpointer=memory)

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
            "a1_review_verdict": "",
            "a1_review_comment": "",
            "a3_review_verdict": "",
            "a3_review_comment": "",
            "a4_review_verdict": "",
            "a4_review_comment": "",
        }

        fire_progress("workflow started, 4 stakeholder agents in parallel...")
        fire_progress(f"global iteration limit: {MAX_GLOBAL_ITERATIONS}, forceful after exceeding")

        result = app.invoke(
            initial_state,
            config={"configurable": {"thread_id": "req-sse-001"}},
            recursion_limit=50,
        )

        _on_result("_complete", result.get("workflow_status", "完成"))
        fire_progress(f"all done! baseline: {result.get('baseline_version', 'N/A')}")

    except WorkflowStopped:
        _wf_error = "stopped by user"
        fire_progress("workflow stopped by user")
    except Exception as e:
        _wf_error = str(e)
        fire_progress(f"error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _wf_running = False
        _wf_done_event.set()
        _sse_broadcast("done", {"status": "ok", "error": _wf_error})


# ── FastAPI ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/events")
async def sse_events(request: Request):
    q: queue.Queue = queue.Queue()
    with _sse_lock:
        _sse_queues.append(q)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = q.get_nowait()
                    yield {"event": event, "data": data}
                except queue.Empty:
                    await asyncio.sleep(0.1)
        finally:
            with _sse_lock:
                if q in _sse_queues:
                    _sse_queues.remove(q)

    return EventSourceResponse(generate())


@app.post("/start")
async def start_workflow():
    global _wf_running, _wf_error
    if _wf_running:
        return JSONResponse({"ok": False, "error": "workflow already running"})
    _wf_running = True
    _wf_error = ""
    _wf_done_event.clear()
    thread = threading.Thread(target=_run_workflow, daemon=True)
    thread.start()
    return JSONResponse({"ok": True})


@app.post("/ccb")
async def ccb_submit(request: Request):
    body = await request.json()
    verdict = body.get("verdict", "不通过(分析类)")
    comment = body.get("comment", "")
    set_ccb_result(verdict, comment)
    _on_progress(f"CCB submitted: {verdict}")
    return JSONResponse({"ok": True})


@app.post("/pause")
async def pause_workflow():
    if not _wf_running:
        return JSONResponse({"ok": False, "error": "workflow not running"})
    set_pause_requested()
    _on_progress("pause requested, will pause after current node completes")
    return JSONResponse({"ok": True})


@app.post("/resume")
async def resume_workflow():
    set_resume_signal()
    _on_progress("resumed")
    return JSONResponse({"ok": True})


@app.post("/stop")
async def stop_workflow():
    set_stop_requested()
    _on_progress("stop requested")
    return JSONResponse({"ok": True})


@app.get("/status")
async def get_status():
    return JSONResponse({
        "running": _wf_running,
        "done": _wf_done_event.is_set(),
        "error": _wf_error,
    })


# ── HTML page embedded ──

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_PAGE)


HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Medical Device Rental - Requirements Workflow</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;color:#333}
header{background:linear-gradient(135deg,#1a73e8,#0d47a1);color:white;padding:18px 28px;display:flex;justify-content:space-between;align-items:center}
header h1{font-size:22px;font-weight:600}
header span{font-size:13px;opacity:.9}
.container{display:flex;gap:20px;padding:20px;max-width:1400px;margin:0 auto}
.sidebar{width:320px;flex-shrink:0}
.main{flex:1;min-width:0}
.card{background:white;border-radius:12px;padding:16px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.card h3{font-size:14px;color:#666;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.progress-bar{height:8px;background:#e8eaed;border-radius:4px;overflow:hidden;margin-bottom:14px}
.progress-fill{height:100%;background:linear-gradient(90deg,#1a73e8,#4fc3f7);border-radius:4px;transition:width .5s ease;width:0%}
.steps .step{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:13px;color:#888}
.steps .step.active{color:#1a73e8;font-weight:600}
.steps .step.done{color:#34a853}
.steps .step .dot{width:8px;height:8px;border-radius:50%;background:#ddd;flex-shrink:0}
.steps .step.active .dot{background:#1a73e8;box-shadow:0 0 0 3px rgba(26,115,232,.2)}
.steps .step.done .dot{background:#34a853}
.log{font-size:12px;color:#555;max-height:300px;overflow-y:auto;line-height:1.6}
.log div{padding:2px 0;border-bottom:1px solid #f0f0f0}
.tabs{display:flex;gap:4px;margin-bottom:14px;flex-wrap:wrap}
.tab-btn{padding:8px 16px;border:none;background:#e8eaed;border-radius:8px;cursor:pointer;font-size:13px;transition:.2s}
.tab-btn:hover{background:#d2d5d9}
.tab-btn.active{background:#1a73e8;color:white}
.tab-content{display:none}
.tab-content.active{display:block}
.chat{margin-bottom:12px}
.chat-q{background:#e3f2fd;padding:10px 14px;border-radius:12px 12px 12px 4px;max-width:85%;border-left:4px solid #1a73e8;margin-bottom:6px}
.chat-a{background:#e8f5e9;padding:10px 14px;border-radius:12px 12px 4px 12px;max-width:85%;margin-left:auto;border-right:4px solid #34a853;margin-bottom:14px}
.chat small{color:#666;font-size:11px;display:block;margin-bottom:3px}
.result-content{font-size:13px;line-height:1.7;max-height:500px;overflow-y:auto;white-space:pre-wrap;background:#fafafa;padding:12px;border-radius:8px}
.empty{color:#999;font-size:13px;text-align:center;padding:30px}
.review-form,.ccb-form{background:#fff3e0;border:1px solid #ffe0b2;border-radius:12px;padding:16px}
.review-form label,.ccb-form label{font-size:13px;display:block;margin-bottom:4px;color:#555}
.review-form select,.ccb-form select,.review-form textarea,.ccb-form textarea{width:100%;padding:8px 12px;border:1px solid #ddd;border-radius:8px;font-size:13px;margin-bottom:10px}
.review-form textarea,.ccb-form textarea{min-height:60px;resize:vertical}
.btn{padding:10px 24px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;transition:.2s}
.btn-primary{background:#1a73e8;color:white}
.btn-primary:hover{background:#1557b0}
.btn-primary:disabled{background:#a8c7fa;cursor:not-allowed}
.btn-success{background:#34a853;color:white}
.btn-success:hover{background:#2d9249}
.btn-warning{background:#f9ab00;color:white}
.btn-warning:hover{background:#e09600}
.btn-warning:disabled{background:#f9d67a;cursor:not-allowed}
.status-badge{display:inline-block;padding:4px 10px;border-radius:12px;font-size:12px;font-weight:600}
.status-running{background:#e3f2fd;color:#1a73e8}
.status-paused{background:#fff3e0;color:#e65100}
.status-done{background:#e6f4ea;color:#34a853}
.status-error{background:#fce8e6;color:#d93025}
.stakeholder-tabs{display:flex;gap:4px;margin-bottom:10px;flex-wrap:wrap}
.stakeholder-tab{padding:6px 14px;border:none;background:#f0f0f0;border-radius:6px;cursor:pointer;font-size:12px}
.stakeholder-tab.active{background:#1a73e8;color:white}
.stakeholder-content{display:none}
.stakeholder-content.active{display:block}
.iteration-badge{background:#e8f5e9;color:#2e7d32;padding:4px 10px;border-radius:12px;font-size:12px;display:inline-block;margin-bottom:8px}
</style>
</head>
<body>

<header>
  <div><h1>Medical Device Rental Management System</h1><span>Requirements Engineering Workflow - with review checkpoints</span></div>
  <div>
    <span id="iterationBadge" class="iteration-badge" style="display:none">Round 1</span>
    <span id="statusBadge" class="status-badge status-running" style="display:none">Pending</span>
  </div>
</header>

<div class="container">
  <div class="sidebar">
    <div class="card">
      <h3>Progress</h3>
      <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
      <div class="steps" id="steps">
        <div class="step" data-phase="A1_对话"><span class="dot"></span>A1: Stakeholder Dialog</div>
        <div class="step" data-phase="A1_汇总"><span class="dot"></span>A1: Consolidate</div>
        <div class="step" data-phase="A2_分析"><span class="dot"></span>A2: Quality Analysis</div>
        <div class="step" data-phase="A3_UML"><span class="dot"></span>A3: UML Modeling</div>
        <div class="step" data-phase="A4_SRS"><span class="dot"></span>A4: SRS Generation</div>
        <div class="step" data-phase="A5_验证"><span class="dot"></span>A5: Validation</div>
        <div class="step" data-phase="A5_缺陷报告"><span class="dot"></span>A5: Defect Reports</div>
        <div class="step" data-phase="CCB_审批"><span class="dot"></span>CCB: Pending Approval</div>
        <div class="step" data-phase="A6_基线"><span class="dot"></span>A6: Baseline</div>
        <div class="step" data-phase="A7_ADR"><span class="dot"></span>A7: ADR</div>
        <div class="step" data-phase="_done"><span class="dot"></span>Done</div>
      </div>
    </div>

    <div class="card">
      <button id="startBtn" class="btn btn-primary" style="width:100%;margin-bottom:8px">Run Full Workflow</button>
      <div style="display:flex;gap:6px;margin-bottom:10px">
        <button id="pauseBtn" class="btn btn-warning" style="display:none;flex:1" disabled>Pause</button>
        <button id="resumeBtn" class="btn btn-success" style="display:none;flex:1" disabled>Resume</button>
        <button id="stopBtn" class="btn" style="display:none;flex:0.6;background:#d93025;color:white;border:none" disabled>Stop</button>
      </div>

      <div id="ccbPanel" style="display:none">
        <div class="ccb-form">
          <h3 style="font-size:14px;margin-bottom:8px">CCB Approval</h3>
          <label>Validation Result: <span id="ccbVerdict" style="font-weight:600">--</span></label>
          <select id="ccbChoice"><option>Pass</option><option>Fail (Acquisition)</option><option>Fail (Analysis)</option></select>
          <textarea id="ccbComment" placeholder="Comments (optional)"></textarea>
          <button id="ccbBtn" class="btn btn-success" style="width:100%">Submit Approval</button>
        </div>
      </div>

      <div id="donePanel" style="display:none">
        <p style="font-size:13px;color:#34a853;margin-bottom:10px">Workflow Complete!</p>
        <button id="resetBtn" class="btn" style="width:100%;background:#e8eaed">Run Again</button>
      </div>
    </div>

    <div class="card">
      <h3>Log</h3>
      <div class="log" id="logContainer"></div>
    </div>
  </div>

  <div class="main">
    <div class="card">
      <div class="tabs" id="mainTabs">
        <button class="tab-btn active" data-tab="dialogs">Dialog</button>
        <button class="tab-btn" data-tab="requirements">Requirements</button>
        <button class="tab-btn" data-tab="uml">UML</button>
        <button class="tab-btn" data-tab="srs">SRS</button>
        <button class="tab-btn" data-tab="validation">Validation</button>
        <button class="tab-btn" data-tab="baseline">Baseline/RTM</button>
      </div>

      <div id="tab-dialogs" class="tab-content active">
        <div class="stakeholder-tabs" id="stakeholderTabs"></div>
        <div id="stakeholderDialogs"></div>
        <div class="empty" id="dialogEmpty">Click Run Full Workflow to start</div>
      </div>
      <div id="tab-requirements" class="tab-content"><div class="result-content" id="reqContent">pending</div></div>
      <div id="tab-uml" class="tab-content"><div class="result-content" id="umlContent">pending</div></div>
      <div id="tab-srs" class="tab-content"><div class="result-content" id="srsContent">pending</div></div>
      <div id="tab-validation" class="tab-content"><div class="result-content" id="validationContent">pending</div></div>
      <div id="tab-baseline" class="tab-content"><div class="result-content" id="baselineContent">pending</div></div>
    </div>
  </div>
</div>

<script>
const STAKEHOLDERS = ["招商业务员", "库房人员", "运维工程师", "财务"];
const dialogs = {};
const results = {};
let phaseHistory = [];
let sse = null;

function initStakeholderTabs() {
  const container = document.getElementById("stakeholderTabs");
  const contentArea = document.getElementById("stakeholderDialogs");
  container.innerHTML = STAKEHOLDERS.map((s, i) =>
    `<button class="stakeholder-tab${i===0?' active':''}" data-idx="${i}">${s}</button>`
  ).join("");
  contentArea.innerHTML = STAKEHOLDERS.map((s, i) =>
    `<div class="stakeholder-content${i===0?' active':''}" data-idx="${i}" id="dialog-${s}"></div>`
  ).join("");

  document.querySelectorAll(".stakeholder-tab").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".stakeholder-tab").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".stakeholder-content").forEach(c => c.classList.remove("active"));
      btn.classList.add("active");
      document.querySelector(`.stakeholder-content[data-idx="${btn.dataset.idx}"]`).classList.add("active");
    };
  });
}

function addDialog(stakeholder, question, answer) {
  if (!dialogs[stakeholder]) dialogs[stakeholder] = [];
  dialogs[stakeholder].push({q: question, a: answer});
  renderDialog(stakeholder);
  document.getElementById("dialogEmpty").style.display = "none";
}

function renderDialog(stakeholder) {
  const container = document.getElementById("dialog-" + stakeholder);
  if (!container) return;
  const items = dialogs[stakeholder] || [];
  container.innerHTML = items.map((m, i) =>
    `<div class="chat">
      <div class="chat-q"><small>Q${i+1}:</small>${escapeHtml(m.q)}</div>
      <div class="chat-a"><small>A:</small>${escapeHtml(m.a)}</div>
    </div>`
  ).join("");
}

function updateProgress(phase) {
  phaseHistory.push(phase);
  const steps = document.querySelectorAll(".step");
  steps.forEach(step => step.classList.remove("active", "done"));
  let hit = false;
  steps.forEach(step => {
    const p = step.dataset.phase;
    if (p === phase) { step.classList.add("active"); hit = true; }
    else if (!hit) step.classList.add("done");
  });
  const pcts = {
    A1_对话:10, A1_汇总:18,
    A2_分析:30,
    A3_UML:45,
    A4_SRS:60,
    A5_验证:73,
    A5_缺陷报告:80,
    CCB_审批:88,
    A6_基线:95,
    A7_ADR:99,
    _done:100
  };
  const pct = pcts[phase] || 0;
  document.getElementById("progressFill").style.width = pct + "%";
}

function escapeHtml(s) {
  if (!s) return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function connectSSE() {
  if (sse) sse.close();
  sse = new EventSource("/events");
  sse.addEventListener("progress", e => {
    const data = JSON.parse(e.data);
    addLog(data.msg);
    const m = data.msg.match(/global round (\d+)/);
    if (m) {
      document.getElementById("iterationBadge").style.display = "inline-block";
      document.getElementById("iterationBadge").textContent = "Round " + m[1];
    }
  });
  sse.addEventListener("dialog", e => {
    const data = JSON.parse(e.data);
    addDialog(data.stakeholder, data.question, data.answer);
  });
  sse.addEventListener("result", e => {
    const data = JSON.parse(e.data);
    results[data.key] = data.value;
    updateResultTabs();
  });
  sse.addEventListener("phase", e => {
    const data = JSON.parse(e.data);
    updateProgress(data.phase);
    if (data.phase === "CCB_审批") showCCBPanel();
  });
  sse.addEventListener("done", e => {
    document.getElementById("startBtn").disabled = false;
    document.getElementById("startBtn").textContent = "Run Full Workflow";
    document.getElementById("statusBadge").textContent = "Done";
    document.getElementById("statusBadge").className = "status-badge status-done";
    document.getElementById("pauseBtn").style.display = "none";
    document.getElementById("resumeBtn").style.display = "none";
    document.getElementById("stopBtn").style.display = "none";
    updateProgress("_done");
    document.getElementById("donePanel").style.display = "block";
    document.getElementById("ccbPanel").style.display = "none";
  });
  sse.onerror = () => {};
}

function showCCBPanel() {
  document.getElementById("ccbPanel").style.display = "block";
  document.getElementById("startBtn").disabled = true;
  document.getElementById("startBtn").textContent = "Waiting for CCB...";
}

function addLog(msg) {
  const log = document.getElementById("logContainer");
  const div = document.createElement("div");
  div.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  };
});

function updateResultTabs() {
  if (results.consolidated_requirements)
    document.getElementById("reqContent").innerHTML = "<pre>" + escapeHtml(results.consolidated_requirements) + "</pre>";
  if (results.uml_use_case) {
    let html = "<b>Use Case Diagram</b><pre>" + escapeHtml(results.uml_use_case) + "</pre>";
    if (results.uml_activity_diagrams)
      html += "<hr><b>Activity/Sequence/ER</b><pre>" + escapeHtml(results.uml_activity_diagrams).substring(0,5000) + "...</pre>";
    document.getElementById("umlContent").innerHTML = html;
  }
  if (results.srs_draft)
    document.getElementById("srsContent").innerHTML = "<pre>" + escapeHtml(results.srs_draft) + "</pre>";
  if (results.validation_report) {
    let vr = "<b>Verdict:</b> " + (results.validation_verdict || "--") + "<hr>";
    try {
      const d = JSON.parse(results.validation_report.substring(results.validation_report.indexOf("{")));
      if (d.findings) d.findings.forEach(f => {
        vr += `<div style="margin-bottom:6px">${f.severity==='严重'?'R':f.severity==='中'?'Y':'G'} <b>[${f.type}]</b> ${escapeHtml(f.description)}<br><small>${f.section||''} | ${escapeHtml(f.suggestion||'')}</small></div>`;
      });
    } catch(e) {}
    document.getElementById("validationContent").innerHTML = vr;
  }
  if (results.defect_reports)
    document.getElementById("validationContent").innerHTML += "<hr><details><summary>5 Defect Reports</summary><pre>" + escapeHtml(results.defect_reports) + "</pre></details>";
  if (results.baseline_version)
    document.getElementById("baselineContent").innerHTML = "<b>Baseline:</b> " + results.baseline_version + "<hr><pre>" + escapeHtml(results.rtm || "") + "</pre>";
}

document.getElementById("startBtn").onclick = async () => {
  document.getElementById("startBtn").disabled = true;
  document.getElementById("startBtn").textContent = "Running...";
  document.getElementById("statusBadge").style.display = "inline-block";
  document.getElementById("statusBadge").textContent = "Running";
  document.getElementById("statusBadge").className = "status-badge status-running";
  document.getElementById("pauseBtn").style.display = "inline-block";
  document.getElementById("pauseBtn").disabled = false;
  document.getElementById("stopBtn").style.display = "inline-block";
  document.getElementById("stopBtn").disabled = false;
  document.getElementById("resumeBtn").style.display = "none";
  document.getElementById("donePanel").style.display = "none";

  Object.keys(dialogs).forEach(k => delete dialogs[k]);
  Object.keys(results).forEach(k => delete results[k]);
  document.getElementById("logContainer").innerHTML = "";
  document.querySelectorAll(".stakeholder-content").forEach(c => c.innerHTML = "");
  document.getElementById("dialogEmpty").style.display = "block";
  ["reqContent","umlContent","srsContent","validationContent","baselineContent"].forEach(id => {
    document.getElementById(id).innerHTML = "pending";
  });
  document.querySelectorAll(".step").forEach(s => s.classList.remove("active","done"));
  document.getElementById("progressFill").style.width = "0%";
  document.getElementById("iterationBadge").style.display = "none";

  const resp = await fetch("/start", {method:"POST"});
  const data = await resp.json();
  if (!data.ok) {
    addLog("Error: " + (data.error || "start failed"));
    document.getElementById("startBtn").disabled = false;
    document.getElementById("startBtn").textContent = "Run Full Workflow";
  }
};

document.getElementById("pauseBtn").onclick = async () => {
  document.getElementById("pauseBtn").disabled = true;
  document.getElementById("pauseBtn").textContent = "Pausing...";
  document.getElementById("resumeBtn").style.display = "inline-block";
  document.getElementById("resumeBtn").disabled = false;
  document.getElementById("statusBadge").textContent = "Paused";
  document.getElementById("statusBadge").className = "status-badge status-paused";
  await fetch("/pause", {method:"POST"});
};

document.getElementById("resumeBtn").onclick = async () => {
  document.getElementById("resumeBtn").disabled = true;
  document.getElementById("resumeBtn").textContent = "Resuming...";
  document.getElementById("pauseBtn").disabled = false;
  document.getElementById("pauseBtn").textContent = "Pause";
  document.getElementById("statusBadge").textContent = "Running";
  document.getElementById("statusBadge").className = "status-badge status-running";
  await fetch("/resume", {method:"POST"});
  document.getElementById("resumeBtn").style.display = "none";
};

document.getElementById("stopBtn").onclick = async () => {
  document.getElementById("stopBtn").disabled = true;
  document.getElementById("stopBtn").textContent = "Stopping...";
  document.getElementById("pauseBtn").disabled = true;
  document.getElementById("resumeBtn").disabled = true;
  await fetch("/stop", {method:"POST"});
  document.getElementById("stopBtn").textContent = "Stopped";
};

document.getElementById("ccbBtn").onclick = async () => {
  const verdict = document.getElementById("ccbChoice").value;
  const comment = document.getElementById("ccbComment").value;
  await fetch("/ccb", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({verdict, comment})});
  document.getElementById("ccbPanel").style.display = "none";
  document.getElementById("startBtn").textContent = "Continuing...";
};

document.getElementById("resetBtn").onclick = () => {
  document.getElementById("donePanel").style.display = "none";
  document.getElementById("startBtn").disabled = false;
  document.getElementById("startBtn").textContent = "Run Full Workflow";
  document.getElementById("statusBadge").style.display = "none";
};

initStakeholderTabs();
connectSSE();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8502)
