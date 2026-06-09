from __future__ import annotations

from dataclasses import asdict, dataclass
from time import time


@dataclass
class BridgeStatus:
    state: str = "disconnected"
    detail: str = "Deckhand server not connected"
    last_change_ms: int = 0

    def __post_init__(self) -> None:
        if self.last_change_ms == 0:
            self.last_change_ms = int(time() * 1000)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def update(self, state: str, detail: str) -> None:
        self.state = state
        self.detail = detail
        self.last_change_ms = int(time() * 1000)


bridge_status = BridgeStatus()

