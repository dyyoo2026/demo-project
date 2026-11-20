from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

import billing.renewal as renewal_module
from billing.errors import PaymentError, PaymentGatewayUnavailable
from billing.models import Subscription, SubscriptionStatus
from billing.renewal import RenewalReport, SubscriptionRenewalService

NOW = datetime(2025, 3, 1, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 테스트 더블
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """서비스가 datetime.now(timezone.utc) 를 직접 호출하므로 모듈의 datetime 을 바꿔 시각을 고정한다."""
    c = Clock(NOW)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return c.now.replace(tzinfo=None) if tz is None else c.now.astimezone(tz)

    monkeypatch.setattr(renewal_module, "datetime", _FrozenDatetime)
    return c


class FakeRepository:
    """find_due 는 받은 구독을 필터링 없이 그대로 돌려준다.

    (서비스 쪽의 방어적인 상태/만료 체크를 검증하기 위함)
    save 는 저장 시점의 스냅샷을 남겨서, 저장 당시 상태를 검증할 수 있게 한다.
    """

    def __init__(self, events: list) -> None:
        self.subscriptions: list[Subscription] = []
        self.find_due_calls: list[datetime] = []
        self.saved: list[Subscription] = []
        self._events = events

    def find_due(self, now: datetime) -> list[Subscription]:
        self.find_due_calls.append(now)
        return list(self.subscriptions)

    def save(self, subscription: Subscription) -> None:
        self._events.append(("save", subscription.id))
        self.saved.append(copy.deepcopy(subscription))


@dataclass
class Charge:
    user_id: str
    amount: int
    idempotency_key: str


class FakePaymentClient:
    """user_id 별로 결과(거래 ID 문자열 또는 예외)를 순서대로 지정한다. 지정이 없으면 성공."""

    def __init__(self, events: list) -> None:
        self.charges: list[Charge] = []
        self._scripts: dict[str, list] = {}
        self._events = events

    def will(self, user_id: str, *outcomes) -> None:
        self._scripts.setdefault(user_id, []).extend(outcomes)

    def charge(self, user_id: str, amount: int, idempotency_key: str) -> str:
        self._events.append(("charge", user_id))
        self.charges.append(Charge(user_id, amount, idempotency_key))
        script = self._scripts.get(user_id)
        outcome = script.pop(0) if script else f"tx-{len(self.charges)}"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@dataclass
class Notification:
    user_id: str
    template: str
    params: dict = field(default_factory=dict)


class RecordingNotifier:
    def __init__(self, events: list) -> None:
        self.sent: list[Notification] = []
        self.fail_with: BaseException | None = None
        self._events = events

    def send(self, user_id: str, template: str, **params: object) -> None:
        self._events.append(("notify", user_id))
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(Notification(user_id, template, params))


@pytest.fixture
def events():
    """charge / save / notify 호출 순서를 기록하는 공용 로그."""
    return []


@pytest.fixture
def repo(events):
    return FakeRepository(events)


@pytest.fixture
def payments(events):
    return FakePaymentClient(events)


@pytest.fixture
def notifier(events):
    return RecordingNotifier(events)


@pytest.fixture
def service(repo, payments, notifier):
    return SubscriptionRenewalService(repo, payments, notifier)


@pytest.fixture
def make_sub(make_subscription):
    """번호 n 으로 id/user_id 가 겹치지 않는 구독을 만든다. 기본 expires_at 은 NOW 이전(만료됨)."""

    def _make(n: int = 1, **overrides) -> Subscription:
        overrides.setdefault("id", f"sub-{n}")
        overrides.setdefault("user_id", f"user-{n}")
        return make_subscription(**overrides)

    return _make


def empty_report() -> RenewalReport:
    return RenewalReport(renewed=[], failed=[], suspended=[], skipped=[])


# ---------------------------------------------------------------------------
# 대상 선정 / 스킵
# ---------------------------------------------------------------------------


class TestSelection:
    def test_queries_repository_with_current_utc_time(self, service, repo):
        service.renew_due_subscriptions()

        assert repo.find_due_calls == [NOW]
        assert repo.find_due_calls[0].tzinfo == timezone.utc

    def test_empty_batch_returns_empty_report_and_touches_nothing(
        self, service, payments, repo, notifier
    ):
        report = service.renew_due_subscriptions()

        assert report == empty_report()
        assert payments.charges == []
        assert repo.saved == []
        assert notifier.sent == []

    @pytest.mark.parametrize(
        "status", [SubscriptionStatus.CANCELLED, SubscriptionStatus.SUSPENDED]
    )
    def test_skips_inactive_subscription_without_charging(
        self, service, repo, payments, notifier, make_sub, status
    ):
        sub = make_sub(status=status)
        repo.subscriptions = [sub]

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=["sub-1"])
        assert payments.charges == []
        assert repo.saved == []
        assert notifier.sent == []

    def test_skips_subscription_not_yet_due_even_if_repository_returns_it(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [make_sub(expires_at=NOW + timedelta(seconds=1))]

        report = service.renew_due_subscriptions()

        assert report.skipped == ["sub-1"]
        assert report.renewed == []
        assert payments.charges == []
        assert repo.saved == []

    def test_charges_subscription_expiring_exactly_now(self, service, repo, payments, make_sub):
        repo.subscriptions = [make_sub(expires_at=NOW)]

        report = service.renew_due_subscriptions()

        assert report.renewed == ["sub-1"]
        assert len(payments.charges) == 1


# ---------------------------------------------------------------------------
# 결제 성공
# ---------------------------------------------------------------------------


class TestSuccessfulRenewal:
    def test_charges_plan_price_to_user_with_idempotency_key(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [
            make_sub(plan_price=14900, expires_at=datetime(2025, 2, 27, tzinfo=timezone.utc))
        ]

        service.renew_due_subscriptions()

        assert payments.charges == [
            Charge(user_id="user-1", amount=14900, idempotency_key="sub-1:20250227")
        ]

    def test_extends_expiry_by_30_days_and_resets_failed_attempts(
        self, service, repo, payments, make_sub
    ):
        old_expiry = datetime(2025, 3, 1, tzinfo=timezone.utc)
        sub = make_sub(expires_at=old_expiry, failed_attempts=2)
        repo.subscriptions = [sub]
        payments.will("user-1", "tx-abc")

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(renewed=["sub-1"], failed=[], suspended=[], skipped=[])
        assert len(repo.saved) == 1
        saved = repo.saved[0]
        assert saved.expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)
        assert saved.failed_attempts == 0
        assert saved.status == SubscriptionStatus.ACTIVE
        assert saved.history == ["renewed: tx-abc"]

    def test_sends_success_notification_with_new_expiry(
        self, service, repo, notifier, make_sub
    ):
        repo.subscriptions = [make_sub(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))]

        service.renew_due_subscriptions()

        assert notifier.sent == [
            Notification(
                "user-1",
                "renewal_success",
                {"expires_at": datetime(2025, 3, 31, tzinfo=timezone.utc)},
            )
        ]

    def test_saves_before_notifying(self, service, repo, events, make_sub):
        repo.subscriptions = [make_sub()]

        service.renew_due_subscriptions()

        assert events == [("charge", "user-1"), ("save", "sub-1"), ("notify", "user-1")]


