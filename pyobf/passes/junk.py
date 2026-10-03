from __future__ import annotations

import ast

from ..names import NameAllocator
from .base import BlockPass


class JunkCodePass(BlockPass):
    """Insert bounded integer-only noise without reading user names or globals."""

    def _block(self, names: NameAllocator) -> list[ast.stmt]:
        noise = names.new()
        loop = names.new()
        seed = names.random.randrange(1000, 1 << 20)
        mask = names.random.randrange(1000, 1 << 20)
        # Consecutive integers have an even product, so this branch cannot run.
        # The live name prevents Python from folding away the whole predicate.
        variants = [
            f"""if ({noise} * ({noise} + 1)) & 1:
    {noise} = ({noise} ^ {mask}) + {seed}
    for {loop} in (2, 5, 9):
        {noise} = ({noise} + {loop}) ^ {mask}""",
            f"""if ({noise} * ({noise} + 1)) & 1:
    while {noise} < 0:
        {noise} = ({noise} + {seed}) & 65535
else:
    {noise} = ({noise} ^ {mask}) + {seed}""",
            f"""if ({noise} * ({noise} + 1)) & 1:
    {noise} = (({seed}, {mask}, {seed} ^ {mask})[1] + {noise}) & 65535
    if {noise} & 2:
        {noise} ^= {seed}""",
        ]
        code = f"{noise} = {seed}\n{names.random.choice(variants)}\ndel {noise}"
        return ast.parse(code).body

    def run(self, statements: list[ast.stmt], names: NameAllocator) -> list[ast.stmt]:
        # Cap expansion even for very large protected regions.
        positions = {0}
        if statements:
            positions.update(names.random.sample(range(len(statements)), min(3, len(statements))))
        output: list[ast.stmt] = []
        for index, statement in enumerate(statements):
            if index in positions:
                output.extend(self._block(names))
            output.append(statement)
        if not statements:
            output.extend(self._block(names))
        return output
