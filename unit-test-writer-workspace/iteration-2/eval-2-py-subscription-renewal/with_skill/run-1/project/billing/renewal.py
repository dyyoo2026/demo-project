from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from billing.errors import PaymentError, PaymentGatewayUnavailable
from billing.models import Subscription, SubscriptionStatus

logger = logging.getLogger(__name__)

RENEWAL_PERIOD_DAYS = 30
MAX_FAILED_ATTEMPTS = 3


class SubscriptionRepository(Protocol):
    def find_due(self, now: datetime) -> list[Subscription]: ...
    def save(self, subscription: Subscription) -> None: ...


class PaymentClient(Protocol):
    def charge(self, user_id: str, amount: int, idempotency_key: str) -> str:
        """결제 후 거래 ID를 반환한다. 실패 시 PaymentError / PaymentGatewayUnavailable."""
        ...


class Notifier(Protocol):
    def send(self, user_id: str, template: str, **params: object) -> None: ...


@dataclass
class RenewalReport:
    renewed: list[str]
    failed: list[str]
    suspended: list[str]
    skipped: list[str]


class SubscriptionRenewalService:
    """만료된 구독을 자동 갱신한다.

    - 만료 시각(expires_at)이 현재 시각 이하인 구독이 대상이다.
    - CANCELLED / SUSPENDED 구독은 결제하지 않고 skipped 로 분류한다.
    - 결제 성공: 만료일을 30일 연장, 실패 횟수 초기화, 저장, "renewal_success" 알림.
    - PaymentError: 실패 횟수 +1 후 저장. 3회째 실패하면 SUSPENDED 로 전환하고
      "subscription_suspended" 알림, 그 전까지는 "renewal_failed" 알림(남은 시도 횟수 포함).
    - PaymentGatewayUnavailable: 결제사 장애이므로 실패 횟수를 올리지 않고,
      이번 배치의 나머지 구독 처리를 중단한다(다음 배치에서 재시도).
    - idempotency_key 는 "{구독ID}:{만료일 YYYYMMDD}" 형식이라 같은 주기에 중복 결제되지 않는다.
    """

    def __init__(
        self,
        repo: SubscriptionRepository,
        payment_client: PaymentClient,
        notifier: Notifier,
    ) -> None:
        self._repo = repo
        self._payments = payment_client
        self._notifier = notifier

    def renew_due_subscriptions(self) -> RenewalReport:
        now = datetime.now(timezone.utc)
        report = RenewalReport(renewed=[], failed=[], suspended=[], skipped=[])

        for sub in self._repo.find_due(now):
            if sub.status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.SUSPENDED):
                report.skipped.append(sub.id)
                continue
            if not sub.is_due(now):
                report.skipped.append(sub.id)
                continue

            key = f"{sub.id}:{sub.expires_at:%Y%m%d}"
            try:
                tx_id = self._payments.charge(sub.user_id, sub.plan_price, idempotency_key=key)
            except PaymentGatewayUnavailable:
                logger.warning("payment gateway unavailable; aborting batch at %s", sub.id)
                break
            except PaymentError as exc:
                sub.failed_attempts += 1
                if sub.failed_attempts >= MAX_FAILED_ATTEMPTS:
                    sub.status = SubscriptionStatus.SUSPENDED
                    sub.history.append(f"suspended: {exc}")
                    self._repo.save(sub)
                    self._notifier.send(sub.user_id, "subscription_suspended")
                    report.suspended.append(sub.id)
                else:
                    sub.history.append(f"failed: {exc}")
                    self._repo.save(sub)
                    self._notifier.send(
                        sub.user_id,
                        "renewal_failed",
                        remaining_attempts=MAX_FAILED_ATTEMPTS - sub.failed_attempts,
                    )
                    report.failed.append(sub.id)
                continue

            sub.extend(RENEWAL_PERIOD_DAYS)
            sub.failed_attempts = 0
            sub.history.append(f"renewed: {tx_id}")
            self._repo.save(sub)
            self._notifier.send(sub.user_id, "renewal_success", expires_at=sub.expires_at)
            report.renewed.append(sub.id)

        return report
