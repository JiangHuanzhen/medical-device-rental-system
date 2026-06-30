---
name: 一致性校验
description: 检查四种设计产物之间的交叉一致性——ASD vs MDS、ASD vs DTS、MDS vs DTS、ADR vs 其他产物
type: quality-check
inputs:
  - asd_path: wiki/design/spec/ASD-*.md
  - mds_path: wiki/design/spec/MDS-*.md
  - dts_path: wiki/design/spec/DTS-*.md
  - adr_dir: wiki/design/adr/
  - tlcd_path: wiki/design/contracts/TLCD-*.md
output: wiki/design/reports/consistency-{date}.md
---

# 一致性校验

## 操作定义
执行四种工程产物之间的四组交叉比对，检测产物之间的矛盾。一致性校验的核心问题是：这些产物共同描述的是同一套设计吗？还是各说各话？

## 参数说明
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ASD_PATH` | `wiki/design/spec/ASD-*.md` | 架构风格声明 |
| `MDS_PATH` | `wiki/design/spec/MDS-*.md` | 模块划分方案 |
| `DTS_PATH` | `wiki/design/spec/DTS-*.md` | 依赖拓扑规范 |
| `ADR_DIR` | `wiki/design/adr/` | ADR 决策记录目录 |
| `TLCD_PATH` | `wiki/design/contracts/TLCD-*.md` | 三层约束设计 |

## 执行步骤

### 交叉比对 1: ASD vs MDS
- ASD 中定义的层次/组件 → MDS 中每个模块是否明确标注了所属层次？
- ASD 中的分层规则 → MDS 的模块职责划分是否与分层一致？
- 检查 MDS 中是否存在与 ASD 风格冲突的模块组织方式

### 交叉比对 2: ASD vs DTS
- ASD 中的分层约束（如 L-01: Controller→Repository 禁止）→ DTS 的 `forbidden_dependencies` 是否包含对应条目？
- ASD 中的通信机制限制（如同步/异步）→ DTS 中边的 `type` 是否一致？
- 检查 DTS 中是否存在与 ASD 约束矛盾的实际依赖

### 交叉比对 3: MDS vs DTS
- MDS 中定义的模块 → DTS 中是否每个模块都有对应的节点？
- MDS 中的 `depends_on` → DTS 中是否有对应的依赖边？
- DTS 中的 `forbidden_dependencies` → 是否与 MDS 的模块职责边界一致？

### 交叉比对 4: ADR vs 其他产物
- ADR-001 中的架构选型结论 → ASD 中的风格声明是否一致？
- ADR-002~4 中的技术栈/数据库/部署决策 → MDS/DTS/TLCD 中是否体现？
- ADR-005 中的变更决策 → 其他产物是否已同步更新？

## 输出格式
```markdown
# 一致性校验报告

## 矛盾清单
| 编号 | 交叉组 | 产物A | 产物B | 矛盾描述 | 严重程度 | 建议 |
|------|--------|-------|-------|---------|---------|------|

## 各交叉组统计
| 交叉组 | 检查项数 | 矛盾数 | 一致率 |
|--------|---------|--------|--------|
| ASD vs MDS | | | |
| ASD vs DTS | | | |
| MDS vs DTS | | | |
| ADR vs 其他 | | | |

## 总体评估
```

## 质量标准
- 四组交叉比对无阻塞性矛盾（严重程度=阻塞）
- 重要矛盾数 ≤ 2
- 各组一致率 ≥ 90%

## 验证步骤
1. 每条矛盾必须同时引用两种产物的原文（文件路径 + 行号/段落号）
2. 确认矛盾分类（阻塞性/重要/建议）合理
3. 抽查一组交叉比对确认未被遗漏的矛盾
