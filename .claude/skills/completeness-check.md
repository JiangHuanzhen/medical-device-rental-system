---
name: 完备性校验
description: 检查四种设计产物的覆盖完整性——是否有遗漏的功能需求、缺失的模块职责、未覆盖的架构决策
type: quality-check
inputs:
  - asd_path: wiki/design/spec/ASD-*.md
  - mds_path: wiki/design/spec/MDS-*.md
  - dts_path: wiki/design/spec/DTS-*.md
  - adr_dir: wiki/design/adr/
  - srs_path: wiki/baselines/BL-*/SRS-正式版.md
output: wiki/design/reports/completeness-{date}.md
---

# 完备性校验

## 操作定义
检查四种核心工程产物（ASD、MDS、DTS、ADR）对 SRS 需求基线的覆盖完整性。确保每条功能需求在模块划分中有对应模块承担，每个模块的每项职责在依赖拓扑中有合法依赖边支撑，每个关键架构决策点有对应的 ADR 记录。

## 参数说明
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ASD_PATH` | `wiki/design/spec/ASD-*.md` | 架构风格声明路径 |
| `MDS_PATH` | `wiki/design/spec/MDS-*.md` | 模块划分方案路径 |
| `DTS_PATH` | `wiki/design/spec/DTS-*.md` | 依赖拓扑规范路径 |
| `ADR_DIR` | `wiki/design/adr/` | ADR 决策记录目录 |
| `SRS_PATH` | `wiki/baselines/BL-*/SRS-正式版.md` | SRS 基线路径 |

## 执行步骤

### Step 1: 读取输入
从知识库加载四种设计产物和 SRS 基线。

### Step 2: 需求-模块映射检查
逐条遍历 SRS 的功能需求条目，检查每条需求是否在 MDS 中有明确对应的模块承担。
- 匹配规则：需求描述的动词短语 → MDS 模块的 `interfaces` 字段
- 未匹配的需求标记为「需求遗漏」

### Step 3: 职责-依赖支撑检查
对 MDS 中每个模块的每项职责，检查 DTS 中是否有合法的依赖边支撑该职责的执行。
- 如 OrderService 的 `createOrder` 需要调用 `CellService.allocateCell`，DTS 中应有对应的 sync 边
- 缺少依赖边的标记为「依赖缺失」

### Step 4: 架构决策覆盖检查
检查以下关键决策点是否都有对应的 ADR：
- 架构风格选型 → ADR-001
- 技术栈选型 → ADR-002
- 数据库选型 → ADR-003
- 部署架构 → ADR-004
- 变更决策（如有）→ ADR-005
- 遗漏的决策点标记为「决策遗漏」

## 输出格式
```markdown
# 完备性校验报告

## 检查概要
- 检查时间: {timestamp}
- SRS 功能需求总数: N
- MDS 模块总数: M
- ADR 总数: K

## 需求遗漏
| 需求ID | 需求描述 | 严重程度 | 建议 |
|--------|---------|---------|------|

## 依赖缺失
| 模块 | 职责 | 缺少的依赖边 | 严重程度 | 建议 |
|------|------|------------|---------|------|

## 决策遗漏
| 决策点 | 严重程度 | 建议 |
|--------|---------|------|

## 总体评估
- 完备性得分: X/100
- 是否通过: [通过/条件通过/不通过]
```

## 质量标准
- 所有「高」严重程度的遗漏必须在进入代码生成前修复
- 「中」严重程度可在代码生成阶段补充
- SRS 功能需求覆盖率 ≥ 95%
- 关键架构决策点 ADR 覆盖率 = 100%

## 验证步骤
1. 确认输出的 Markdown 表格无缺失列
2. 检查每条需求的匹配结果是否合理（非机械化字符串匹配）
3. 确认完备性得分计算逻辑正确
