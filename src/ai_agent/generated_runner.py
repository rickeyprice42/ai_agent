"""Restricted pure-data extension worker; launched with Python -I -S.

This is deliberately not a general Python sandbox. The parent checks an AST
allowlist; arithmetic/string operations are additionally bounded here before
allocation. The model cannot import, call functions, inspect attributes or do I/O.
"""
from __future__ import annotations

import ast
import json
import math
import sys

MAX_BYTES = 256_000
MAX_SEQUENCE = 50_000
MAX_NUMBER = 1_000_000_000_000


def checked(value):
    if isinstance(value, (str, list, tuple)) and len(value) > MAX_SEQUENCE:
        raise ValueError("Generated result exceeds the sequence limit.")
    if type(value) in {int, float} and (not math.isfinite(value) or abs(value) > MAX_NUMBER):
        raise ValueError("Generated result exceeds the numeric limit.")
    return value


def add(left, right):
    if isinstance(left, (str, list, tuple)) and type(left) is type(right) and len(left) + len(right) > MAX_SEQUENCE:
        raise ValueError("Generated addition exceeds the sequence limit.")
    return checked(left + right)


def multiply(left, right):
    sequence, count = (left, right) if isinstance(left, (str, list, tuple)) else (right, left)
    if isinstance(sequence, (str, list, tuple)) and isinstance(count, int) and len(sequence) * max(0, count) > MAX_SEQUENCE:
        raise ValueError("Generated multiplication exceeds the sequence limit.")
    return checked(left * right)


def join(values):
    if any(type(value) not in {str, int, float, bool, type(None)} for value in values):
        raise ValueError("Generated formatting supports scalar JSON values only.")
    parts = [str(value) for value in values]
    if sum(map(len, parts)) > MAX_SEQUENCE:
        raise ValueError("Generated string exceeds the sequence limit.")
    return "".join(parts)


class BoundedOperations(ast.NodeTransformer):
    def visit_BinOp(self, node):
        self.generic_visit(node)
        operation = "_avelin_add" if isinstance(node.op, ast.Add) else "_avelin_multiply"
        return ast.copy_location(ast.Call(ast.Name(operation, ast.Load()), [node.left, node.right], []), node)

    def visit_JoinedStr(self, node):
        values = []
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                if value.format_spec is not None or value.conversion != -1:
                    raise ValueError("Format specifications and conversions are not supported.")
                values.append(self.visit(value.value))
            else:
                values.append(value)
        return ast.copy_location(ast.Call(ast.Name("_avelin_join", ast.Load()), [ast.List(values, ast.Load())], []), node)


def main():
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Generated input exceeds the byte limit.")
    payload = json.loads(raw)
    tree = BoundedOperations().visit(ast.parse(payload["source"]))
    ast.fix_missing_locations(tree)
    scope = {"__builtins__": {}, "_avelin_add": add, "_avelin_multiply": multiply, "_avelin_join": join}
    exec(compile(tree, "<avelin-generated-extension>", "exec"), scope, scope)
    result = scope["run"](payload["arguments"])
    chunks = []
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode({"result": result}):
        encoded = chunk.encode("utf-8")
        size += len(encoded)
        if size > MAX_BYTES:
            raise ValueError("Generated output exceeds the byte limit.")
        chunks.append(encoded)
    sys.stdout.buffer.write(b"".join(chunks))


if __name__ == "__main__":
    main()
