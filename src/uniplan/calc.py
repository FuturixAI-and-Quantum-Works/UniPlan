"""A safe arithmetic evaluator, so the planner computes (string voltages, areas, ratios) instead of doing sums in its head."""

import ast
import math
import operator

OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}  # fmt: skip
NAMES = {k: getattr(math, k) for k in ("pi", "e", "sqrt", "log", "log10", "exp", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "radians", "degrees", "ceil", "floor", "hypot")}
NAMES.update(abs=abs, round=round, min=min, max=max, sum=sum, acre=4046.8564224, hectare=10000.0)


def evaluate(expr: str, variables: dict[str, float] | None = None) -> float:
    env = {**NAMES, **(variables or {})}

    def ev(n: ast.AST):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in env:
            return env[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and callable(env.get(n.func.id)) and not n.keywords:
            return env[n.func.id](*[ev(a) for a in n.args])
        if isinstance(n, (ast.List, ast.Tuple)):
            return [ev(e) for e in n.elts]
        raise ValueError(f"not allowed in a calculation: {ast.dump(n)[:60]}")

    if len(expr) > 2000:
        raise ValueError("expression too long")
    return ev(ast.parse(expr, mode="eval"))
