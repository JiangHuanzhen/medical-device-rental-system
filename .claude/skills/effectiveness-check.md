---
name: 有效性校验
description: 检查工程产物是否可有效驱动AI代码生成——约束可判定、产物信息充分、场景覆盖完整
type: quality-check
inputs:
  - asd_path: wiki/design/spec/ASD-*.md
  - mds_path: wiki/design/spec/MDS-*.md
  - tlcd_path: wiki/design/contracts/TLCD-*.md
  - oas_path: wiki/design/contracts/openapi-*.yaml
output: wiki/design/reports/effectiveness-{date}.md
---

# 有效性校验

## 操作定义
评估四种工程产物是否能在实际的代码生成场景中为 AI 提供足够的约束信息。完备性/正确性/一致性校验回答"产物本身有没有问题"，有效性校验回答"产物能不能用"——即这些约束是否足以让 AI 在不同模块的生成任务中产出正确的代码。

## 参数说明
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ASD_PATH` | `wiki/design/spec/ASD-*.md` | 架构风格声明 |
| `MDS_PATH` | `wiki/design/spec/MDS-*.md` | 模块划分方案 |
| `TLCD_PATH` | `wiki/design/contracts/TLCD-*.md` | 三层约束设计 |
| `OAS_PATH` | `wiki/design/contracts/openapi-*.yaml` | OpenAPI 接口契约 |

## 执行步骤

### Step 1: 约束可判定性检查
逐条检查每条约束是否满足"可判定"标准：
- 包含明确的判断条件（如"controller 包不得 import repository 包"）
- 不依赖主观判断（"代码应该清晰易读" — 不可判定）
- 可通过 CodeGraph RCR 或 grep/静态分析自动校验
- 标注每条约束的可判定性：✅可判定 / ⚠️部分可判定 / ❌不可判定

### Step 2: 代码生成场景覆盖测试
选择三个典型代码生成场景，检查产物是否提供了足够的约束信息：
- **场景A（跨模块调用）**: 生成 OrderService.createOrder 时，ASD+TLCD 是否约束了它可以调用哪些模块？OAS 是否定义了请求/响应格式？
- **场景B（数据访问）**: 生成 CellRepository 时，TLCD 是否约束了禁止包含业务逻辑？ASD 是否约束了调用方向？
- **场景C（异常处理）**: 生成全局异常处理器时，C-CODE 是否约束了异常处理方式？OAS 是否定义了错误响应格式？

### Step 3: 信息充分性评估
- 对于 LLM 代码生成场景，产物是否提供了下限（必须做什么）和上限（不能做什么）？
- 产物之间的推导关系是否足够紧密（ASD→MDS→DTS→TLCD→OAS 链条是否不断裂）？
- 缺失哪些约束会导致 AI 在特定场景下"自由发挥"？

## 输出格式
```markdown
# 有效性校验报告

## 约束可判定性
| 约束编号 | 可判定性 | 问题 | 建议 |
|---------|---------|------|------|

## 场景覆盖测试
### 场景A: 跨模块调用
- 是否充分: [是/否]
- 缺失约束: [列出]

### 场景B: 数据访问
### 场景C: 异常处理

## 信息充分性评估
- ASD→TLCD 推导链: [完整/部分缺失/断裂]
- 职责下限 vs 禁止上限: [都有/只有下限/只有上限]

## 总体评估
```

## 质量标准
- 可判定约束占比 ≥ 90%
- 三个代码生成场景的信息充分性 ≥ 80%
- 无"断裂"的推导链——产物间的依赖关系可追溯

## 验证步骤
1. 对标记为 ⚠️ 或 ❌ 的约束，确认判定确实需要主观判断
2. 确认三个场景的选择覆盖了 Controller/Service/Repository 三层
3. 验证信息充分性评估不是凭感觉，而是逐项对照的结果
