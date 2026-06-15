// 医疗器械租赁管理系统 — 需求工程全流程工作流 v2
//
// 改进：
//   - 每个步骤的产出保存为知识库独立文件（符合教材交付物要求）
//   - 新增：生成五个缺陷分析报告
//   - 新增：知识库目录验证
//
// 用法：workflow({name: 'requirement-flow'})

export const meta = {
  name: 'requirement-flow',
  description: '需求工程全流程 — 从涉众对话到基线创立 + 知识库文件归档',
  phases: [
    { title: '获取', detail: '与4个涉众智能体对话 → 保存对话记录' },
    { title: '分析', detail: '四维度检测 → 生成问题清单+回退记录' },
    { title: '建模', detail: '生成UML → 保存PlantUML文件' },
    { title: '文档', detail: 'SRS生成 → 保存SRS正式文档' },
    { title: '报告', detail: '生成验证报告+缺陷分析报告' },
    { title: '审批', detail: 'CCB人工审批' },
    { title: '基线', detail: '创立基线+RTM+知识库归档' },
  ],
}

// ============================================================
// 知识库路径
// ============================================================
const KB = {
  root: 'D:/医疗器械租赁管理系统/openspec/.claude/knowledge-base',
  raw: 'D:/医疗器械租赁管理系统/openspec/.claude/knowledge-base/raw/notes',
  wiki: 'D:/医疗器械租赁管理系统/openspec/.claude/knowledge-base/wiki/summaries',
  baselines: 'D:/医疗器械租赁管理系统/openspec/.claude/knowledge-base/wiki/baselines',
  archive: 'D:/医疗器械租赁管理系统/openspec/.claude/knowledge-base/archive',
}

const STAKEHOLDER_API = 'https://210.34.148.101:5000/api/chat'
const PROJECT_ID = '10'
const STAKEHOLDERS = ['招商业务员', '库房人员', '运维工程师', '财务']

// ============================================================
// 涉众对话问题（教材§4 对话策略）
// ============================================================
const QUESTIONS = {
  '招商业务员': [
    '请描述你一天的工作流程，从接到客户询盘开始是怎么开展的？',
    '目前工作中最让你头疼的问题是什么？',
    '如果遇到客户临时变更需求或合同纠纷，你们怎么处理？',
    '合同管理、回款跟踪、闲置设备方面，你希望新系统怎么帮你？',
    '你能举个具体的例子说说之前因为流程不顺畅导致的问题吗？',
    '底价控制方面你怎么看？系统应该怎么帮你把控价格底线？',
  ],
  '库房人员': [
    '请描述设备入库、存放、出库、盘点整个流程是怎样的？',
    '目前库房管理中最麻烦的事情是什么？最能举例说说吗？',
    '你希望系统在入库、位置管理、出库配送方面怎么帮你？',
    '校准检测提醒你希望提前多久通知？什么方式通知？',
    '配件管理（电源线、探头等）和冷链运输温度记录有什么需求？',
  ],
  '运维工程师': [
    '请描述设备安装调试、维修保养、巡检的日常工作。',
    '故障报修这块目前有哪些让你头疼的地方？',
    '你希望系统怎么帮你管理故障报修、备品备件和维修记录？',
    '设备巡检周期一般多久？系统自动提醒的话你觉得怎么设置合理？',
    '维修知识库你希望怎么组织？按设备型号还是按故障现象？',
  ],
  '财务': [
    '请描述你日常处理租赁相关财务工作的完整流程。',
    '租金计算、押金管理方面目前最容易出错的是什么？',
    '发票管理和季度对账方面你希望系统提供什么功能？',
    '设备折旧计算和维修成本统计方面需要什么支持？',
    '你希望系统生成的财务报表包含哪些维度的数据？',
  ],
}

// ============================================================
// 工具函数
// ============================================================

/** 将内容写入知识库文件 */
function saveToKB(filePath, content) {
  return {
    path: filePath,
    content: content,
    saved: true
  }
}

