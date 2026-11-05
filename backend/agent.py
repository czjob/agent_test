"""
Agent 核心：手写 ReAct 循环 + 逐 token 流式输出。

为什么不用 langchain 1.x 的预置 agent（create_agent）：
  它内部用 ainvoke 调模型，拿不到 token 级分片，做不出打字机效果；
  astream_events 也拿不到 on_chat_model_stream 事件。

为什么这个文件不碰数据库：
  它的职责只有"跟模型对话 + 跑工具 + 吐事件"。
  历史从哪读、结果存哪，是上层 routers/chat.py 的事。
  这样将来换存储（Redis / 别的库）不需要动这个文件，也方便单独测试。
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from config import settings
from llm import llm
from tools import TOOLS_BY_NAME, tools

SYSTEM_PROMPT = "你是智能助手，必要时调用工具完成任务，回答简洁准确。"

# 绑定工具：把工具的 JSON Schema 注入每次请求，模型才能返回 tool_calls
# 注意用的是 llm.py 里那个共享实例——摘要压缩、记忆抽取也用同一个客户端，
# 好处是连接池和超时配置只有一份。
_model = llm.bind_tools(tools)


def build_system_prompt(
    summary: str | None = None,
    preferences: Sequence[str] = (),
    facts: Sequence[str] = (),
) -> str:
    """
    拼系统提示：基础人设 + 早期对话摘要 + 用户画像。

    为什么偏好和事实要分两段，而不是混成一个列表：
      这两类记忆的效力完全不同。
      "喜欢简洁的回答"是**用户对输出形式的要求**，必须压过模型自己的默认风格；
      "是前端工程师"只是背景，用来说明问题该怎么讲，不该影响回答的长短。
      混在一起写，模型会把它们一视同仁地当"参考信息"，偏好就失灵了——
      实测过：混着写时问"什么是虚拟 DOM"，模型照样输出六百字带小标题的长文。
      分开写并明确标注"必须遵守"，才会真的变简洁。

    为什么这三段要合到一个 SystemMessage 里而不是拆成多条：
      OpenAI 兼容接口对 system 消息的位置有要求（必须在最前面），
      多条 system 消息在部分兼容实现上行为不一致。合并成一条最稳。

    段的顺序有讲究：
      摘要在前（"聊到哪了"），画像在后（"这个人是谁"）。
      模型对末尾内容的注意力更强，把长期稳定的画像放最后，
      比放前面更容易在整轮回答里被贯彻。
    """
    parts = [SYSTEM_PROMPT]

    if summary:
        parts.append(
            "## 早期对话摘要\n"
            "以下是你和用户在更早的对话中已经确认的内容，可以直接当作事实使用：\n"
            f"{summary.strip()}"
        )

    if preferences:
        lines = "\n".join(f"- {item}" for item in preferences)
        parts.append(
            "## 用户明确表达过的偏好与要求（必须遵守）\n"
            "这些是对你输出形式的要求，优先级高于你的默认风格。"
            "即使你觉得更详细的回答更好，也要按用户的要求来：\n"
            f"{lines}"
        )

    if facts:
        lines = "\n".join(f"- {item}" for item in facts)
        parts.append(
            "## 关于用户的背景事实\n"
            "这些可以作为理解问题的前提，但不要主动复述，除非用户问起：\n"
            f"{lines}"
        )

    return "\n\n".join(parts)


@dataclass
class AgentEvent:
    """
    流式过程中的一条事件，由 routers/chat.py 转成 SSE 报文。

    type:
      token      —— 模型输出的文本增量
      tool_start —— 开始调用某个工具
      tool_end   —— 工具返回结果
      error      —— 链路异常，前端展示为错误
    """

    type: str
    content: str


async def stream_agent(
    history: list[BaseMessage],
    produced: list[BaseMessage],
    *,
    system_prompt: str | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    执行一轮完整的 Agent 对话。

    参数说明：
      history       —— 已经准备好的历史消息（不含系统提示，本函数内部自己拼）
      produced      —— 输出参数。本轮新产生的 AI / 工具消息会被依次 append 进来，
                       调用方在流结束后拿它落库。用"出参列表"而不是 yield 最终结果，
                       是因为生成器只能 yield 事件，return 值取不到。
      system_prompt —— 系统提示。不传就用基础人设；
                       上层会把 L2 摘要和 L3 记忆拼进去（见 build_system_prompt）。

    收敛条件：
      - 模型不再返回 tool_calls        → 说明这就是最终回答，正常结束
      - 轮数达到 MAX_AGENT_ROUNDS      → 防死循环，返回 error 事件
    """
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt or SYSTEM_PROMPT), *history]

    for _round in range(settings.MAX_AGENT_ROUNDS):
        full_msg: AIMessage | None = None
        partial_text = ""

        try:
            # astream 逐 token 返回 AIMessageChunk
            async for chunk in _model.astream(messages):
                content = chunk.content
                if content:
                    # 控制推送节奏：模型快到几毫秒一个 token 时，短回答会瞬间刷完，
                    # 看不出流式效果。加最小间隔保证肉眼可见。
                    if settings.TOKEN_MIN_INTERVAL > 0:
                        await asyncio.sleep(settings.TOKEN_MIN_INTERVAL)
                    partial_text += content
                    yield AgentEvent("token", content)

                # AIMessageChunk 的 + 运算会把 tool_call_chunks 按 index 拼成完整 tool_calls
                full_msg = chunk if full_msg is None else full_msg + chunk
        except Exception as exc:
            # 模型调用本身失败（网络 / 额度 / 限流）。把已经吐出去的字存下来，
            # 否则前端看到一半的回答，刷新后却一片空白。
            if partial_text:
                produced.append(AIMessage(content=partial_text))
            yield AgentEvent("error", f"模型调用失败：{exc}")
            return

        if full_msg is None:
            yield AgentEvent("error", "模型未返回任何内容")
            return

        # 这一轮的模型回复无论如何都要进消息序列：
        # 下一步无论是执行工具还是结束，模型都需要看到自己说了什么
        messages.append(full_msg)
        produced.append(full_msg)

        # 没有工具调用 → 最终回答，结束
        if not full_msg.tool_calls:
            return

        # 有工具调用 → 逐个执行，结果作为 ToolMessage 追加
        for tool_call in full_msg.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_call_id = tool_call["id"]

            yield AgentEvent("tool_start", f"🔧 正在调用工具：{tool_name}")

            tool = TOOLS_BY_NAME.get(tool_name)
            if tool is None:
                # 模型幻觉出不存在的工具：不崩溃，把错误作为结果回传，它通常会自我纠正
                result = f"错误：工具 {tool_name} 不存在"
            else:
                # 工具内部已 try 兜底，不会抛异常；同步函数很快，直接调用即可
                result = str(tool.invoke(tool_args))

            yield AgentEvent("tool_end", f"✅ 工具返回：{result}")

            tool_message = ToolMessage(
                content=result, tool_call_id=tool_call_id, name=tool_name
            )
            messages.append(tool_message)
            produced.append(tool_message)

        # 继续下一轮：模型看到工具结果后生成最终总结

    yield AgentEvent("error", "生成失败：工具调用轮数超出上限")
