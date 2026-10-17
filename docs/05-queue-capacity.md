# 队列满时，怎样可靠地拒绝新任务

提交成功比处理成功早。假如处理速度跟不上提交速度，只限制单次请求大小仍会积累大量排队任务。这个版本给每个租户和整个数据库设置待处理任务数量上限，让调用方收到明确的 429。

## 先复现

在已安装本项目测试依赖的虚拟环境中运行：

```bash
python scripts/capacity_demo.py
```

脚本启动临时 SQLite 和真实本机 HTTP 服务，暂停后台消费，将每租户容量设为 2、全局容量设为 3：

| 操作 | 响应 | 原因 |
| --- | --- | --- |
| Alice 提交两个不同 key | 202、202 | Alice 占用两个待处理位置 |
| Alice 提交第三个 key | 429 | 达到租户上限 |
| Bob 提交第一个 key | 202 | 仍有一个全局位置 |
| Bob 提交第二个 key | 429 | Bob 尚未满，但全局已满 |
| Alice 用原 key 重发原内容 | 202，原任务 ID | 读取已有任务，不增加占用 |
| Alice 用原 key 提交不同内容 | 409 | 幂等冲突优先于容量判断 |
| Alice 取消一个任务，再提交先前被拒的 key | 200、202 | 取消释放容量；被拒 key 未登记 |

脚本退出后清理临时服务和数据库。回归测试还让 8 个独立存储连接并发提交，验证两种上限都不会因竞争被越过。

## 配置与计数

| 配置 | 默认值 | 计数范围 |
| --- | --- | --- |
| `KTA_MAX_PENDING_PER_TENANT` | 100 | 当前租户的 `queued` + `running` |
| `KTA_MAX_PENDING_TOTAL` | 1000 | 整个数据库的 `queued` + `running` |

两个值都须为正整数；较小的限制先起作用。正常服务读取环境变量，`--demo` 使用固定默认值。Python 调用方可以配置同名小写的 `Settings` / `Store` 参数；向 `create_app` 显式传入 `store` 时采用这个存储对象的限制。所有向同一个数据库提交任务的进程应使用相同限制。

等待指数退避的重试仍占用位置。成功、终态失败和取消后不再占用。过期运行任务在 worker 执行恢复检查前仍属于 `running`；降低上限不会删除既有任务，而是暂停接收新的任务直至有空位。终态任务的幂等记录仍保留并可重放。

## 为什么把检查放进事务

如果先读出“剩余一个位置”，离开事务后再插入，并发请求可能都看到同一个空位。这里先取得 `BEGIN IMMEDIATE` 写锁，在同一事务内按以下顺序处理：

1. 查当前租户和幂等 key；相同内容返回原任务，不同内容返回 409。
2. 对新 key 统计租户与全局待处理数量；超限抛出容量异常并回滚。
3. 插入任务和首条事件，再一起提交。

取消、发布和失败状态也使用写事务，所以容量释放与新增任务通过同一数据库锁排序。新增的 `(tenant,state)` 索引可在原 schema version 1 数据库打开时创建，不重写任务。

429 的响应体统一为 `pending task capacity reached; retry later`，不附带其他租户的标识或任务数量。`Retry-After: 1` 只是建议客户端稍后重试，不能保证那个时刻一定有空位。客户端应保留原幂等 key 和内容，采用有限次数的退避并加入抖动，避免持续紧密重试。存储锁超时仍返回 503。

这项限制不决定下一个任务归谁、不限制每秒请求数，也不清理历史任务、事件或文档。它不能单独限制磁盘占用或构成完整的滥用防护；这些能力需要分别实现和验证。

## English

Pending admission defaults to 100 queued/running tasks per tenant and 1000 across the database. Scheduled retries still count; success, terminal failure and cancellation free a slot. Expired ownership remains pending until worker recovery updates its state. Lowering a limit does not evict existing work. Completed requests remain replayable.

Set positive integers `KTA_MAX_PENDING_PER_TENANT` and `KTA_MAX_PENDING_TOTAL` for the normal server; demo mode uses fixed defaults. Python callers can set the matching lowercase `Settings` or `Store` fields. An explicitly injected Store owns its limits. Configure every submitting process consistently.

Idempotency lookup, capacity counts and insertion share one SQLite write transaction. Independent concurrent writers cannot overbook that transaction's configured bounds. Replays return the original 202 response, changed payloads return 409, and rejected new keys create no task/event. New submissions at capacity return the same generic 429 message and advisory `Retry-After: 1`, without tenant names or counts. Database contention can still produce 503.

Run `python scripts/capacity_demo.py` for a real HTTP example with tenant limit 2 and total limit 3. Regression tests also cover concurrent connections, delayed retries, configuration validation and terminal-state release. This does not implement fair scheduling, per-second rate limits, or historical storage retention.
