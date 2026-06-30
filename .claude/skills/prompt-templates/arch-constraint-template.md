---
name: 架构约束提示词模板
description: 驱动AI代码生成的分层约束提示词模板，适配分层架构项目
type: prompt-template
reusable: true
parameters:
  - PROJECT_NAME: 项目名称
  - PACKAGE_ROOT: 根包名
  - MODULE_LIST: 模块列表
---

# 架构约束提示词模板

## 适用场景
适用于采用分层架构（Controller→Service→Repository）的 Spring Boot 项目，用于驱动AI在约束边界内逐层生成代码。

## 使用方法
将以下模板中的 `{PARAM}` 替换为项目实际值后，注入AI代码生成的 prompt 前缀。

## 模板

```
你是 {PROJECT_NAME} 的开发AI。代码必须严格遵守以下设计约束，这些约束来自已评审的架构决策，不可自行修改或忽略。

## C-ARCH 架构层约束（全局禁止规则 — 所有模块强制遵守）
{C-ARCH_CONSTRAINTS}

## C-CODE 代码层约束（全局编码禁止规则 — 所有模块强制遵守）
{C-CODE_CONSTRAINTS}

## C-MOD 模块层约束（模块禁止规则 + 依赖白名单 — 按模块变化）
{C-MOD_CONSTRAINTS}

## 接口契约 (OpenAPI)
{OAS_YAML}

## 模块划分方案 (MDS)
{MDS_JSON}

## 架构决策记录 (ADR)
{ADR_SUMMARY}

## 已有代码结构 (实现记忆)
{CODE_STRUCTURE}

## 当前任务
{TASK_DESCRIPTION}
```

## 参数说明
| 参数 | 来源 | 说明 |
|------|------|------|
| `{PROJECT_NAME}` | 项目配置 | 如「医疗器械租赁管理系统」 |
| `{C-ARCH_CONSTRAINTS}` | `wiki/design/spec/ASD-*.md` + `wiki/design/contracts/TLCD-*.md` | 用 `_extract_by_prefix` 提取 C-ARCH/L-xx 约束 |
| `{C-CODE_CONSTRAINTS}` | `wiki/design/contracts/TLCD-*.md` | 用 `_extract_by_prefix` 提取 C-CODE-xxx 约束 |
| `{C-MOD_CONSTRAINTS}` | `wiki/design/contracts/TLCD-*.md` | 用 `_extract_by_prefix` 提取 C-MOD-xxx 约束 |
| `{OAS_YAML}` | `wiki/design/contracts/openapi-*.yaml` | OpenAPI 3.0 接口契约 |
| `{MDS_JSON}` | `wiki/design/spec/MDS-*.md` | 模块划分方案 JSON |
| `{ADR_SUMMARY}` | `wiki/design/adr/ADR-*.md` | ADR 决策摘要 |
| `{CODE_STRUCTURE}` | `project/src/` | 已有代码结构，用 `_summarize_code_structure` 提取 |

## 缓存优化说明
- **固定前缀**: `C-ARCH` + `C-CODE` 段落跨模块不变，置于 prompt 最前以最大化缓存命中
- **变动后缀**: `C-MOD` + `OAS` 段落实按模块变化，置于 prompt 后部
- 实测效果: 统一约束措辞和固定前缀位置后，缓存命中率从 ~62% 提升到 ~76%

## 不适用场景
- 非分层架构项目（事件驱动/微服务需调整前置约束段落）
- 非 Java/Spring Boot 技术栈（需替换 C-CODE 中的语言特定约束）
