# 长任务为什么需要续租

先运行 `python scripts/lease_demo.py`，再读 `store.py` 的 `claim / renew / complete` 和 `worker.py`。示例使用真实 SQLite、真实心跳线程和受控时钟；没有等待十秒来碰运气，也不是吞吐量测试。

## 一个会失败的时间线

假设租约长度是 10 秒。A 在 t=0 领取，期限为 t=10；处理需要 12 秒。没有续租时，t=10 之后 B 可以重新领取；A 即使先算完，也必须拒绝发布。只把租约调得很长，会让进程崩溃后的恢复更慢。

现在 A 在 t=8 成功续租，期限变为 t=18。t=12 时 B 仍不能领取，A 用原来的 token 完成。续租不算新的 attempt，也不改变 token。示例用事件等待续租真正落库，再推进到 t=12，因此这一顺序是确定的。

| 操作 | 同一写事务中检查 | 结果 |
| --- | --- | --- |
| `claim()` | 待运行或已过期的任务 | 新 attempt、新 token、初始期限 |
| `renew(lease)` | tenant、id、running、token 相同，且期限严格大于当前时间 | 延长到不早于 `now + lease_seconds`，写 `lease_renewed` 事件 |
| `complete(lease)` | 再次检查同样的有效租约条件 | 原文、FTS、终态和成功事件一起提交 |
| `cancel()` | 当前租户有权限且任务可取消 | 撤销 token；旧处理器无法续租或发布 |

`BEGIN IMMEDIATE` 保证领取、续租、取消和提交不能同时修改同一数据库。时间在拿到写锁之后读取。**到期时刻本身已无资格**；续租不能让过期的 A 复活。进程崩溃、心跳不及时或数据库阻塞仍可能导致失租，因此最终提交的 fencing 检查不可省。

## 处理器入口

```python
from knowledge_task_api.api import create_app
from knowledge_task_api.config import Settings

def prepare(lease):
    # 你的本地处理步骤；返回后仍由 Store 做带 fencing 的索引发布。
    # 若接入网络库，必须给其请求设置超时。
    pass

app = create_app(Settings.demo("demo.sqlite3"), processor=prepare)
```

这是 Python 扩展入口；HTTP 文档提交格式不变，默认处理器仍是本地文本入库。每次领取后启动独立心跳，默认间隔为租约的三分之一。`Worker(..., heartbeat_interval=...)` 允许测试或本地配置显式间隔，必须大于 0 且短于租约。

续租失败后，Worker 不再发布，若租约仍有效则按既有次数和退避规则重新排队；若已失效，等待下一次领取恢复。异常日志只记错误类别。停止会阻止新任务和结果发布，但 Python 不能强制终止任意回调：`stop()` 最多等 5 秒，回调应自行限制执行时间。心跳也不是执行时间上限，一个持续存活的卡死处理器可能一直续租。任务超时、子进程隔离与外部副作用幂等仍需后续设计。

## 本轮还修了什么

原实现接受 `lease_seconds=float("nan")`。SQLite 将该值存为 NULL 后，`lease_until <= now` 和 `lease_until > now` 都不成立，任务既不能回收也不能完成。现在在创建数据库之前拒绝非有限、非正数和布尔时长；Settings 也拒绝非法流时长和非整数请求大小。

动手练习：在临时分支取消一次续租调用，预测示例在哪个断言失败；再把回调改为取消当前任务，查看持久化事件与文档计数。恢复代码后运行 `python -m pytest`、`python scripts/lease_demo.py` 和 `python scripts/demo.py`。开发日志记录你自己的预测、输出和解释；不要把受控时钟下的验证称为生产可靠性认证。
