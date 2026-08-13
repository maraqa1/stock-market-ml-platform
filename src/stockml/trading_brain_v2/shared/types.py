from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrainBlockResult:
    block_id: str
    status: str = "placeholder"
    decision: str = "NO_ACTION"
    reason: str = "not_implemented"
    details: dict[str, Any] = field(default_factory=dict)


class PlaceholderBlock:
    block_id = ""
    name = ""

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        return BrainBlockResult(
            block_id=self.block_id,
            details={"block_name": self.name, "payload_keys": sorted((payload or {}).keys())},
        )