# ---------------------------------------------------------------------------
# idempotency key
# ---------------------------------------------------------------------------


class TestIdempotencyKey:
    def test_key_uses_expiry_date_only_ignoring_time_of_day(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [
            make_sub(id="sub-xyz", expires_at=datetime(2025, 2, 28, 23, 59, 59, tzinfo=timezone.utc))
        ]

        service.renew_due_subscriptions()

        assert payments.charges[0].idempotency_key == "sub-xyz:20250228"

    def test_retry_after_payment_error_reuses_same_key_in_same_period(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [make_sub()]
        payments.will("user-1", PaymentError("card declined"))

        service.renew_due_subscriptions()  # 실패
        service.renew_due_subscriptions()  # 다음 배치에서 재시도 → 성공

        keys = [c.idempotency_key for c in payments.charges]
        assert keys == ["sub-1:20250301", "sub-1:20250301"]

    def test_retry_after_gateway_unavailable_reuses_same_key(
        self, service, repo, payments, make_sub
    ):
        # 타임아웃은 결제사에서 실제로는 승인됐을 수도 있으므로, 재시도 시 같은 키여야 중복 결제를 막을 수 있다.
        repo.subscriptions = [make_sub()]
        payments.will("user-1", PaymentGatewayUnavailable("timeout"))

        service.renew_due_subscriptions()
        service.renew_due_subscriptions()

        keys = [c.idempotency_key for c in payments.charges]
        assert keys == ["sub-1:20250301", "sub-1:20250301"]

    def test_next_period_uses_new_key_after_success(
        self, service, repo, payments, clock, make_sub
    ):
        repo.subscriptions = [make_sub(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))]

        service.renew_due_subscriptions()
        clock.now = NOW + timedelta(days=30)
        service.renew_due_subscriptions()

        keys = [c.idempotency_key for c in payments.charges]
        assert keys == ["sub-1:20250301", "sub-1:20250331"]

    def test_same_expiry_date_on_different_subscriptions_gives_different_keys(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [make_sub(1), make_sub(2)]

        service.renew_due_subscriptions()

        keys = [c.idempotency_key for c in payments.charges]
        assert keys == ["sub-1:20250301", "sub-2:20250301"]


# ---------------------------------------------------------------------------
# 결제 거절 (PaymentError)
# ---------------------------------------------------------------------------


class TestPaymentDeclined:
    @pytest.mark.parametrize(
        "previous_failures, expected_remaining",
        [(0, 2), (1, 1)],
        ids=["first-failure", "second-failure"],
    )
    def test_failure_below_limit_increments_counter_and_notifies_remaining_attempts(
        self, service, repo, payments, notifier, make_sub, previous_failures, expected_remaining
    ):
        expiry = datetime(2025, 3, 1, tzinfo=timezone.utc)
        repo.subscriptions = [make_sub(expires_at=expiry, failed_attempts=previous_failures)]
        payments.will("user-1", PaymentError("card declined"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(renewed=[], failed=["sub-1"], suspended=[], skipped=[])
        assert len(repo.saved) == 1
        saved = repo.saved[0]
        assert saved.failed_attempts == previous_failures + 1
        assert saved.status == SubscriptionStatus.ACTIVE
        assert saved.expires_at == expiry  # 실패 시 연장되지 않는다
        assert saved.history == ["failed: card declined"]
        assert notifier.sent == [
            Notification("user-1", "renewal_failed", {"remaining_attempts": expected_remaining})
        ]

    def test_third_failure_suspends_subscription(
        self, service, repo, payments, notifier, make_sub
    ):
        expiry = datetime(2025, 3, 1, tzinfo=timezone.utc)
        repo.subscriptions = [make_sub(expires_at=expiry, failed_attempts=2)]
        payments.will("user-1", PaymentError("insufficient funds"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(renewed=[], failed=[], suspended=["sub-1"], skipped=[])
        assert len(repo.saved) == 1
        saved = repo.saved[0]
        assert saved.status == SubscriptionStatus.SUSPENDED
        assert saved.failed_attempts == 3
        assert saved.expires_at == expiry
        assert saved.history == ["suspended: insufficient funds"]
        # 정지 시에는 renewal_failed 가 아니라 subscription_suspended 만 보낸다
        assert notifier.sent == [Notification("user-1", "subscription_suspended", {})]

    def test_failure_with_counter_already_over_limit_still_suspends(
        self, service, repo, payments, notifier, make_sub
    ):
        # 데이터 불일치(ACTIVE 인데 이미 실패 횟수가 한도 초과) 상황에서도 음수 remaining_attempts 를 보내지 않는다
        repo.subscriptions = [make_sub(failed_attempts=5)]
        payments.will("user-1", PaymentError("declined"))

        report = service.renew_due_subscriptions()

        assert report.suspended == ["sub-1"]
        assert repo.saved[0].status == SubscriptionStatus.SUSPENDED
        assert [n.template for n in notifier.sent] == ["subscription_suspended"]

    @pytest.mark.parametrize("previous_failures", [0, 2], ids=["failed", "suspended"])
    def test_saves_before_notifying_on_failure(
        self, service, repo, payments, events, make_sub, previous_failures
    ):
        repo.subscriptions = [make_sub(failed_attempts=previous_failures)]
        payments.will("user-1", PaymentError("declined"))

        service.renew_due_subscriptions()

        assert events == [("charge", "user-1"), ("save", "sub-1"), ("notify", "user-1")]

    def test_failure_does_not_stop_rest_of_batch(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [make_sub(1), make_sub(2), make_sub(3)]
        payments.will("user-1", PaymentError("declined"))
        payments.will("user-2", PaymentError("declined"))

        report = service.renew_due_subscriptions()

        assert [c.user_id for c in payments.charges] == ["user-1", "user-2", "user-3"]
        assert report == RenewalReport(
            renewed=["sub-3"], failed=["sub-1", "sub-2"], suspended=[], skipped=[]
        )

    def test_three_consecutive_failed_batches_suspend_and_then_skip(
        self, service, repo, payments, notifier, make_sub
    ):
        sub = make_sub()
        repo.subscriptions = [sub]
        payments.will(
            "user-1",
            PaymentError("declined #1"),
            PaymentError("declined #2"),
            PaymentError("declined #3"),
        )

        reports = [service.renew_due_subscriptions() for _ in range(4)]

        assert reports == [
            RenewalReport(renewed=[], failed=["sub-1"], suspended=[], skipped=[]),
            RenewalReport(renewed=[], failed=["sub-1"], suspended=[], skipped=[]),
            RenewalReport(renewed=[], failed=[], suspended=["sub-1"], skipped=[]),
            RenewalReport(renewed=[], failed=[], suspended=[], skipped=["sub-1"]),
        ]
        assert len(payments.charges) == 3  # 정지 후에는 결제를 시도하지 않는다
        assert notifier.sent == [
            Notification("user-1", "renewal_failed", {"remaining_attempts": 2}),
            Notification("user-1", "renewal_failed", {"remaining_attempts": 1}),
            Notification("user-1", "subscription_suspended", {}),
        ]
        assert sub.status == SubscriptionStatus.SUSPENDED
        assert sub.failed_attempts == 3
        assert sub.history == [
            "failed: declined #1",
            "failed: declined #2",
            "suspended: declined #3",
        ]

    def test_success_after_failures_resets_counter(
        self, service, repo, payments, notifier, make_sub
    ):
        sub = make_sub(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentError("declined"), PaymentError("declined"), "tx-ok")

        service.renew_due_subscriptions()
        service.renew_due_subscriptions()
        report = service.renew_due_subscriptions()

        assert report.renewed == ["sub-1"]
        assert sub.failed_attempts == 0
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)
        assert sub.history[-1] == "renewed: tx-ok"
        assert notifier.sent[-1].template == "renewal_success"


# ---------------------------------------------------------------------------
# 결제사 장애 (PaymentGatewayUnavailable)
# ---------------------------------------------------------------------------


class TestGatewayUnavailable:
    @pytest.mark.parametrize("previous_failures", [0, 2])
    def test_does_not_count_as_failure_and_leaves_subscription_untouched(
        self, service, repo, payments, notifier, make_sub, previous_failures
    ):
        # previous_failures=2 → 한 번만 더 실패하면 정지되는 상태에서도 장애는 정지로 이어지면 안 된다
        expiry = datetime(2025, 3, 1, tzinfo=timezone.utc)
        sub = make_sub(expires_at=expiry, failed_attempts=previous_failures)
        repo.subscriptions = [sub]
        payments.will("user-1", PaymentGatewayUnavailable("503"))

        report = service.renew_due_subscriptions()

        assert report == empty_report()
        assert sub.failed_attempts == previous_failures
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.expires_at == expiry
        assert sub.history == []
        assert repo.saved == []
        assert notifier.sent == []

    def test_aborts_remaining_subscriptions_in_batch(
        self, service, repo, payments, notifier, make_sub
    ):
        repo.subscriptions = [make_sub(1), make_sub(2), make_sub(3), make_sub(4)]
        payments.will("user-2", PaymentGatewayUnavailable("timeout"))

        report = service.renew_due_subscriptions()

        assert [c.user_id for c in payments.charges] == ["user-1", "user-2"]
        assert report == RenewalReport(renewed=["sub-1"], failed=[], suspended=[], skipped=[])
        assert [s.id for s in repo.saved] == ["sub-1"]
        assert [n.user_id for n in notifier.sent] == ["user-1"]

    def test_keeps_results_processed_before_abort(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [
            make_sub(1),                                         # 거절 → failed
            make_sub(2, status=SubscriptionStatus.CANCELLED),    # skipped
            make_sub(3, failed_attempts=2),                      # 거절 → suspended
            make_sub(4),                                         # 성공 → renewed
            make_sub(5),                                         # 결제사 장애 → 중단
            make_sub(6),                                         # 처리 안 됨
        ]
        payments.will("user-1", PaymentError("declined"))
        payments.will("user-3", PaymentError("declined"))
        payments.will("user-5", PaymentGatewayUnavailable("timeout"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(
            renewed=["sub-4"], failed=["sub-1"], suspended=["sub-3"], skipped=["sub-2"]
        )
        assert "user-6" not in [c.user_id for c in payments.charges]

    def test_logs_warning_with_subscription_id(self, service, repo, payments, make_sub, caplog):
        repo.subscriptions = [make_sub(7)]
        payments.will("user-7", PaymentGatewayUnavailable("timeout"))

        with caplog.at_level(logging.WARNING, logger="billing.renewal"):
            service.renew_due_subscriptions()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "sub-7" in warnings[0].getMessage()

    def test_next_batch_processes_subscriptions_that_were_aborted(
        self, service, repo, payments, make_sub
    ):
        repo.subscriptions = [make_sub(1), make_sub(2)]
        payments.will("user-1", PaymentGatewayUnavailable("timeout"))

        first = service.renew_due_subscriptions()
        second = service.renew_due_subscriptions()

        assert first == empty_report()
        assert second == RenewalReport(renewed=["sub-1", "sub-2"], failed=[], suspended=[], skipped=[])


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------


class TestReport:
    def test_classifies_mixed_batch_preserving_order(self, service, repo, payments, make_sub):
        repo.subscriptions = [
            make_sub(1),
            make_sub(2, status=SubscriptionStatus.SUSPENDED),
            make_sub(3, failed_attempts=2),
            make_sub(4),
            make_sub(5, expires_at=NOW + timedelta(days=1)),
            make_sub(6),
        ]
        payments.will("user-3", PaymentError("declined"))
        payments.will("user-4", PaymentError("declined"))

        report = service.renew_due_subscriptions()

        assert report == RenewalReport(
            renewed=["sub-1", "sub-6"],
            failed=["sub-4"],
            suspended=["sub-3"],
            skipped=["sub-2", "sub-5"],
        )


# ---------------------------------------------------------------------------
# 현재 동작 기록 (명세에 정의되지 않은 경계 상황)
# ---------------------------------------------------------------------------


class TestCurrentBehaviorOnUnexpectedErrors:
    def test_unexpected_charge_exception_propagates_without_counting_failure(
        self, service, repo, payments, notifier, make_sub
    ):
        sub = make_sub(failed_attempts=1)
        repo.subscriptions = [sub, make_sub(2)]
        payments.will("user-1", RuntimeError("bug in client"))

        with pytest.raises(RuntimeError):
            service.renew_due_subscriptions()

        assert sub.failed_attempts == 1
        assert repo.saved == []
        assert notifier.sent == []
        assert [c.user_id for c in payments.charges] == ["user-1"]  # 나머지 배치도 중단됨

    def test_notifier_failure_after_charge_propagates_but_renewal_is_already_saved(
        self, service, repo, notifier, make_sub
    ):
        repo.subscriptions = [make_sub(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))]
        notifier.fail_with = ConnectionError("smtp down")

        with pytest.raises(ConnectionError):
            service.renew_due_subscriptions()

        assert len(repo.saved) == 1
        assert repo.saved[0].expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)

    def test_long_overdue_subscription_is_extended_from_old_expiry_not_from_now(
        self, service, repo, payments, make_sub
    ):
        # 59일 연체된 구독: 1회 결제로 30일만 연장되므로 결제 후에도 여전히 만료 상태이고,
        # 다음 배치에서 다음 주기분이 다시 청구된다.
        sub = make_sub(expires_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        repo.subscriptions = [sub]

        service.renew_due_subscriptions()

        assert sub.expires_at == datetime(2025, 1, 31, tzinfo=timezone.utc)
        assert sub.is_due(NOW) is True

        service.renew_due_subscriptions()

        assert [c.idempotency_key for c in payments.charges] == [
            "sub-1:20250101",
            "sub-1:20250131",
        ]
