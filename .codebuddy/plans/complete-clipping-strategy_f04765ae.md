---
name: complete-clipping-strategy
overview: 依据《切片内容策略》补齐当前代码中已明确的未完成链路，更新文档状态，验证全仓 worker 测试，并明确仍需真实数据才能完成的实验与校准项。
todos:
  - id: trace-consumers
    content: 使用 [subagent:code-explorer] 和 [skill:lsp-code-analysis] 追踪标题、封面、模板字段的完整消费链路
    status: completed
  - id: add-compatible-title-flow
    content: 完善 publish_pack、publish.py 与后台的 title_options 兼容消费和安全回退
    status: completed
    dependencies:
      - trace-consumers
  - id: connect-cute-template
    content: 将 main_value 与证据规则接入 autoedit 萌点模板选择并保留人工覆盖
    status: completed
    dependencies:
      - trace-consumers
  - id: add-regression-tests
    content: 补充标题兼容、模板选择、默认回退和旧流程保护测试
    status: completed
    dependencies:
      - add-compatible-title-flow
      - connect-cute-template
  - id: sync-strategy-document
    content: 更新切片内容策略文档中的实现状态、验证状态和后续待办
    status: completed
    dependencies:
      - add-compatible-title-flow
      - connect-cute-template
  - id: run-full-validation
    content: 运行完整 worker 测试与受影响文件诊断，记录剩余真实数据验证缺口
    status: completed
    dependencies:
      - add-regression-tests
      - sync-strategy-document
---

## 用户需求

继续依据 `docs/切片内容策略.md` 完善当前项目，优先完成不依赖真实线上数据即可落地的工程任务，并保持现有未提交修改不被覆盖。

## Product Overview

完善切片精排、标题发布、萌点自动编辑和策略文档的实现闭环；保留旧版字段、评分和发布行为，同时让新五维价值策略能够安全进入影子验证流程。

## Core Features

- 核对并补齐 `title_options` 在发布脚本、后台和相关页面中的兼容消费。
- 将 `main_value` 与价值证据接入萌点模板选择，保留默认模板和人工切换能力。
- 更新策略文档中的“当前实现核对”，明确已完成、部分完成、待验证和待开发事项。
- 执行完整 `worker` 测试集，修复本次改动引入的回归。
- 不伪造实现非峰值召回、语义召回、人工时间标记、权重校准、固定稿龄快照和真实发布实验；将其保留为明确的后续任务。

## Tech Stack Selection

- 复用现有 Python worker、YAML 配置、Markdown prompt、pytest 测试体系。
- 复用现有发布脚本、`autoedit`、后台 Vue 页面和既有数据结构。
- 不引入新的框架、数据库或外部依赖，避免扩大修改范围。

## Implementation Approach

先通过代码语义分析和调用链搜索确认 `autoedit`、`publish_pack.py`、`publish.py`、后台编辑页面及测试对标题和模板字段的实际消费方式。随后采用兼容适配：优先读取合法的结构化 `title_options`，生成旧链路仍可使用的字符串 `title_candidates`；萌点模板只在 `main_value`、维度值和证据满足最低条件时自动建议，否则继续使用现有默认模板，并允许人工覆盖。

新路径保持影子运行，不改变旧版 `final`、`title_candidates` 和默认发布行为。结构化标题选择应为线性遍历，候选数量通常很小，时间复杂度为 O(n)，不增加明显性能成本。模板选择只进行字段校验和少量规则判断，不调用额外模型，避免增加运行时成本。

## Implementation Notes

- 修改前重新读取所有目标文件，保留工作区已有用户修改。
- `title_options` 缺失、格式错误或证据不完整时回退到 `title_candidates`，不能阻断旧发布流程。
- 不把 `null`、缺失维度或低置信度强行解释为萌点成立。
- 复用现有日志和错误处理方式，日志中不输出完整提示词、用户隐私或大段候选内容。
- 文档状态必须与当前代码一致，区分“已实现”和“已验证”。
- 完成后运行完整 worker 测试，并单独复核受影响模块的静态诊断。
- 不执行 Git 提交，不回退其他未提交文件。

