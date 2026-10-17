# 续租之后，还要限制处理时间 / Processing budgets

先运行 `python scripts/budget_demo.py`，再读本文。示例使用真实 SQLite 和受控的处理时钟，直接到达期限；它不让你等待五分钟，也不是性能测试。

## 为什么租约还不够

租约回答“现在谁有资格发布结果”。心跳说明进程还活着，但一个活着的回调也可能卡住，一直续租。处理预算回答“这一轮已经用了多久”。默认每次领取后的处理预算为 300 秒，使用单调时钟，独立于数据库中的租约墙上时钟。

| 情况 | 当前行为 |
|---|---|
| 正常完成，仍有有效租约且预算未耗尽 | 在带 fencing 的事务中发布文档与索引 |
| 有效租约的处理预算耗尽 | 停止续租、撤销 token，记录 failed / processing_timeout |
| 超时回调后来返回 | 旧租约不能续租或发布，不自动重试这个超时任务 |
| 租约已经过期、取消或被接管 | 超时操作不能修改新的 owner；原有租约恢复规则继续适用 |
| 主动停止 Worker | 协作回调可观察停止；有效尝试按已有退避规则重新排队 |

为什么发布时要再查一遍？处理器在 t=299.9 返回，可能还要等待 SQLite 写锁。即使前一次检查通过，在 t=300 取得锁时也不能再发布。`complete` 在拿到写锁、确认租约之后重新检查单调时钟，等于截止时间也会拒绝。

超时写入与 token 撤销在同一事务中，持久化 failed 事件仍可经 JSON / SSE 回放。没有新增数据库字段或迁移。普通临时存储错误仍沿用有限重试；处理超时不会盲目重复。先诊断原因、调整处理器或预算，再用**新的幂等 key**提交；原任务记录保留。

## 配置

正常 `serve` 和独立 `worker` 读取进程环境变量 `KTA_PROCESSING_SECONDS`，默认 300。大于 0、有限且不超过 `threading.TIMEOUT_MAX`；非法值拒绝启动。`.env.example` 只是说明，不会自动加载。`--demo` 使用固定本地示例配置。

Python 配置可用 `Settings(..., processing_seconds=60)`；直接创建 Worker 用 `max_processing_seconds=60`。原来的 `processor(lease)` 保持兼容。需要协作退出时，改用以下扩展入口，两个处理器参数不能同时提供：

```python
from knowledge_task_api.api import create_app
from knowledge_task_api.config import Settings

def process(lease, context):
    for step in range(10):
        context.checkpoint()  # 停止、失租或预算耗尽时抛出中断异常。
        # 在这里完成一个有自身时限的工作步骤。
        # 网络请求要设置独立超时；不可让单步无限阻塞。

settings = Settings.demo("local.sqlite3")
app = create_app(settings, processor_with_context=process)
```

`context.remaining_seconds` 给出剩余预算；`context.stop_requested` 可以用于自行清理资源；`checkpoint()` 帮你停止后续步骤。取消和失租由心跳观察，最长可能等到下一轮续租；应用停止事件可以直接被协作回调观察到。默认处理器仍只做本地文本入库，工具桥仍只有两个只读工具，没有模型调用。

## 这不是强制杀线程

预算限制的是继续持有租约和发布结果的资格。Python 无法安全强杀任意回调；忽略上下文或无限阻塞的旧代码仍可能占用线程。`stop()` 最多等待 5 秒，心跳线程和数据库调度也不是实时系统。数据库不可用时，超时状态可能不能立即落库，但 Worker 不再续租或发布，仍由过期租约恢复。

需要对不受控计算提供硬终止时，应另行设计子进程隔离、资源配额和回收；本版没有这些保证。外部 API、付款、消息等副作用也不受本地提交 fencing 保护，必须单独设计幂等和恢复。预算每次领取重新开始，不是跨重试、跨进程的总任务时长限制。

## English

Each claimed attempt receives a monotonic processing budget, 300 seconds by default. A live owner exceeding it stops heartbeating, loses its token, and records terminal `processing_timeout`; this failure is not automatically retried. Publication checks the budget again after acquiring the SQLite write lock. An obsolete timeout cannot change a replacement owner. Expired/cancelled ownership continues to follow the existing recovery rules.

Set `KTA_PROCESSING_SECONDS` for normal server/worker startup, `Settings.processing_seconds` for Python configuration, or `Worker.max_processing_seconds` directly. `processor(lease)` remains supported. Use `processor_with_context(lease, context)` for checkpoints, remaining time and stop requests. Every blocking operation still needs its own timeout.

This is cooperative cancellation and fenced publication, not forced thread termination or a real-time guarantee. A blocked legacy callback may occupy a thread after losing ownership; storage outages may delay recording failure. Budgets reset per attempt. Hard process isolation and external-side-effect guarantees remain future work.
