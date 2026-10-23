"""Compute-unit (CU) ledger. Colab bills per wall-clock hour while a VM is
assigned; we estimate usage as hours * CU_PER_HOUR and persist per-day totals.
`ccu-info` from the colab CLI (when parseable) overrides the balance."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Ledger:
    path: Path | None = None
    per_day: dict[str, float] = field(default_factory=dict)  # "YYYY-MM-DD" -> CU
    balance: float | None = None  # last known remaining CU from ccu-info

    @staticmethod
    def load(path: Path) -> "Ledger":
        led = Ledger(path=path)
        if path.is_file():
            d = json.loads(path.read_text())
            led.per_day = {k: float(v) for k, v in d.get("per_day", {}).items()}
            led.balance = d.get("balance")
        return led

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"per_day": self.per_day, "balance": self.balance}))
            tmp.replace(self.path)

    def add(self, cu: float, when: float | None = None) -> None:
        day = datetime.fromtimestamp(when or time.time()).strftime("%Y-%m-%d")
        self.per_day[day] = round(self.per_day.get(day, 0.0) + cu, 4)
        if self.balance is not None:
            self.balance = max(0.0, self.balance - cu)

    def today(self, now: float | None = None) -> float:
        return self.per_day.get(datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d"), 0.0)

    def month(self, now: float | None = None) -> float:
        prefix = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-")
        return round(sum(v for k, v in self.per_day.items() if k.startswith(prefix)), 4)