## Architecture Design

数据流保持为：

精排结果
→ 兼容字段归一化
→ 发布标题候选与封面文本消费
→ `autoedit` 模板选择
→ 人工复核或旧默认流程
→ 现有发布与渲染链路

兼容层只负责字段转换和策略选择，不改变原有评分、发布和渲染职责。真实数据校准、固定稿龄统计与新增召回来源继续属于后续实验和数据模块，不在本轮伪造实现。

## Directory Structure

本轮预计修改或影响以下文件：

- `worker/scripts/publish_pack.py` `[MODIFY]`
- 兼容读取 `title_options`，将合法结构化标题转换为发布链路可用的标题候选。
- 保留旧 `title_candidates` 的输出格式和人工编辑行为。
- 对无效结构化数据执行安全回退。

- `worker/clipper/publish.py` `[MODIFY]`
- 核对封面文字与结构化标题的消费关系。
- 优先使用已验证的 `cover_text`，避免将标题字段错误当作事实证据。

- `worker/scripts/autoedit.py` `[MODIFY]`
- 接入萌点模板的自动建议或显式模板选择。
- 保留现有默认模板、命令行参数和人工选择行为。
- 对 `main_value`、价值维度、证据和低置信度情况执行保守回退。

- `worker/clipper/llm.py` `[MODIFY]`（仅在现有模板加载接口需要扩展时）
- 复用现有 prompt 加载机制支持 `edit_cute_v1`。
- 不改变已有 prompt 的变量契约。

- `admin/src/views/Editor.vue` `[MODIFY]`
- 核对并兼容展示结构化标题候选。
- 保留字符串标题列表、人工创建标题和现有发布编辑流程。

- `admin/src/components/ViewerReview.vue` `[MODIFY]`（仅在当前审核视图需要展示新字段时）
- 展示主价值或标题依据时保持可读、可选和非阻断。
- 不把模型判断显示为已验证事实。

- `worker/tests/test_publish.py` `[MODIFY]`
- 增加结构化标题存在、缺失、非法和回退场景测试。

- `worker/tests/test_variants.py` 或相关 `autoedit` 测试文件 `[MODIFY]`
- 增加萌点模板自动选择、默认回退和人工覆盖测试。

- `docs/切片内容策略.md` `[MODIFY]`
- 更新第八、九节及“当前实现核对”。
- 明确五维 schema、影子排序、边界修正和模板文件的完成状态。
- 保留非峰值召回、固定稿龄快照、权重校准和真实实验为待开发或待验证事项。

- `config/kiya.yaml` `[VERIFY]`
- 仅核对现有 `value_pick_score`、`value_screen_weight` 和 autoedit 配置是否足以支持兼容链路，避免无必要配置改动。

- `prompts/rerank_v4.md` `[VERIFY]`
- 核对 `title_options`、`cover_text`、`main_value` 和证据字段与消费端契约一致。

- `prompts/edit_cute_v1.md` `[VERIFY]`
- 核对模板变量、输出结构与现有编辑脚本加载方式一致。

## Agent Extensions

### SubAgent

- **code-explorer**
- **Purpose**：跨目录追踪 `autoedit`、发布脚本、后台页面、prompt 加载和测试之间的调用链，确认所有实际消费者。
- **Expected outcome**：输出准确的修改文件、字段契约、回退路径和测试影响范围，避免遗漏下游消费者。

### Skill

- **lsp-code-analysis**
- **Purpose**：对 Python 和 Vue 代码进行符号、引用和调用关系分析，确认函数定义、调用点及可安全扩展的位置。
- **Expected outcome**：获得可验证的调用链和局部修改边界，降低兼容字段接入造成回归的风险。