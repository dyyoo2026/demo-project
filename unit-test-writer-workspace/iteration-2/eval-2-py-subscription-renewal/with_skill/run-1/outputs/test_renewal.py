import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import call, create_autospec

import pytest

from billing.errors import PaymentError, PaymentGatewayUnavailable
from billing.models import SubscriptionStatus
from billing.renewal import (
    Notifier,
    PaymentClient,
    RenewalReport,
    SubscriptionRenewalService,
)

# conftest 의 기본 구독은 2025-03-01 00:00 UTC 에 만료된다. 배치는 같은 날 09:00 에 돈다.
NOW = datetime(2025, 3, 1, 9, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class FakeRepository:
    """find_due 가 돌려줄 구독 목록을 지정하고, save 시점의 상태를 스냅샷으로 기록한다."""

    def __init__(self):
        self.due = []
        self.find_due_calls = []
        self.saved = []

    def find_due(self, now):
        self.find_due_calls.append(now)
        return list(self.due)

    def save(self, subscription):
        self.saved.append(copy.deepcopy(subscription))


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr("billing.renewal.datetime", _FrozenDatetime)


@pytest.fixture
def repo():
    return FakeRepository()


@pytest.fixture
def payments():
    client = create_autospec(PaymentClient, instance=True)
    client.charge.return_value = "tx-100"
    return client


@pytest.fixture
def notifier():
    return create_autospec(Notifier, instance=True)


@pytest.fixture
def service(repo, payments, notifier):
    return SubscriptionRenewalService(repo=repo, payment_client=payments, notifier=notifier)


# --- 대상 선정 ---------------------------------------------------------------


def test_queries_repository_with_current_utc_time(service, repo):
    service.renew_due_subscriptions()

    assert repo.find_due_calls == [NOW]


def test_returns_empty_report_when_nothing_is_due(service, payments, notifier):
    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=[])
    payments.charge.assert_not_called()
    notifier.send.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [SubscriptionStatus.CANCELLED, SubscriptionStatus.SUSPENDED],
    ids=["cancelled", "suspended"],
)
def test_skips_inactive_subscription_without_charging(
    service, repo, payments, notifier, make_subscription, status
):
    repo.due = [make_subscription(status=status)]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=["sub-1"])
    payments.charge.assert_not_called()
    assert repo.saved == []
    notifier.send.assert_not_called()


def test_skips_subscription_not_yet_due_even_if_repository_returns_it(
    service, repo, payments, make_subscription
):
    repo.due = [make_subscription(expires_at=NOW + timedelta(seconds=1))]

    report = service.renew_due_subscriptions()

    assert report.skipped == ["sub-1"]
    assert report.renewed == []
    payments.charge.assert_not_called()


def test_renews_subscription_expiring_exactly_now(service, repo, make_subscription):
    repo.due = [make_subscription(expires_at=NOW)]

    report = service.renew_due_subscriptions()

    assert report.renewed == ["sub-1"]


# --- 결제 성공 ---------------------------------------------------------------


def test_charges_plan_price_with_idempotency_key_from_current_expiry_date(
    service, repo, payments, make_subscription
):
    repo.due = [
        make_subscription(
            id="sub-42",
            user_id="user-7",
            plan_price=14900,
            expires_at=datetime(2025, 2, 28, 23, 0, tzinfo=timezone.utc),
        )
    ]

    service.renew_due_subscriptions()

    payments.charge.assert_called_once_with("user-7", 14900, idempotency_key="sub-42:20250228")


def test_successful_renewal_extends_expiry_by_30_days_from_previous_expiry(
    service, repo, make_subscription
):
    repo.due = [make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))]

    service.renew_due_subscriptions()

    assert len(repo.saved) == 1
    assert repo.saved[0].expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)
    assert repo.saved[0].history == ["renewed: tx-100"]
    assert repo.saved[0].status == SubscriptionStatus.ACTIVE


def test_successful_renewal_resets_failed_attempts(service, repo, make_subscription):
    repo.due = [make_subscription(failed_attempts=2)]

    service.renew_due_subscriptions()

    assert repo.saved[0].failed_attempts == 0


def test_successful_renewal_notifies_user_with_new_expiry(
    service, repo, notifier, make_subscription
):
    repo.due = [make_subscription(user_id="user-7")]

    service.renew_due_subscriptions()

    notifier.send.assert_called_once_with(
        "user-7",
        "renewal_success",
        expires_at=datetime(2025, 3, 31, tzinfo=timezone.utc),
    )


def test_successful_renewal_is_reported_as_renewed(service, repo, make_subscription):
    repo.due = [make_subscription()]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=["sub-1"], failed=[], suspended=[], skipped=[])


# --- 결제 거절 (PaymentError) -------------------------------------------------


@pytest.mark.parametrize(
    ("prior_attempts", "expected_attempts", "expected_remaining"),
    [(0, 1, 2), (1, 2, 1)],
    ids=["first-failure", "second-failure"],
)
def test_payment_error_increments_attempts_and_notifies_remaining_attempts(
    service,
    repo,
    payments,
    notifier,
    make_subscription,
    prior_attempts,
    expected_attempts,
    expected_remaining,
):
    repo.due = [make_subscription(failed_attempts=prior_attempts)]
    payments.charge.side_effect = PaymentError("card declined")

    report = service.renew_due_subscriptions()

    assert len(repo.saved) == 1
    assert repo.saved[0].failed_attempts == expected_attempts
    assert repo.saved[0].status == SubscriptionStatus.ACTIVE
    notifier.send.assert_called_once_with(
        "user-1", "renewal_failed", remaining_attempts=expected_remaining
    )
    assert report == RenewalReport(renewed=[], failed=["sub-1"], suspended=[], skipped=[])


