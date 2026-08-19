from collections import defaultdict
from uuid import uuid4


class UuidIdGenerator:
    def new(self, prefix: str) -> str:
        normalized = prefix.rstrip("_")
        if not normalized:
            raise ValueError("ID prefix cannot be empty")
        return f"{normalized}_{uuid4().hex}"


class SequenceIdGenerator:
    def __init__(self, *, start: int = 1) -> None:
        if start < 0:
            raise ValueError("start must be non-negative")
        self._counters: defaultdict[str, int] = defaultdict(lambda: start)

    def new(self, prefix: str) -> str:
        normalized = prefix.rstrip("_")
        if not normalized:
            raise ValueError("ID prefix cannot be empty")
        value = self._counters[normalized]
        self._counters[normalized] += 1
        return f"{normalized}_{value}"
