from dataclasses import dataclass
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        object.__setattr__(self, "value", self.value.astimezone(UTC))

    def now(self) -> datetime:
        return self.value
