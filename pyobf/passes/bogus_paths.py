"""Bounded dead-path shapes using only the guard's private integer seed."""
from __future__ import annotations

import ast


class BogusPaths:
    families = ('mix', 'diamond', 'ladder', 'countdown', 'scan', 'table', 'cleanup', 'select')

    def __init__(self, random):
        self.random = random
        self.pending = []
        self.previous = None

    def build(self, seed, *, family=None):
        # Exhaust a shuffled bag before repeating a family. Also avoid repeats
        # across bag boundaries; numeric randomization alone is not diversity.
        if family is None:
            if not self.pending:
                self.pending = list(self.families)
                self.random.shuffle(self.pending)
                if self.pending[-1] == self.previous:
                    self.pending[0], self.pending[-1] = self.pending[-1], self.pending[0]
            family = self.pending.pop()
            self.previous = family
        if family not in self.families:
            raise ValueError(f'Unknown bogus path: {family}')
        r = self.random
        mask = r.randrange(256, 1 << 24)
        amount = r.randrange(3, 257)
        shift = r.randrange(1, 8)
        bound = r.randrange(2, 6)

        def mix():
            op = r.choice(('^', '+', '-'))
            rotate = r.choice(('<<', '>>'))
            return f'(({seed} {op} {mask}) {rotate} {shift}) & 65535'

        templates = {
            'mix': f'{seed} = {mix()}\n{seed} ^= {amount}\n{seed} = {mix()}',
            'diamond': f'''if {seed} & {bound}:
    {seed} = {mix()}
    if {seed} % {amount}:
        {seed} ^= {mask}
else:
    {seed} += {amount}''',
            'ladder': f'''if {seed} % {amount} == 0:
    {seed} = {mix()}
elif {seed} & 1:
    {seed} -= {mask}
else:
    {seed} ^= {amount}''',
            'countdown': f'''{seed} &= {bound}
while {seed} > 0:
    {seed} -= 1
else:
    {seed} ^= {mask}''',
            'scan': f'''for {seed} in ({mask}, {amount}, {bound}):
    if {seed} & 1:
        {seed} = {mix()}
    else:
        {seed} ^= {mask}''',
            'table': f'''{seed} = ({mask}, {amount}, {bound})[{seed} % 3]
{seed} ^= ({amount}, {mask})[{seed} & 1]''',
            'cleanup': f'''try:
    {seed} = {mix()}
finally:
    {seed} ^= {amount}''',
            'select': f'''{seed} = {mix()} if {seed} & 1 else ({seed} ^ {amount})
{seed} = ({seed} & {mask}) or ({seed} + {bound})''',
        }
        return ast.parse(templates[family]).body