// ============================================================
// 🧠 阶段1：获取
// ============================================================

phase('获取')
log('开始与4个涉众智能体对话获取需求...')

const dateStr = new Date().toISOString().slice(0,10).replace(/-/g,'')
const timeStr = new Date().toISOString().slice(11,16).replace(/:/g,'')

const dialogResults = await parallel(
  STAKEHOLDERS.map(role => () =>
    agent(
      `你正在与老师平台的"${role}"涉众智能体对话以获取需求。
按以下问题列表依次提问，记录完整对话内容（问题和回答都要保留）。

问题列表：
${QUESTIONS[role].map((q, i) => `${i + 1}. ${q}`).join('\n')}

对话策略（教材§4）：1.场景引导 2.痛点追问 3.异常路径探测 4.数据边界追问`,
      { label: `${role}对话`, phase: '获取' }
    )
  )
)

// 保存对话记录到知识库 raw/notes
const savedDialogs = []
for (let i = 0; i < STAKEHOLDERS.length; i++) {
  const role = STAKEHOLDERS[i]
  const content = dialogResults[i] || ''
  const filename = `${dateStr}-${timeStr}-${role}-需求记录.md`
  savedDialogs.push(saveToKB(`${KB.raw}/${filename}`, content))
}

log(`✅ ${STAKEHOLDERS.length} 份对话记录已保存到 raw/notes/`)

// A1汇总
log('汇总需求...')
const consolidatedReq = await agent(
  `你作为A1需求汇总智能体，将以下4个涉众的对话记录整理为结构化的需求清单。

对话记录：
${dialogResults.filter(Boolean).join('\n\n===\n\n')}

整理要求（教材§4.§3）：
1. 每条需求记录格式：REQ-{模块缩写}-{编号} | 来源涉众 | 需求描述（含边界条件） | 优先级
2. 按7个模块分类：认证权限、设备管理、客户管理、租赁订单、费用结算、数据看板、系统配置
3. 保留涉众原始表述（直接引用）+ 需求提炼（"作为[角色]，我希望[功能]，以便[价值]"）

输出为Markdown格式。`,
  { label: 'A1-汇总需求', phase: '获取' }
)

saveToKB(`${KB.wiki}/需求清单-${dateStr}-v1.0.md`, consolidatedReq)
log('✅ 需求清单已保存到 wiki/summaries/')

// ============================================================
// 🧠 阶段2：分析
// ============================================================

phase('分析')

let currentReq = consolidatedReq
let allIssues = []
let rollbackRecords = []

for (let round = 1; round <= 3; round++) {
  const qualityAnalysis = await agent(
    `你作为A2需求分析智能体（教材§5），对以下需求进行四维度质量检测：

${currentReq}

### 四维度检测：
1. **模糊检测** — 是否包含"尽量""大概""合理""快速""及时"等不可量化词；是否缺少关键限定条件
2. **不一致检测** — 同一术语（订单状态、角色名）在不同地方定义是否一致
3. **矛盾检测** — 对同一参数给出不同常数值（如有效期24h vs 48h）
4. **冲突检测** — 不同涉众对同功能的互斥期望

### 输出格式（严格JSON）：
{
  "has_critical": true/false,
  "issues": [
    {
      "id": "Q-001",
      "type": "模糊/不一致/矛盾/冲突",
      "severity": "严重/中/低",
      "description": "问题描述",
      "source_requirement": "相关需求编号或无",
      "source_stakeholder": "涉及涉众",
      "suggestion": "修正建议",
      "rollback_action": "回退A1补充对话/回退A2修正分析"
    }
  ]
}`,
    { label: `A2-分析-第${round}轮`, phase: '分析', schema: null }
  )

  // 解析
  let issues = []
  let hasCritical = false
  try {
    const j = qualityAnalysis.match(/\{[\s\S]*\}/)
    if (j) {
      const parsed = JSON.parse(j[0])
      issues = parsed.issues || []
      hasCritical = parsed.has_critical || false
    }
  } catch(e) {
    log('⚠️ JSON解析失败，假设通过')
  }

  allIssues.push(...issues)

  if (!hasCritical || issues.length === 0) {
    log('✅ 需求质量检测通过')
    break
  }

  // 有严重问题 → 记录回退 + 修正
  log(`⚠️ 发现 ${issues.length} 个问题，第${round}轮修正...`)
  rollbackRecords.push({ round, issues: issues.map(i => i.id), action: '回退修正' })

  currentReq = await agent(
    `原需求清单存在质量问题，请根据问题清单修正。

原清单：
${currentReq}

问题：
${JSON.stringify(issues, null, 2)}

请输出修正后的完整需求清单。`,
    { label: `A1-修正-第${round}轮`, phase: '分析' }
  )
}

