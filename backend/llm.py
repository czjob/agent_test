"""
模型客户端单例。

为什么单独一个文件：
  P0 只有一个调用方（agent.py），放在里面不算问题。
  P1 之后摘要压缩、记忆抽取都要用模型，如果各自 new 一个 ChatOpenAI，
  就等于各自维护一套连接池和重试配置——改个超时参数要改三处。

这里只放"通用对话模型"。带工具的模型在 agent.py 里 bind_tools 得到，
因为工具清单属于 Agent 的职责，不属于模型客户端。
"""

from langchain_openai import ChatOpenAI

from config import settings

# 全局唯一实例。
# 注意 temperature=0：摘要和记忆抽取都要求结果稳定可复现，
# 同一段对话跑两次得到不同的摘要会让"增量压缩"逻辑难以推理。
llm = ChatOpenAI(
    model=settings.LLM_MODEL,
    temperature=0,
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    timeout=60,
    max_retries=2,
)
