import ast
import functools
import math
import operator

FUNCS = {"round": round, "min": min, "max": max, "abs": abs}
BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}


@functools.lru_cache(maxsize=None)
def parse(expr):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
                raise ValueError("%s: only %s calls" % (expr, ", ".join(FUNCS)))
        elif not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
                                   ast.Load, *BINOPS, *UNARY)):
            raise ValueError("%s: %s not allowed" % (expr, type(node).__name__))
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("%s: only numbers" % expr)
    return tree.body


def round_away(x):
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def names(expr):
    return {n.id for n in ast.walk(parse(expr)) if isinstance(n, ast.Name) and n.id not in FUNCS}


def evaluate(expr, env):
    return _eval(parse(expr), env)


def _eval(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise KeyError("unknown name in formula: %s" % node.id)
        return env[node.id]
    if isinstance(node, ast.BinOp):
        return BINOPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        return UNARY[type(node.op)](_eval(node.operand, env))
    return FUNCS[node.func.id](*(_eval(a, env) for a in node.args))


class Calc:
    """One [calc.<name>] table: VDP targets computed from EEPROM items."""

    def __init__(self, name, d):
        self.name = name
        self.kind = d.get("kind", "expr")
        self.when = d.get("when")
        self.live = d.get("live", True)
        self.note = d.get("note", "")
        self.bits = d.get("bits", 16)
        self.size = d.get("size", 2)
        self.readable = d.get("readable", False)
        self.mirror = {int(k, 16): v for k, v in d.get("mirror", {}).items()}
        self.save_a0 = d.get("save_a0")
        kind, addrs = ("fp", d["fp"]) if "fp" in d else ("reg", d["reg"])
        self.targets = [(kind, a) for a in (addrs if isinstance(addrs, list) else [addrs])]
        self.lets = list(d.get("let", {}).items())
        self.expr = d.get("expr")
        self.terms = d.get("terms", [])
        self.weights = d.get("weights", {})
        self.round = d.get("round", "sum")
        exprs = [e for _, e in self.lets] + self.terms + ([self.expr] if self.expr else [])
        self.uses = set(d.get("uses", []))
        for e in exprs:
            self.uses |= names(e)
        self.uses -= {k for k, _ in self.lets}
        self.computable = bool(self.expr or self.terms)

    def formula(self):
        lets = "".join("%s = %s; " % kv for kv in self.lets)
        if self.kind == "matrix":
            return "%sTable 3-3 weights (%s Hz) x [%s]%s" % (lets, "/".join(sorted(self.weights)), ", ".join(self.terms),
                                                           ", each term rounded" if self.round == "each" else "")
        return lets + self.expr if self.expr else "formula unknown (uses %s)" % ", ".join(sorted(self.uses))

    def fields(self):
        return set(self.weights) if self.kind == "matrix" else None

    def values(self, env, field=None):
        env = dict(env)
        for k, e in self.lets:
            env[k] = evaluate(e, env)
        mask = (1 << self.bits) - 1
        if self.kind == "matrix":
            coef = [evaluate(t, env) for t in self.terms]
            if self.round == "each":
                return [sum(round_away(w * c) for w, c in zip(row, coef)) & mask for row in self.weights[field]]
            return [round(sum(w * c for w, c in zip(row, coef))) & mask for row in self.weights[field]]
        return [round(evaluate(self.expr, env)) & mask]

    def target_name(self, i):
        kind, addr = self.targets[i]
        return "FP %03X" % addr if kind == "fp" else "reg %02X" % addr
