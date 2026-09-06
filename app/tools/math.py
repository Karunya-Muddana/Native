# app/tools/math.py
"""Arithmetic on one expression, without handing the string to eval().

`eval` on model-produced text is the isolation hole in an otherwise sandboxed
system: everything else here runs in a container with no network, while this ran
arbitrary Python in the agent's own process, with its imports and its
filesystem. `__import__("os").system(...)` is a valid expression.

So the expression is parsed to an AST and walked. Only the node types below are
allowed to exist — there is no name lookup, no attribute access, no call to
anything but the handful of functions in FUNCTIONS, and no way to reach a
builtin. Anything else is a parse-time refusal, not a runtime one.
"""

import ast
import math
import operator

from langchain_core.tools import tool

# Binary and unary operators, mapped to the operator module rather than
# evaluated. `**` included, but see MAX_EXPONENT — 9**9**9 is a denial of
# service that needs no cleverness at all.
BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": lambda *a: sum(a[0]) if len(a) == 1 and isinstance(a[0], (list, tuple)) else sum(a),
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "degrees": math.degrees,
    "radians": math.radians,
    "floor": math.floor,
    "ceil": math.ceil,
    "trunc": math.trunc,
    "hypot": math.hypot,
    "factorial": math.factorial,
}
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

MAX_EXPONENT = 1000  # 2**1000 is already a 300-digit number; nothing legitimate needs more
MAX_EXPRESSION = 500


@tool
def calculate(expression: str) -> str:
    """
    Evaluate one arithmetic expression and return the result.

    Supports + - * / // % **, parentheses, comparisons, and the common maths
    functions: sqrt, log, log10, exp, sin, cos, tan, asin, acos, atan, atan2,
    degrees, radians, floor, ceil, round, abs, min, max, sum, hypot, factorial.
    The constants pi, e and tau are available by name.

    This is for a single expression — '(12.4 - 9.8) / 9.8 * 100'. It is not
    Python: no variables, no assignment, no loops, no imports. For anything
    with steps, data or a file in it, use python_runner.
    """
    expression = (expression or "").strip()
    if not expression:
        return "Error: no expression given."
    if len(expression) > MAX_EXPRESSION:
        return f"Error: expression is longer than {MAX_EXPRESSION} characters."

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        return f"Error: '{expression}' is not a valid expression ({e.msg})."

    try:
        result = _evaluate(tree.body)
    except _Rejected as e:
        return f"Error: {e}"
    except ZeroDivisionError:
        return "Error: division by zero."
    except (ValueError, OverflowError) as e:
        return f"Error: {str(e)}."
    except Exception as e:
        return f"Error: {str(e)}"

    return _format(result)


class _Rejected(Exception):
    """Something in the expression is outside what this tool evaluates."""


def _evaluate(node: ast.AST):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise _Rejected(f"only numbers are allowed, got {type(node.value).__name__}")

    if isinstance(node, ast.BinOp):
        op = BIN_OPS.get(type(node.op))
        if op is None:
            raise _Rejected(f"the operator {_name(node.op)} is not allowed here")
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise _Rejected(f"exponent {right} is too large (limit {MAX_EXPONENT})")
        return op(left, right)

    if isinstance(node, ast.UnaryOp):
        op = UNARY_OPS.get(type(node.op))
        if op is None:
            raise _Rejected(f"the operator {_name(node.op)} is not allowed here")
        return op(_evaluate(node.operand))

    if isinstance(node, ast.Compare):
        # '12.4 < 9.8' is a fair question to ask this tool; chained comparisons
        # (a < b < c) work the same way Python's do.
        left = _evaluate(node.left)
        for op_node, right_node in zip(node.ops, node.comparators):
            right = _evaluate(right_node)
            compare = _COMPARE.get(type(op_node))
            if compare is None:
                raise _Rejected(f"the comparison {_name(op_node)} is not allowed here")
            if not compare(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        # A plain name only. `node.func` being an Attribute is exactly the
        # `os.system` / `().__class__` shape this whole module exists to refuse.
        if not isinstance(node.func, ast.Name):
            raise _Rejected("only the built-in maths functions can be called")
        name = node.func.id
        function = FUNCTIONS.get(name)
        if function is None:
            raise _Rejected(f"unknown function '{name}'. Available: {', '.join(sorted(FUNCTIONS))}")
        if node.keywords:
            raise _Rejected(f"{name}() takes plain arguments, not keyword arguments")
        return function(*[_evaluate(arg) for arg in node.args])

    if isinstance(node, ast.Name):
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        raise _Rejected(
            f"unknown name '{node.id}'. There are no variables here — "
            f"substitute the number, or use python_runner."
        )

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_evaluate(element) for element in node.elts]  # for min/max/sum

    raise _Rejected(
        f"{type(node).__name__} is not allowed in an expression. "
        "This tool does arithmetic only — use python_runner for anything more."
    )


_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _name(op: ast.AST) -> str:
    return type(op).__name__


def _format(result) -> str:
    """Trim float noise without lying about the value.

    0.1 + 0.2 should read as 0.3, but a genuinely long result must not be
    rounded into a different number — so the short form is used only when it
    round-trips back to the same float.
    """
    if isinstance(result, bool):
        return str(result)
    if isinstance(result, float):
        if result != result or result in (math.inf, -math.inf):
            return str(result)
        short = f"{result:.10g}"
        if float(short) == result:
            return short
    if isinstance(result, list):
        return ", ".join(_format(r) for r in result)
    return str(result)