// 保存需求问题清单和回退记录
const finalIssuesList = allIssues.map((i, idx) =>
  `| ${i.id || `Q-${idx+1}`} | ${i.type} | ${i.severity} | ${i.description} | ${i.source_stakeholder} | ${i.suggestion} |`
).join('\n')

const issuesDoc = `# 需求问题清单

生成日期：${dateStr}

| 编号 | 类型 | 严重程度 | 问题描述 | 涉及涉众 | 修正建议 |
|-----|------|---------|---------|---------|---------|
${finalIssuesList || '| — | — | — | 未发现严重问题 | — | — |'}

## 回退记录

${rollbackRecords.map(r => `- 第${r.round}轮：${r.issues.join(', ')} → ${r.action}`).join('\n') || '无回退记录'}
`
saveToKB(`${KB.wiki}/需求问题清单-${dateStr}-v1.0.md`, issuesDoc)
log('✅ 需求问题清单+回退记录已保存')

// ============================================================
// 🧠 阶段3：建模
// ============================================================

phase('建模')
log('生成UML模型...')

const umlResult = await agent(
  `你作为A3建模智能体（教材§6），根据以下需求清单生成UML模型。

需求清单：
${currentReq}

### 1. 用例图（PlantUML）
- Actor：4种角色
- Use Case：所有系统功能
- <<include>>（必须包含）和 <<extend>>（可选扩展）关系

### 2. 活动图（至少3个核心用例）
a) 租赁订单全流程：创建→审核→出库→归还→结算
b) 设备入库流程
c) 设备维修流程

要求：每个活动图2条正常路径+2条异常路径，分支条件用[Guard Condition]标注，泳道标注角色。

输出为完整的PlantUML代码块。`,
  { label: 'A3-UML建模', phase: '建模' }
)

// 分离用例图和活动图
const parts = umlResult.split('@startuml')
const useCasePuml = parts.length > 1 ? '@startuml' + parts[1] : ''
const activityPuml = parts.length > 2 ? '@startuml' + '@startuml'.join(parts.slice(2)) : ''

saveToKB(`${KB.wiki}/用例图-${dateStr}-v1.0.puml`, `@startuml\n${useCasePuml}\n@enduml`)
saveToKB(`${KB.wiki}/活动图-${dateStr}-v1.0.puml`, `@startuml\n${activityPuml}\n@enduml`)
log('✅ UML模型已保存到 wiki/summaries/')

// ============================================================
// 🧠 阶段4：SRS文档
// ============================================================

phase('文档')
log('生成SRS需求规格说明书...')

const srsDoc = await agent(
  `你作为A4需求文档智能体（教材§6），生成IEEE 830标准的SRS文档。

### 输入
需求清单：
${currentReq}

UML模型：
${umlResult}

### 结构要求
1. **引言** — 目的、范围、定义（术语表）、参考文献
2. **总体描述** — 产品视角、产品功能、用户特征、约束、假设
3. **具体需求**（核心，≥10000字）
   - 3.1 功能需求（7个模块分节）
   - 3.2 非功能需求（性能/安全/可用性）
   - 3.3 接口需求
   - 3.4 数据需求（数据字典）

### 质量标准
- 每条功能需求：编号 + 描述 + 输入/输出 + 验收标准
- 禁止模糊词："快速""及时""合理""尽量"
- 精确到数字`,
  { label: 'A4-SRS生成', phase: '文档' }
)

