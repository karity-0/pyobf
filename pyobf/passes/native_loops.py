"""Bounded loop unrolling using native Python branches and iteration."""
from __future__ import annotations

import ast
import copy

from ..control_flow import LoopBoundaryVisitor, assign


def unroll_native_loop(node, names, *, builtin_proxy=None):
    """Keep loops with owned break/continue native; never emit a dispatcher.

    With no owned exits, repeated test/fetch/body lanes can share one ordinary
    while loop. Exhaustion leaves the loop before executing the original else,
    preserving exits in that else which belong to an enclosing source loop.
    """
    exits = LoopBoundaryVisitor()
    for statement in node.body:
        exits.visit(statement)
    weight = sum(sum(1 for _ in ast.walk(statement)) for statement in node.body)
    declarations = any(isinstance(item, (ast.Global, ast.Nonlocal))
                       for statement in node.body for item in ast.walk(statement))
    if exits.breaking or exits.continuing or declarations or weight > 1024:
        return node
    lanes = names.random.choice((2, 3))
    if isinstance(node, ast.While):
        body = [ast.If(test=copy.deepcopy(node.test),
                       body=copy.deepcopy(node.body), orelse=[ast.Break()])
                for _ in range(lanes)]
        return [ast.While(test=ast.Constant(True), body=body, orelse=[]), *node.orelse]

    asynchronous = isinstance(node, ast.AsyncFor)
    apis = ('aiter', 'anext', 'StopAsyncIteration') if asynchronous else ('iter', 'next', 'StopIteration')
    helpers = {name: names.new() for name in apis}
    iterator, value = names.new(), names.new()
    temporaries = [*helpers.values(), iterator, value]
    if builtin_proxy is None:
        initialize = [ast.ImportFrom(module='builtins', names=[
            ast.alias(name=name, asname=alias) for name, alias in helpers.items()
        ], level=0)]
    else:
        initialize = [assign(alias, builtin_proxy.expression(name, builtin_only=True))
                      for name, alias in helpers.items()]
    initialize.append(assign(iterator, ast.Call(
        func=ast.Name(id=helpers[apis[0]], ctx=ast.Load()), args=[node.iter], keywords=[],
    )))
    body = []
    for _ in range(lanes):
        fetch = ast.Call(func=ast.Name(id=helpers[apis[1]], ctx=ast.Load()),
                         args=[ast.Name(id=iterator, ctx=ast.Load())], keywords=[])
        if asynchronous:
            fetch = ast.Await(value=fetch)
        body.append(ast.Try(body=[assign(value, fetch)], handlers=[ast.ExceptHandler(
            type=ast.Name(id=helpers[apis[2]], ctx=ast.Load()), name=None, body=[ast.Break()],
        )], orelse=[], finalbody=[]))
        body.extend([
            ast.Assign(targets=[copy.deepcopy(node.target)], value=ast.Name(id=value, ctx=ast.Load())),
            assign(value, ast.Constant(None)), *copy.deepcopy(node.body),
        ])
    initialize.append(ast.While(test=ast.Constant(True), body=body, orelse=[]))
    return [
        ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store()) for name in temporaries], value=ast.Constant(None)),
        ast.Try(body=initialize, handlers=[], orelse=[], finalbody=[
            ast.Delete(targets=[ast.Name(id=name, ctx=ast.Del()) for name in temporaries]),
        ]),
        *node.orelse,
    ]
