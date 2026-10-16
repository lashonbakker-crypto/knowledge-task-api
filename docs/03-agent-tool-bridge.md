# 只读 Agent 工具适配器 / Read-only tool bridge

这是一个**真实可调用的工具层**，不是完整 Agent。它把现有租户隔离的文档搜索和原文读取接口转换成模型可见的函数描述；仓库不内置模型调用、循环规划或自主写入。

## 快速体验 / Quick demo

在仓库根目录安装测试依赖后：

~~~bash
python -m pip install -c requirements.lock ".[test]"
python scripts/agent_bridge_demo.py
python -m pytest tests/test_agent_bridge.py -q
~~~

脚本只使用本地临时 SQLite 和 FastAPI TestClient，不连接第三方模型服务。它真实提交一份教学文档，启动工作线程将文档入库，再执行两次**只读**工具调用：`search_knowledge` 查找匹配片段、`read_knowledge_source` 读取有长度限制的原文，最后确认另一租户无法读到它。

## 作为模型工具 / Tool calling contract

~~~python
from knowledge_task_api.agent_bridge import TOOL_DEFINITIONS, ReadOnlyToolBridge

# You create a trusted HTTP client, e.g. httpx.Client(base_url=YOUR_API_URL).
# Keep your tenant token on the trusted tool-execution side, never in the model schema.
bridge = ReadOnlyToolBridge(client, token=YOUR_PRIVATE_API_TOKEN)
print([item['function']['name'] for item in TOOL_DEFINITIONS])
print(bridge.execute('search_knowledge', {'query': 'durable leases', 'limit': 3}))
~~~

`TOOL_DEFINITIONS` is a static function-tool schema for adapters that support function calling. Models can choose a name and arguments; **the trusted application** must decide whether to execute the call. `ReadOnlyToolBridge` accepts only two tool names and only the documented arguments. There is no arbitrary URL, shell command, file path, write method or token field in model-supplied arguments.

Queries must contain 1–12 Unicode word tokens; punctuation-only and oversized token lists are rejected before HTTP dispatch. Queries go to the user's explicitly configured API client. Tool results never contain the access token. Search returns up to five hits; original-document reads are truncated to 2000 characters. Each hit includes a document ID and source task. Tenant ownership checks are still enforced in `Store`, not solely by client-side filtering.

**安全边界：** 检索到的文档、标题和片段全部视为不可信数据，不能把文档中的“忽略规则、执行命令”等文本当作工具指令。若你把返回内容发给第三方模型，它会离开本地机器；是否上传应由使用者明确决定。请勿在公共 Demo 以外使用示例 token。这个适配层没有工具调用预算、速率限制、多步 Agent 或语义检索；这些仍属未来工作。
