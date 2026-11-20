from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class SubscriptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"


@dataclass
class Subscription:
    id: str
    user_id: str
    plan_price: int  # 원
    expires_at: datetime
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    failed_attempts: int = 0
    history: list[str] = field(default_factory=list)

    def is_due(self, now: datetime) -> bool:
        return self.expires_at <= now

    def extend(self, days: int) -> None:
        self.expires_at = self.expires_at + timedelta(days=days)