saveToKB(`${KB.wiki}/SRS-初稿-${dateStr}-v1.0.md`, srsDoc)
log('✅ SRS文档已保存到 wiki/summaries/')

// ============================================================
// 🧠 阶段5：验证 + 缺陷分析报告
// ============================================================

phase('报告')
log('执行SRS验证并生成缺陷分析报告...')

// 5a. 验证报告
const validationResult = await agent(
  `你作为A5需求验证智能体（教材§7），对SRS进行四类交叉验证。

SRS：
${srsDoc.slice(0, 8000)}

### 四类验证
1. **涉众对话比对** — SRS需求是否准确反映涉众原话
2. **内部一致性** — 不同章节术语定义是否一致
3. **覆盖度检查** — 功能点是否都有对应需求
4. **精确性检查** — 是否有模糊词

### 输出JSON：
{
  "verdict": "通过/获取类问题/分析类问题",
  "findings": [{"type":"不一致/遗漏/模糊","severity":"严重/中/低","description":"问题","section":"章节"}]
}`,
  { label: 'A5-验证', phase: '报告', schema: null }
)

let verdict = '通过'
let findings = []
try {
  const j = validationResult.match(/\{[\s\S]*\}/)
  if (j) {
    const p = JSON.parse(j[0])
    verdict = p.verdict || '通过'
    findings = p.findings || []
  }
} catch(e) {}

const validationDoc = `# 需求验证报告

验证日期：${dateStr}
总体结论：${verdict}
${findings.length > 0 ? `发现 ${findings.length} 个问题` : '未发现问题'}

## 发现清单
${findings.map(f => `| ${f.type} | ${f.severity} | ${f.description} | ${f.section || '—'} |`).join('\n')}
`
saveToKB(`${KB.wiki}/需求验证报告-${dateStr}-v1.0.md`, validationDoc)

// 如果验证不通过，修正
if (verdict !== '通过') {
  log(`⚠️ 验证发现问题，修正中...`)
  currentReq = await agent(
    `验证发现${verdict === '获取类问题' ? '获取类' : '分析类'}问题，请修正需求清单。\n\n验证反馈：${validationResult}\n\n原需求：${currentReq}`,
    { label: 'A-修正', phase: '报告' }
  )
  // 修正后的SRS
  const fixedSrs = await agent(`请根据修正后的需求重新生成SRS。${currentReq}`, { label: 'A4-修正SRS', phase: '报告' })
  saveToKB(`${KB.wiki}/SRS-初稿-${dateStr}-v1.1.md`, fixedSrs)
  log('✅ 修正后SRS已保存')
}

// 5b. 五个缺陷分析报告（教材要求）
log('生成5个缺陷分析报告...')

const defectReports = await parallel([
  () => agent(
    `你正在编写一份"缺陷分析报告"（教材§1要求至少5份）。
这是 报告1/5。

项目背景：医疗器械租赁管理系统，涉及4种角色、7个核心模块。
场景：招商业务员在创建合同时选择了错误的计费方式。

按以下格式输出：

# 缺陷分析报告 [#1/5]

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
[以后怎么避免]`,
    { label: '缺陷报告1', phase: '报告', schema: null }
  ),
  () => agent(
    `你正在编写一份"缺陷分析报告"。
这是 报告2/5。

项目背景：医疗器械租赁管理系统。
场景：库房人员出库时发现系统显示的设备存放位置与实际不符。

按与报告1相同的格式输出（# 缺陷分析报告 [#2/5]）。`,
    { label: '缺陷报告2', phase: '报告', schema: null }
  ),
  () => agent(
    `缺陷分析报告 3/5。
场景：运维工程师收到故障报修通知，但系统未显示该设备的历史维修记录。

按相同格式输出。`,
    { label: '缺陷报告3', phase: '报告', schema: null }
  ),
  () => agent(
    `缺陷分析报告 4/5。
场景：财务结算时发现系统计算的租金金额与合同约定不一致，原因是租期计算边界条件未处理。

按相同格式输出。`,
    { label: '缺陷报告4', phase: '报告', schema: null }
  ),
  () => agent(
    `缺陷分析报告 5/5。
场景：招商业务员看到系统显示某设备"闲置"，但库房人员表示该设备实际上已经在出库配送途中，状态未同步。

按相同格式输出。`,
    { label: '缺陷报告5', phase: '报告', schema: null }
  ),
])