def test_payment_error_records_reason_in_history(service, repo, payments, make_subscription):
    repo.due = [make_subscription()]
    payments.charge.side_effect = PaymentError("card declined")

    service.renew_due_subscriptions()

    assert repo.saved[0].history == ["failed: card declined"]


def test_payment_error_does_not_extend_expiry(service, repo, payments, make_subscription):
    repo.due = [make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))]
    payments.charge.side_effect = PaymentError("card declined")

    service.renew_due_subscriptions()

    assert repo.saved[0].expires_at == datetime(2025, 3, 1, tzinfo=timezone.utc)


def test_third_payment_failure_suspends_subscription(service, repo, payments, make_subscription):
    repo.due = [make_subscription(failed_attempts=2)]
    payments.charge.side_effect = PaymentError("insufficient funds")

    report = service.renew_due_subscriptions()

    assert len(repo.saved) == 1
    assert repo.saved[0].status == SubscriptionStatus.SUSPENDED
    assert repo.saved[0].failed_attempts == 3
    assert repo.saved[0].history == ["suspended: insufficient funds"]
    assert repo.saved[0].expires_at == datetime(2025, 3, 1, tzinfo=timezone.utc)
    assert report == RenewalReport(renewed=[], failed=[], suspended=["sub-1"], skipped=[])


def test_third_payment_failure_sends_only_suspended_notification(
    service, repo, payments, notifier, make_subscription
):
    repo.due = [make_subscription(failed_attempts=2)]
    payments.charge.side_effect = PaymentError("insufficient funds")

    service.renew_due_subscriptions()

    notifier.send.assert_called_once_with("user-1", "subscription_suspended")


def test_payment_error_does_not_stop_processing_other_subscriptions(
    service, repo, payments, make_subscription
):
    repo.due = [
        make_subscription(id="sub-a", user_id="user-a"),
        make_subscription(id="sub-b", user_id="user-b"),
    ]
    payments.charge.side_effect = [PaymentError("card declined"), "tx-b"]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=["sub-b"], failed=["sub-a"], suspended=[], skipped=[])


# --- 결제사 장애 (PaymentGatewayUnavailable) -----------------------------------


def test_gateway_unavailable_does_not_count_as_failed_attempt(
    service, repo, payments, notifier, make_subscription
):
    sub = make_subscription(failed_attempts=2)
    repo.due = [sub]
    payments.charge.side_effect = PaymentGatewayUnavailable("timeout")

    report = service.renew_due_subscriptions()

    assert sub.failed_attempts == 2
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.expires_at == datetime(2025, 3, 1, tzinfo=timezone.utc)
    assert sub.history == []
    assert repo.saved == []
    notifier.send.assert_not_called()
    assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=[])


def test_gateway_unavailable_aborts_rest_of_batch_but_keeps_earlier_results(
    service, repo, payments, notifier, make_subscription
):
    repo.due = [
        make_subscription(id="sub-a", user_id="user-a"),
        make_subscription(id="sub-b", user_id="user-b"),
        make_subscription(id="sub-c", user_id="user-c"),
        make_subscription(id="sub-d", user_id="user-d", status=SubscriptionStatus.CANCELLED),
    ]
    payments.charge.side_effect = ["tx-a", PaymentGatewayUnavailable("503")]

    report = service.renew_due_subscriptions()

    assert payments.charge.call_args_list == [
        call("user-a", 9900, idempotency_key="sub-a:20250301"),
        call("user-b", 9900, idempotency_key="sub-b:20250301"),
    ]
    assert [saved.id for saved in repo.saved] == ["sub-a"]
    notifier.send.assert_called_once_with(
        "user-a", "renewal_success", expires_at=datetime(2025, 3, 31, tzinfo=timezone.utc)
    )
    assert report == RenewalReport(renewed=["sub-a"], failed=[], suspended=[], skipped=[])


def test_gateway_unavailable_after_payment_error_keeps_failed_result(
    service, repo, payments, make_subscription
):
    repo.due = [
        make_subscription(id="sub-a", user_id="user-a"),
        make_subscription(id="sub-b", user_id="user-b"),
    ]
    payments.charge.side_effect = [PaymentError("card declined"), PaymentGatewayUnavailable("timeout")]

    report = service.renew_due_subscriptions()

    assert [(saved.id, saved.failed_attempts) for saved in repo.saved] == [("sub-a", 1)]
    assert report == RenewalReport(renewed=[], failed=["sub-a"], suspended=[], skipped=[])


# --- 혼합 배치 ---------------------------------------------------------------


def test_classifies_each_subscription_in_mixed_batch(service, repo, payments, make_subscription):
    repo.due = [
        make_subscription(id="sub-renew", user_id="user-1"),
        make_subscription(id="sub-cancelled", status=SubscriptionStatus.CANCELLED),
        make_subscription(id="sub-fail", user_id="user-2", failed_attempts=0),
        make_subscription(id="sub-suspend", user_id="user-3", failed_attempts=2),
    ]
    payments.charge.side_effect = ["tx-1", PaymentError("declined"), PaymentError("declined")]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(
        renewed=["sub-renew"],
        failed=["sub-fail"],
        suspended=["sub-suspend"],
        skipped=["sub-cancelled"],
    )
