![Knowledge Task API](assets/banner.svg)

# Knowledge Task API

[![Tests](https://github.com/lashonbakker-crypto/knowledge-task-api/actions/workflows/ci.yml/badge.svg)](https://github.com/lashonbakker-crypto/knowledge-task-api/actions)

**面向知识检索与 Agent 工具层的可靠任务后端。**

[English](README.en.md) · [架构](docs/architecture.md) · [24 个月路线](docs/roadmap.md) · [面试与简历](docs/interview.md) · [验证记录](docs/validation.md)

第一版解决一个具体问题：把文本作为后台任务持久化入库，出错后按规则重试，用词法检索返回属于当前用户的原文片段。它已经是可运行的后端；模型生成、语义向量检索和完整 Agent 编排是后续里程碑。

技术栈：**Python 3.11+ / FastAPI / SQLite WAL / FTS5 / pytest**。

| 已实现 | 用途与证据 |
| --- | --- |
| Bearer token 与租户资源访问 | 文档、任务、检索和事件都检查归属；普通用户不能读取管理员统计 |
| 并发幂等请求 | 同租户同 key 与相同内容返回同任务；不同内容返回 409 |
| 持久化任务与重试 | 排队、执行、成功、失败、取消；有限次数与指数退避 |
| 心跳续租 | 长处理期间定期延长有效租约，失租后拒绝发布；[原理与练习](docs/02-lease-heartbeats.md) |
| 租约与 fencing token | 过期或被取消的 worker 不能写回文档；发布与索引在一个事务中 |
| 崩溃恢复 | 进程退出后任务仍保留；过期租约可重新领取 |
| 检索与来源片段 | FTS5 词法匹配，返回文档 ID、来源任务、原文片段 |
| 事件回放 / SSE | 数据库保存有序事件；支持游标与 `Last-Event-ID` |
| SSE 终态竞态修复 | 结束流前交付终态事件；覆盖首次连接、续读和多页事件，[复现与练习](docs/01-sse-terminal-events.md) |
| 输入限制与日志 | 写请求先检查真实字节数；日志包含请求 ID、状态和耗时 |

## 只读 Agent 工具适配

新增[只读工具适配器](docs/03-agent-tool-bridge.md)：把现有搜索、原文读取封装成 `search_knowledge` 和 `read_knowledge_source` 两个可供外部模型选择的函数描述，后端仍执行租户鉴权，工具代码只发 GET，不允许模型指定 URL、写入或携带 token。`python scripts/agent_bridge_demo.py` 使用真实 SQLite 和测试 HTTP 客户端演示，无需调用任何模型。**这不是完整 Agent。**

## 本地运行

```bash
git clone https://github.com/lashonbakker-crypto/knowledge-task-api.git
cd knowledge-task-api
python -m venv .venv
source .venv/bin/activate
python -m pip install -c requirements.lock ".[test]"
python -m knowledge_task_api serve --demo
```

Windows PowerShell 用 `.\.venv\Scripts\Activate.ps1` 激活环境。`--demo` 使用公开的示例 token，只允许监听 `127.0.0.1`。打开 `http://127.0.0.1:8080/docs` 可以使用交互式接口文档；Alice 示例 token 为 `demo-alice-local-only`。示例使用本机 SQLite，不需要模型账户或外部数据库服务。

要验证完整流程，可先关闭上面的服务，在同一个虚拟环境运行：

```bash
python scripts/demo.py
python scripts/lease_demo.py
```

脚本启动独立的真实 HTTP 服务，验证提交、幂等、冲突、后台入库、检索、跨租户拒绝及 SSE，然后关闭服务。临时数据库会自动清理。第二个脚本用受控时钟和真实心跳线程验证超过原始租约期限的任务、过期接管与取消。

## 提交一份文档

```bash
curl -X POST http://127.0.0.1:8080/v1/documents \
  -H "Authorization: Bearer demo-alice-local-only" \
  -H "Idempotency-Key: example-document-01" \
  -H "Content-Type: application/json" \
  -d '{"title":"Retry notes","text":"Durable leases allow retry after a worker crash."}'
```

响应是 202，并带任务 ID 和 `Location`。用该 ID 查询 `/v1/tasks/ID`，成功后用 `result_document_id` 获取原文，或调用 `/v1/search?q=durable%20leases`。Shell 示例是 Bash 语法；Windows 可直接使用 OpenAPI 文档或 `scripts/demo.py`。

## 接口

| 方法与路径 | 行为 |
| --- | --- |
| `GET /health` | 数据库可读检查与版本，公开 |
| `POST /v1/documents` | 提交索引任务，要求 `Idempotency-Key` |
| `GET /v1/tasks/{id}` | 查询自己的任务 |
| `POST /v1/tasks/{id}/cancel` | 取消排队 / 运行任务；完成或失败后返回 409 |
| `GET /v1/tasks/{id}/events` | 按 `after` 游标读取持久化事件 |
| `GET /v1/tasks/{id}/stream` | SSE，支持断线后的游标回放 |
| `GET /v1/documents/{id}` | 获取自己的文档原文 |
| `GET /v1/search?q=...` | 当前租户的词法 AND 检索 |
| `GET /v1/stats` | 当前租户的文档与任务计数 |
| `GET /v1/admin/stats` | 仅管理员可用的总体计数 |

常见状态：401 未认证；403 无管理权限；404 资源不存在或不属于自己；409 幂等冲突或非法状态变化；413 写请求过大；422 参数错误；503 存储暂时不可用。

## 测试

```bash
python -m pytest
python scripts/demo.py
python scripts/lease_demo.py
```

测试包含真实 SQLite 事务、并发请求、租约边界、崩溃恢复、发布回滚、访问边界和 HTTP 接口。[验证记录](docs/validation.md)只记录实际执行的环境与结果。GitHub CI 是否通过以 [Actions](https://github.com/lashonbakker-crypto/knowledge-task-api/actions) 为准。

## 正常配置与边界

常规启动必须设置 `KTA_KEYS_JSON`，否则拒绝启动。格式见 [.env.example](.env.example)；应用读取进程环境，不自动加载该文件。可用 `KTA_DATABASE` 选择数据库路径。部署前使用独立访问 token 和 HTTPS。当前 token 配置由运维管理，尚无注册、轮换或 OAuth 服务。

SQLite 适合这一阶段的单机、小规模验证。执行是**至少一次**的尝试语义，旧任务可能重复执行；fencing 与事务限制结果发布，不能据此声称通用的 exactly-once。文档最大 64000 字符，JSON 写请求最大 256 KiB；查询最多 12 个词，返回最多 20 条。心跳默认每个租约周期的三分之一续租，取消或失租仍会拒绝发布；处理器扩展见[续租教程](docs/02-lease-heartbeats.md)。SSE 单次最多 30 秒，客户端可带游标重连。

词法搜索按 Unicode61 分词，中文连续文本的效果有限；它不理解语义。FTS 排序统计共享同一数据库，资源查询有租户过滤，但没有提供强统计隔离。限流、配额、PostgreSQL、任务公平性、向量检索和模型调用均列在后续路线中。本版不要承载公开不受限的大规模服务。

## 学习与贡献

从 [24 个月路线](docs/roadmap.md) 选择一个具体里程碑，再看对应的源码和故障测试。起始版本为 AI 辅助创建，后续请在 [DEVLOG 模板](docs/devlog-template.md) 记录你实际改过、测过、讲得清的内容。简历表述应对应你的真实参与与证据。

原创实现，使用 [MIT License](LICENSE)。官方参考资料见 [references](docs/references.md)。
