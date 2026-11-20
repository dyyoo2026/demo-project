"""SubscriptionRenewalService 단위 테스트.

외부 의존성(저장소, 결제 대행사, 알림)은 아래의 간단한 Fake 로 대체하고,
`datetime.now()` 는 monkeypatch 로 고정해서 시간에 따라 결과가 바뀌지 않게 한다.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from billing import renewal
from billing.errors import PaymentError, PaymentGatewayUnavailable
from billing.models import Subscription, SubscriptionStatus
from billing.renewal import (
    MAX_FAILED_ATTEMPTS,
    RENEWAL_PERIOD_DAYS,
    RenewalReport,
    SubscriptionRenewalService,
)

# conftest 의 기본 만료 시각은 2025-03-01 00:00 UTC. 배치는 같은 날 09:00 에 돈다고 가정한다.
NOW = datetime(2025, 3, 1, 9, 0, tzinfo=timezone.utc)
DEFAULT_EXPIRES_AT = datetime(2025, 3, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeRepository:
    """find_due 는 넘겨받은 구독 목록을 그대로 돌려준다(필터링은 서비스 쪽 책임 확인용).

    save 는 호출 시점의 상태를 deepcopy 로 남겨서 '저장 당시 값'을 검증할 수 있게 한다.
    """

    def __init__(self, subscriptions: list[Subscription] | None = None) -> None:
        self.subscriptions = list(subscriptions or [])
        self.find_due_calls: list[datetime] = []
        self.saved: list[Subscription] = []

    def find_due(self, now: datetime) -> list[Subscription]:
        self.find_due_calls.append(now)
        return list(self.subscriptions)

    def save(self, subscription: Subscription) -> None:
        self.saved.append(copy.deepcopy(subscription))

    def saved_ids(self) -> list[str]:
        return [s.id for s in self.saved]


@dataclass
class ChargeCall:
    user_id: str
    amount: int
    idempotency_key: str


class FakePaymentClient:
    """user_id 별로 결과를 순서대로 지정할 수 있는 결제 클라이언트.

    결과는 거래 ID 문자열 또는 발생시킬 예외 인스턴스. 지정이 없으면 성공(tx-<user_id>).
    """

    def __init__(self) -> None:
        self.calls: list[ChargeCall] = []
        self._outcomes: dict[str, list[object]] = {}

    def will(self, user_id: str, *outcomes: object) -> "FakePaymentClient":
        self._outcomes.setdefault(user_id, []).extend(outcomes)
        return self

    def charge(self, user_id: str, amount: int, idempotency_key: str) -> str:
        self.calls.append(ChargeCall(user_id, amount, idempotency_key))
        queue = self._outcomes.get(user_id)
        outcome = queue.pop(0) if queue else f"tx-{user_id}"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]

    def charged_users(self) -> list[str]:
        return [c.user_id for c in self.calls]


@dataclass
class Notification:
    user_id: str
    template: str
    params: dict = field(default_factory=dict)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, user_id: str, template: str, **params: object) -> None:
        self.sent.append(Notification(user_id, template, dict(params)))


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class _Clock:
    """renewal 모듈이 보는 현재 시각. 테스트 중간에 옮길 수 있다."""

    def __init__(self, now: datetime) -> None:
        self.now = now


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    state = _Clock(NOW)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            # 실제 datetime.now() 와 동일하게: tz 없이 부르면 naive, 주면 해당 tz 로 변환.
            if tz is None:
                return state.now.replace(tzinfo=None)
            return state.now.astimezone(tz)

    monkeypatch.setattr(renewal, "datetime", FrozenDatetime)
    return state


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def payments() -> FakePaymentClient:
    return FakePaymentClient()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def service(clock, repo, payments, notifier) -> SubscriptionRenewalService:
    return SubscriptionRenewalService(repo, payments, notifier)


def _empty_report() -> RenewalReport:
    return RenewalReport(renewed=[], failed=[], suspended=[], skipped=[])


# --------------------------------------------------------------------------- #
# 대상 선정 / 건너뛰기
# --------------------------------------------------------------------------- #


class TestTargetSelection:
    def test_queries_repository_once_with_current_utc_time(self, service, repo):
        service.renew_due_subscriptions()

        assert repo.find_due_calls == [NOW]
        assert repo.find_due_calls[0].tzinfo is not None
        assert repo.find_due_calls[0].utcoffset() == timedelta(0)

    def test_no_due_subscriptions_returns_empty_report(self, service, payments, repo, notifier):
        report = service.renew_due_subscriptions()

        assert report == _empty_report()
        assert payments.calls == []
        assert repo.saved == []
        assert notifier.sent == []

    @pytest.mark.parametrize(
        "status", [SubscriptionStatus.CANCELLED, SubscriptionStatus.SUSPENDED]
    )
    def test_inactive_subscription_is_skipped_without_charging(
        self, service, repo, payments, notifier, make_subscription, status
    ):
        sub = make_subscription(status=status)
        repo.subscriptions = [sub]

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=["sub-1"])
        assert payments.calls == []
        assert repo.saved == []
        assert notifier.sent == []
        assert sub.expires_at == DEFAULT_EXPIRES_AT
        assert sub.status == status

    def test_not_yet_expired_subscription_returned_by_repo_is_skipped(
        self, service, repo, payments, notifier, make_subscription
    ):
        # 저장소가 잘못해서 아직 만료 전 구독을 돌려줘도 결제하면 안 된다.
        sub = make_subscription(expires_at=NOW + timedelta(seconds=1))
        repo.subscriptions = [sub]

        report = service.renew_due_subscriptions()

        assert report.skipped == ["sub-1"]
        assert report.renewed == report.failed == report.suspended == []
        assert payments.calls == []
        assert repo.saved == []
        assert notifier.sent == []

    def test_subscription_expiring_exactly_now_is_renewed(
        self, service, repo, payments, make_subscription
    ):
        repo.subscriptions = [make_subscription(expires_at=NOW)]

        report = service.renew_due_subscriptions()

        assert report.renewed == ["sub-1"]
        assert len(payments.calls) == 1


# --------------------------------------------------------------------------- #
# 결제 성공
# --------------------------------------------------------------------------- #


class TestSuccessfulRenewal:
    def test_charges_plan_price_with_idempotency_key(
        self, service, repo, payments, make_subscription
    ):
        repo.subscriptions = [make_subscription(user_id="user-7", plan_price=14900)]

        service.renew_due_subscriptions()

        assert payments.calls == [
            ChargeCall(user_id="user-7", amount=14900, idempotency_key="sub-1:20250301")
        ]

    def test_extends_expiry_resets_failures_and_saves(
        self, service, repo, payments, make_subscription
    ):
        sub = make_subscription(failed_attempts=2)
        repo.subscriptions = [sub]
        payments.will("user-1", "tx-123")

        report = service.renew_due_subscriptions()

        expected_expiry = DEFAULT_EXPIRES_AT + timedelta(days=RENEWAL_PERIOD_DAYS)
        assert report == RenewalReport(renewed=["sub-1"], failed=[], suspended=[], skipped=[])
        assert sub.expires_at == expected_expiry == datetime(2025, 3, 31, tzinfo=timezone.utc)
        assert sub.failed_attempts == 0
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.history == ["renewed: tx-123"]

        assert len(repo.saved) == 1
        saved = repo.saved[0]
        assert saved.expires_at == expected_expiry
        assert saved.failed_attempts == 0
        assert saved.history == ["renewed: tx-123"]

    def test_sends_success_notification_with_new_expiry(
        self, service, repo, notifier, make_subscription
    ):
        repo.subscriptions = [make_subscription()]

        service.renew_due_subscriptions()

        assert notifier.sent == [
            Notification(
                "user-1",
                "renewal_success",
                {"expires_at": datetime(2025, 3, 31, tzinfo=timezone.utc)},
            )
        ]

    def test_next_cycle_uses_new_idempotency_key(
        self, service, clock, repo, payments, make_subscription
    ):
        repo.subscriptions = [make_subscription()]

        service.renew_due_subscriptions()
        clock.now = NOW + timedelta(days=RENEWAL_PERIOD_DAYS)
        service.renew_due_subscriptions()

        assert [c.idempotency_key for c in payments.calls] == [
            "sub-1:20250301",
            "sub-1:20250331",
        ]

    def test_idempotency_key_uses_expiry_date_not_batch_date(
        self, service, repo, payments, make_subscription
    ):
        # 이틀 밀린 구독이라도 키는 '그 주기의 만료일' 기준이어야 같은 주기 중복 결제가 막힌다.
        repo.subscriptions = [
            make_subscription(id="sub-9", expires_at=datetime(2025, 2, 27, 23, 0, tzinfo=timezone.utc))
        ]

        service.renew_due_subscriptions()

        assert payments.calls[0].idempotency_key == "sub-9:20250227"


# --------------------------------------------------------------------------- #
# 결제 거절 (PaymentError)
# --------------------------------------------------------------------------- #


class TestPaymentDeclined:
    @pytest.mark.parametrize(
        "prior_attempts, expected_remaining",
        [(0, 2), (1, 1)],
        ids=["first-failure", "second-failure"],
    )
    def test_failure_below_limit_records_attempt_and_notifies_remaining(
        self,
        service,
        repo,
        payments,
        notifier,
        make_subscription,
        prior_attempts,
        expected_remaining,
    ):
        sub = make_subscription(failed_attempts=prior_attempts)
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentError("card declined"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(renewed=[], failed=["sub-1"], suspended=[], skipped=[])
        assert sub.failed_attempts == prior_attempts + 1
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.history == ["failed: card declined"]
        assert notifier.sent == [
            Notification("user-1", "renewal_failed", {"remaining_attempts": expected_remaining})
        ]

    def test_failure_persists_incremented_attempts(
        self, service, repo, payments, make_subscription
    ):
        repo.subscriptions = [make_subscription(failed_attempts=1)]
        payments.will("user-1", PaymentError("insufficient funds"))

        service.renew_due_subscriptions()

        assert len(repo.saved) == 1
        saved = repo.saved[0]
        assert saved.failed_attempts == 2
        assert saved.status == SubscriptionStatus.ACTIVE
        assert saved.history == ["failed: insufficient funds"]

    def test_failure_does_not_extend_expiry_or_send_success(
        self, service, repo, payments, notifier, make_subscription
    ):
        sub = make_subscription()
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentError("card declined"))

        service.renew_due_subscriptions()

        assert sub.expires_at == DEFAULT_EXPIRES_AT
        assert repo.saved[0].expires_at == DEFAULT_EXPIRES_AT
        assert "renewal_success" not in [n.template for n in notifier.sent]

    @pytest.mark.parametrize("prior_attempts", [MAX_FAILED_ATTEMPTS - 1, MAX_FAILED_ATTEMPTS + 1])
    def test_failure_reaching_limit_suspends_subscription(
        self, service, repo, payments, notifier, make_subscription, prior_attempts
    ):
        # MAX-1 회 실패한 상태에서 한 번 더 실패 → 정지.
        # (데이터가 꼬여 이미 한도를 넘긴 ACTIVE 구독도 정지되는지 함께 확인)
        sub = make_subscription(failed_attempts=prior_attempts)
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentError("card expired"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(renewed=[], failed=[], suspended=["sub-1"], skipped=[])
        assert sub.status == SubscriptionStatus.SUSPENDED
        assert sub.failed_attempts == prior_attempts + 1
        assert sub.expires_at == DEFAULT_EXPIRES_AT
        assert sub.history == ["suspended: card expired"]

        assert len(repo.saved) == 1
        assert repo.saved[0].status == SubscriptionStatus.SUSPENDED
        assert repo.saved[0].failed_attempts == prior_attempts + 1

        # 정지 시에는 renewal_failed 가 아니라 subscription_suspended 하나만 보낸다.
        assert notifier.sent == [Notification("user-1", "subscription_suspended", {})]

    def test_decline_does_not_stop_rest_of_batch(
        self, service, repo, payments, notifier, make_subscription
    ):
        repo.subscriptions = [
            make_subscription(id="sub-a", user_id="user-a"),
            make_subscription(id="sub-b", user_id="user-b"),
        ]
        payments.will("user-a", PaymentError("card declined"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(
            renewed=["sub-b"], failed=["sub-a"], suspended=[], skipped=[]
        )
        assert payments.charged_users() == ["user-a", "user-b"]
        assert repo.saved_ids() == ["sub-a", "sub-b"]
        assert [(n.user_id, n.template) for n in notifier.sent] == [
            ("user-a", "renewal_failed"),
            ("user-b", "renewal_success"),
        ]

    def test_suspension_does_not_stop_rest_of_batch(
        self, service, repo, payments, make_subscription
    ):
        repo.subscriptions = [
            make_subscription(id="sub-a", user_id="user-a", failed_attempts=2),
            make_subscription(id="sub-b", user_id="user-b"),
        ]
        payments.will("user-a", PaymentError("card declined"))

        report = service.renew_due_subscriptions()

        assert report.suspended == ["sub-a"]
        assert report.renewed == ["sub-b"]

    def test_three_consecutive_failed_batches_suspend_then_stop_charging(
        self, service, clock, repo, payments, notifier, make_subscription
    ):
        sub = make_subscription()
        repo.subscriptions = [sub]
        payments.will("user-1", *[PaymentError("card declined")] * MAX_FAILED_ATTEMPTS)

        reports = []
        for day in range(MAX_FAILED_ATTEMPTS + 1):
            clock.now = NOW + timedelta(days=day)
            reports.append(service.renew_due_subscriptions())

        assert [r.failed for r in reports] == [["sub-1"], ["sub-1"], [], []]
        assert [r.suspended for r in reports] == [[], [], ["sub-1"], []]
        assert reports[-1].skipped == ["sub-1"]

        # 정지된 뒤(4번째 배치)에는 결제를 시도하지 않는다.
        assert len(payments.calls) == MAX_FAILED_ATTEMPTS
        # 만료일이 그대로이므로 재시도 모두 같은 주기 키로 나간다.
        assert {c.idempotency_key for c in payments.calls} == {"sub-1:20250301"}

        assert [(n.template, n.params) for n in notifier.sent] == [
            ("renewal_failed", {"remaining_attempts": 2}),
            ("renewal_failed", {"remaining_attempts": 1}),
            ("subscription_suspended", {}),
        ]
        assert sub.history == [
            "failed: card declined",
            "failed: card declined",
            "suspended: card declined",
        ]
        assert sub.status == SubscriptionStatus.SUSPENDED
        assert sub.failed_attempts == MAX_FAILED_ATTEMPTS

    def test_success_after_failures_resets_counter(
        self, service, clock, repo, payments, notifier, make_subscription
    ):
        sub = make_subscription()
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentError("card declined"), PaymentError("card declined"), "tx-ok")

        for day in range(3):
            clock.now = NOW + timedelta(days=day)
            service.renew_due_subscriptions()

        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.failed_attempts == 0
        assert sub.expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)
        assert sub.history[-1] == "renewed: tx-ok"
        assert notifier.sent[-1].template == "renewal_success"

    def test_payment_error_subclass_is_treated_as_decline(
        self, service, repo, payments, make_subscription
    ):
        class CardDeclined(PaymentError):
            pass

        repo.subscriptions = [make_subscription()]
        payments.will("user-1", CardDeclined("do not honor"))

        report = service.renew_due_subscriptions()

        assert report.failed == ["sub-1"]


# --------------------------------------------------------------------------- #
# 결제사 장애 (PaymentGatewayUnavailable)
# --------------------------------------------------------------------------- #


class TestGatewayUnavailable:
    def test_does_not_count_as_failed_attempt(
        self, service, repo, payments, notifier, make_subscription
    ):
        sub = make_subscription(failed_attempts=1)
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentGatewayUnavailable("timeout"))

        report = service.renew_due_subscriptions()

        assert report == _empty_report()
        assert sub.failed_attempts == 1
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.expires_at == DEFAULT_EXPIRES_AT
        assert sub.history == []
        assert repo.saved == []
        assert notifier.sent == []

    def test_does_not_suspend_subscription_on_its_last_attempt(
        self, service, repo, payments, notifier, make_subscription
    ):
        # 결제사 장애 때문에 고객 구독이 정지되면 안 된다.
        sub = make_subscription(failed_attempts=MAX_FAILED_ATTEMPTS - 1)
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentGatewayUnavailable("503"))

        report = service.renew_due_subscriptions()

        assert report.suspended == []
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.failed_attempts == MAX_FAILED_ATTEMPTS - 1
        assert notifier.sent == []

    def test_aborts_remaining_subscriptions_in_batch(
        self, service, repo, payments, notifier, make_subscription
    ):
        repo.subscriptions = [
            make_subscription(id="sub-a", user_id="user-a"),
            make_subscription(id="sub-b", user_id="user-b"),
            make_subscription(id="sub-c", user_id="user-c"),
            make_subscription(id="sub-d", user_id="user-d", status=SubscriptionStatus.CANCELLED),
        ]
        payments.will("user-b", PaymentGatewayUnavailable("timeout"))

        report = service.renew_due_subscriptions()

        # 장애 지점(sub-b) 이후는 결제 시도도, 저장도, 보고서 분류도 하지 않는다.
        assert payments.charged_users() == ["user-a", "user-b"]
        assert repo.saved_ids() == ["sub-a"]
        assert [n.user_id for n in notifier.sent] == ["user-a"]
        assert report == RenewalReport(renewed=["sub-a"], failed=[], suspended=[], skipped=[])

    def test_keeps_results_processed_before_abort(
        self, service, repo, payments, make_subscription
    ):
        repo.subscriptions = [
            make_subscription(id="sub-skip", user_id="user-skip", status=SubscriptionStatus.CANCELLED),
            make_subscription(id="sub-ok", user_id="user-ok"),
            make_subscription(id="sub-fail", user_id="user-fail"),
            make_subscription(id="sub-susp", user_id="user-susp", failed_attempts=2),
            make_subscription(id="sub-down", user_id="user-down"),
            make_subscription(id="sub-after", user_id="user-after"),
        ]
        payments.will("user-fail", PaymentError("card declined"))
        payments.will("user-susp", PaymentError("card declined"))
        payments.will("user-down", PaymentGatewayUnavailable("connection reset"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(
            renewed=["sub-ok"],
            failed=["sub-fail"],
            suspended=["sub-susp"],
            skipped=["sub-skip"],
        )
        assert repo.saved_ids() == ["sub-ok", "sub-fail", "sub-susp"]
        assert "user-after" not in payments.charged_users()

    def test_abort_on_first_subscription_returns_empty_report(
        self, service, repo, payments, make_subscription
    ):
        repo.subscriptions = [
            make_subscription(id="sub-a", user_id="user-a"),
            make_subscription(id="sub-b", user_id="user-b"),
        ]
        payments.will("user-a", PaymentGatewayUnavailable("timeout"))

        report = service.renew_due_subscriptions()

        assert report == _empty_report()
        assert payments.charged_users() == ["user-a"]
        assert repo.saved == []

    def test_logs_warning_with_subscription_id(
        self, service, repo, payments, make_subscription, caplog
    ):
        repo.subscriptions = [make_subscription(id="sub-42")]
        payments.will("user-1", PaymentGatewayUnavailable("timeout"))

        with caplog.at_level(logging.WARNING, logger="billing.renewal"):
            service.renew_due_subscriptions()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].name == "billing.renewal"
        assert "sub-42" in warnings[0].getMessage()

    def test_next_batch_retries_with_same_idempotency_key(
        self, service, clock, repo, payments, make_subscription
    ):
        # 장애 때 결제사 쪽에서 실제로는 승인됐더라도, 같은 키로 재시도하므로 이중 결제가 막힌다.
        sub = make_subscription()
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentGatewayUnavailable("timeout"), "tx-retry")

        first = service.renew_due_subscriptions()
        clock.now = NOW + timedelta(hours=1)
        second = service.renew_due_subscriptions()

        assert first == _empty_report()
        assert second.renewed == ["sub-1"]
        assert [c.idempotency_key for c in payments.calls] == ["sub-1:20250301"] * 2
        assert sub.failed_attempts == 0
        assert sub.history == ["renewed: tx-retry"]


# --------------------------------------------------------------------------- #
# 예상하지 못한 예외
# --------------------------------------------------------------------------- #


class TestUnexpectedErrors:
    def test_unknown_payment_exception_propagates_without_touching_subscription(
        self, service, repo, payments, notifier, make_subscription
    ):
        # 현재 동작 기록: PaymentError/PaymentGatewayUnavailable 외 예외는 삼키지 않고 그대로 올라간다.
        # 그 앞에서 처리된 구독은 이미 저장/알림까지 끝난 상태로 남는다.
        first = make_subscription(id="sub-a", user_id="user-a")
        broken = make_subscription(id="sub-b", user_id="user-b", failed_attempts=1)
        repo.subscriptions = [first, broken]
        payments.will("user-b", RuntimeError("unexpected response"))

        with pytest.raises(RuntimeError, match="unexpected response"):
            service.renew_due_subscriptions()

        assert repo.saved_ids() == ["sub-a"]
        assert [n.user_id for n in notifier.sent] == ["user-a"]
        assert broken.failed_attempts == 1
        assert broken.status == SubscriptionStatus.ACTIVE
        assert broken.history == []


# --------------------------------------------------------------------------- #
# 혼합 배치
# --------------------------------------------------------------------------- #


def test_mixed_batch_is_classified_in_order(service, repo, payments, make_subscription):
    repo.subscriptions = [
        make_subscription(id="s1", user_id="u1"),
        make_subscription(id="s2", user_id="u2", status=SubscriptionStatus.CANCELLED),
        make_subscription(id="s3", user_id="u3"),
        make_subscription(id="s4", user_id="u4", failed_attempts=2),
        make_subscription(id="s5", user_id="u5", expires_at=NOW + timedelta(days=1)),
        make_subscription(id="s6", user_id="u6"),
        make_subscription(id="s7", user_id="u7", status=SubscriptionStatus.SUSPENDED),
    ]
    payments.will("u3", PaymentError("declined"))
    payments.will("u4", PaymentError("declined"))

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(
        renewed=["s1", "s6"],
        failed=["s3"],
        suspended=["s4"],
        skipped=["s2", "s5", "s7"],
    )
    assert payments.charged_users() == ["u1", "u3", "u4", "u6"]
