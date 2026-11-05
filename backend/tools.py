"""
Agent 可调用的工具。

【给后来者的说明】
@tool 装饰的函数，docstring 不是写给人看的，是写给模型看的"工具说明书"。
模型靠 函数名 + docstring + 参数类型注解 来决定要不要调用、参数怎么传。
docstring 写得含糊，工具调用率会直接掉下来。

P2 加 RAG 时，只需要在这里加一个 search_knowledge_base 工具并放进 tools 列表，
agent.py 的主循环一行都不用改。
"""

import ast
import operator
from datetime import datetime

from langchain_core.tools import tool

# 安全四则运算求值器：只放行数字和 + - * / // % **，杜绝 eval 注入。
# 背景：eval("__import__('os').system('rm -rf /')") 能直接执行任意代码，
# 所以这里用 ast 把表达式拆成语法树，只处理白名单内的节点类型。
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _safe_eval(node) -> float:
    """递归遍历 AST 并求值，只处理白名单节点类型，其余一律拒绝。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        # 排除字符串常量，杜绝 "abc" 这类输入
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        # 防御 9**9**9 这种能把 CPU 打满的指数爆炸
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("指数过大")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _safe_eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    raise ValueError(f"不支持的表达式: {ast.dump(node)}")


@tool
def calculate(expression: str) -> str:
    """
    数学计算器，仅支持加减乘除四则运算。
    参数 expression 是合法的数学表达式字符串，例如 "256 * 32"。
    """
    try:
        result = _safe_eval(ast.parse(expression, mode="eval"))
        return f"计算结果：{result}"
    except Exception as e:
        # 工具内部出错不要抛异常中断对话，把错误信息作为字符串返回，
        # 模型会自己组织语言告诉用户，体验更自然。
        return f"计算错误：{str(e)}"


@tool
def get_current_time() -> str:
    """获取当前的日期和时间。"""
    return datetime.now().strftime("当前时间：%Y年%m月%d日 %H:%M:%S")


# 工具列表。新增工具只需要往这里加，agent.py 会自动 bind 和分发。
tools = [calculate, get_current_time]

# 工具名 -> 工具实例。循环里按模型给出的工具名查表执行。
TOOLS_BY_NAME = {t.name: t for t in tools}