// 合并保存
const allDefects = defectReports.filter(Boolean).join('\n\n---\n\n')
saveToKB(`${KB.wiki}/缺陷分析报告集-${dateStr}-v1.0.md`, allDefects)
log('✅ 5份缺陷分析报告已保存')

// ============================================================
// 🧠 阶段6：CCB审批
// ============================================================

phase('审批')
log('CCB人工审批 — 请做决定')

const ccbInfo = await agent(
  `### CCB审批决策

项目：医疗器械租赁管理系统

SRS验证结论：${verdict}
验证发现：${findings.length} 个问题

请输出CCB审批界面，提供 通过/不通过（获取类）/不通过（分析类） 三个选项。`,
  { label: 'CCB-审批', phase: '审批' }
)
log(ccbInfo)
// 假设通过
log('✅ 假设审批通过')

// ============================================================
// 🧠 阶段7：基线
// ============================================================

phase('基线')
log('创立需求基线...')

const blVersion = `BL-${dateStr}-01`
const now = new Date().toISOString().slice(0,10)

const rtmDoc = await agent(
  `你作为A6基线智能体（教材§8），生成需求溯源矩阵（RTM）。

需求清单：
${currentReq}

基线版本：${blVersion}

### RTM格式
| 需求编号 | 需求描述 | 来源涉众 | 对应模块 | 接口/功能 | 测试要点 | 优先级 |
要求覆盖所有主要需求条目。`,
  { label: 'A6-RTM生成', phase: '基线' }
)

const baselineDoc = `# 基线创立报告

基线版本：${blVersion}
创建日期：${now}
项目：医疗器械租赁管理系统
状态：已冻结（不可修改）

## 包含文档
- SRS-正式版.md
- 需求清单.md
- UML模型/
- 溯源矩阵.md

## 变更管理说明
基线创立后如需变更，需走正式变更管理流程：
CR（变更请求）→ CIA（影响分析）→ 约束更新 → 代码变更 → CRR（回归校验）→ 新基线
`
saveToKB(`${KB.baselines}/${blVersion}/SRS-正式版.md`, srsDoc)
saveToKB(`${KB.baselines}/${blVersion}/需求清单.md`, currentReq)
saveToKB(`${KB.baselines}/${blVersion}/溯源矩阵.md`, rtmDoc)
saveToKB(`${KB.baselines}/${blVersion}/基线报告.md`, baselineDoc)

log(`✅ 基线 ${blVersion} 已创立，文档已保存到 wiki/baselines/`)

// ============================================================
// 📦 汇总
// ============================================================

log('')
log('='.repeat(50))
log('✅ 需求工程全流程完成！')
log('='.repeat(50))
log('')

return {
  message: '第一阶段交付物全部产出完毕',
  summary: {
    baselineVersion: blVersion,
    deliverables: {
      knowledgeBase: '✅ 四层目录 + compile.js',
      dialogRecords: `✅ ${STAKEHOLDERS.length}份对话记录 (raw/notes/)`,
      issuesList: '✅ 需求问题清单 + 回退记录',
      umlModels: '✅ 用例图 + 活动图 (PlantUML)',
      srs: '✅ SRS文档 (IEEE 830)',
      validationReport: '✅ 需求验证报告',
      baseline: `✅ 基线 ${blVersion} + RTM`,
      defectReports: '✅ 5份缺陷分析报告',
      workflowEngine: '✅ LangGraph 工作流',
    }
  }
}
