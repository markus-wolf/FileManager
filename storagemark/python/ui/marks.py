"""MarkSet — a set of marked paths with a mutation version counter.

The header wants 'Marked: N items, X GB' but recomputing the size of a
potentially-huge marked set on every 0.5s header tick is wasteful, and
mark mutations happen in many widgets. Overriding the mutators lets the
app recompute the total only when the version actually changes.
"""
from __future__ import annotations


class MarkSet(set):
    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.version = 0

    def add(self, item) -> None:
        if item not in self:
            self.version += 1
        super().add(item)

    def discard(self, item) -> None:
        if item in self:
            self.version += 1
        super().discard(item)

    def clear(self) -> None:
        if self:
            self.version += 1
        super().clear()

    def update(self, *others) -> None:
        before = len(self)
        super().update(*others)
        if len(self) != before:
            self.version += 1

    def difference_update(self, *others) -> None:
        before = len(self)
        super().difference_update(*others)
        if len(self) != before:
            self.version += 1
