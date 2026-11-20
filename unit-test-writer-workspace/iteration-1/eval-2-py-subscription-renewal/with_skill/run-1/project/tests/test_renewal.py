import logging
from datetime import datetime, timezone
from unittest.mock import call, create_autospec

import pytest

from billing.errors import PaymentError, PaymentGatewayUnavailable
from billing.models import SubscriptionStatus
from billing.renewal import (
    Notifier,
    PaymentClient,
    RenewalReport,
    SubscriptionRenewalService,
    SubscriptionRepository,
)

NOW = datetime(2025, 3, 1, 9, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """billing.renewal 안의 datetime.now() 를 NOW 로 고정한다."""

    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr("billing.renewal.datetime", _FrozenDatetime)


@pytest.fixture
def repo():
    repo = create_autospec(SubscriptionRepository, instance=True)
    repo.find_due.return_value = []
    return repo


@pytest.fixture
def payments():
    payments = create_autospec(PaymentClient, instance=True)
    payments.charge.return_value = "tx-1"
    return payments


@pytest.fixture
def notifier():
    return create_autospec(Notifier, instance=True)


@pytest.fixture
def service(repo, payments, notifier):
    return SubscriptionRenewalService(repo=repo, payment_client=payments, notifier=notifier)


# --- 조회 / 대상 선정 -------------------------------------------------------


def test_returns_empty_report_when_nothing_is_due(service, payments):
    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=[])
    payments.charge.assert_not_called()


def test_queries_repository_with_current_utc_time(service, repo):
    service.renew_due_subscriptions()

    repo.find_due.assert_called_once_with(NOW)


@pytest.mark.parametrize(
    "status",
    [SubscriptionStatus.CANCELLED, SubscriptionStatus.SUSPENDED],
    ids=["cancelled", "suspended"],
)
def test_skips_inactive_subscription_without_charging(
    service, repo, payments, notifier, make_subscription, status
):
    sub = make_subscription(status=status)
    repo.find_due.return_value = [sub]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=["sub-1"])
    payments.charge.assert_not_called()
    repo.save.assert_not_called()
    notifier.send.assert_not_called()


def test_skips_subscription_not_yet_due_even_if_repository_returns_it(
    service, repo, payments, make_subscription
):
    sub = make_subscription(expires_at=datetime(2025, 3, 1, 9, 0, 1, tzinfo=timezone.utc))
    repo.find_due.return_value = [sub]

    report = service.renew_due_subscriptions()

    assert report.skipped == ["sub-1"]
    assert report.renewed == []
    payments.charge.assert_not_called()


def test_renews_subscription_expiring_exactly_now(service, repo, make_subscription):
    sub = make_subscription(expires_at=NOW)
    repo.find_due.return_value = [sub]

    report = service.renew_due_subscriptions()

    assert report.renewed == ["sub-1"]
    assert sub.expires_at == datetime(2025, 3, 31, 9, 0, tzinfo=timezone.utc)


# --- 결제 성공 ---------------------------------------------------------------


def test_renews_due_subscription_and_extends_expiry_by_30_days(service, repo, make_subscription):
    sub = make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))
    repo.find_due.return_value = [sub]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=["sub-1"], failed=[], suspended=[], skipped=[])
    assert sub.expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)
    assert sub.status == SubscriptionStatus.ACTIVE


def test_charges_plan_price_with_idempotency_key_from_expiry_date(
    service, repo, payments, make_subscription
):
    sub = make_subscription(
        id="sub-42",
        user_id="user-7",
        plan_price=14900,
        expires_at=datetime(2025, 2, 7, 23, 30, tzinfo=timezone.utc),
    )
    repo.find_due.return_value = [sub]

    service.renew_due_subscriptions()

    payments.charge.assert_called_once_with("user-7", 14900, idempotency_key="sub-42:20250207")


def test_success_saves_subscription_and_records_transaction_in_history(
    service, repo, payments, make_subscription
):
    payments.charge.return_value = "tx-abc"
    sub = make_subscription()
    repo.find_due.return_value = [sub]

    service.renew_due_subscriptions()

    repo.save.assert_called_once_with(sub)
    assert sub.history == ["renewed: tx-abc"]


def test_success_sends_renewal_notification_with_new_expiry(
    service, repo, notifier, make_subscription
):
    sub = make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))
    repo.find_due.return_value = [sub]

    service.renew_due_subscriptions()

    notifier.send.assert_called_once_with(
        "user-1", "renewal_success", expires_at=datetime(2025, 3, 31, tzinfo=timezone.utc)
    )


def test_success_resets_previous_failed_attempts(service, repo, make_subscription):
    sub = make_subscription(failed_attempts=2)
    repo.find_due.return_value = [sub]

    service.renew_due_subscriptions()

    assert sub.failed_attempts == 0
    assert sub.status == SubscriptionStatus.ACTIVE


# --- 결제 실패: PaymentError (한도 전) ---------------------------------------


@pytest.mark.parametrize(
    ("previous_attempts", "expected_attempts"),
    [(0, 1), (1, 2)],
    ids=["first_failure", "second_failure"],
)
def test_payment_error_below_limit_records_failure_and_keeps_subscription_active(
    service, repo, payments, make_subscription, previous_attempts, expected_attempts
):
    payments.charge.side_effect = PaymentError("card declined")
    sub = make_subscription(
        failed_attempts=previous_attempts,
        expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    repo.find_due.return_value = [sub]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=[], failed=["sub-1"], suspended=[], skipped=[])
    assert sub.failed_attempts == expected_attempts
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.history == ["failed: card declined"]
    assert sub.expires_at == datetime(2025, 3, 1, tzinfo=timezone.utc)
    repo.save.assert_called_once_with(sub)


@pytest.mark.parametrize(
    ("previous_attempts", "expected_remaining"),
    [(0, 2), (1, 1)],
    ids=["first_failure", "second_failure"],
)
def test_payment_error_below_limit_notifies_remaining_attempts(
    service, repo, payments, notifier, make_subscription, previous_attempts, expected_remaining
):
    payments.charge.side_effect = PaymentError("card declined")
    repo.find_due.return_value = [make_subscription(failed_attempts=previous_attempts)]

    service.renew_due_subscriptions()

    notifier.send.assert_called_once_with(
        "user-1", "renewal_failed", remaining_attempts=expected_remaining
    )


# --- 결제 실패: PaymentError (3회째 → 정지) ----------------------------------


def test_third_payment_error_suspends_subscription(service, repo, payments, make_subscription):
    payments.charge.side_effect = PaymentError("insufficient funds")
    sub = make_subscription(
        failed_attempts=2,
        expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    repo.find_due.return_value = [sub]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=[], failed=[], suspended=["sub-1"], skipped=[])
    assert sub.status == SubscriptionStatus.SUSPENDED
    assert sub.failed_attempts == 3
    assert sub.history == ["suspended: insufficient funds"]
    assert sub.expires_at == datetime(2025, 3, 1, tzinfo=timezone.utc)
    repo.save.assert_called_once_with(sub)


def test_third_payment_error_sends_suspension_notice_instead_of_failure_notice(
    service, repo, payments, notifier, make_subscription
):
    payments.charge.side_effect = PaymentError("insufficient funds")
    repo.find_due.return_value = [make_subscription(failed_attempts=2)]

    service.renew_due_subscriptions()

    notifier.send.assert_called_once_with("user-1", "subscription_suspended")


def test_payment_error_does_not_stop_processing_rest_of_batch(
    service, repo, payments, make_subscription
):
    failing = make_subscription(id="sub-1", user_id="user-1")
    healthy = make_subscription(id="sub-2", user_id="user-2")
    repo.find_due.return_value = [failing, healthy]
    payments.charge.side_effect = [PaymentError("card declined"), "tx-2"]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=["sub-2"], failed=["sub-1"], suspended=[], skipped=[])
    assert healthy.history == ["renewed: tx-2"]


def test_three_consecutive_failed_batches_suspend_then_skip_subscription(
    service, repo, payments, notifier, make_subscription
):
    payments.charge.side_effect = PaymentError("card declined")
    sub = make_subscription()
    repo.find_due.return_value = [sub]

    reports = [service.renew_due_subscriptions() for _ in range(4)]

    assert [r.failed for r in reports] == [["sub-1"], ["sub-1"], [], []]
    assert [r.suspended for r in reports] == [[], [], ["sub-1"], []]
    assert [r.skipped for r in reports] == [[], [], [], ["sub-1"]]
    # 같은 주기 재시도이므로 매번 동일한 idempotency key, 정지 후에는 결제 시도 없음
    assert payments.charge.call_args_list == [
        call("user-1", 9900, idempotency_key="sub-1:20250301"),
        call("user-1", 9900, idempotency_key="sub-1:20250301"),
        call("user-1", 9900, idempotency_key="sub-1:20250301"),
    ]
    assert notifier.send.call_args_list == [
        call("user-1", "renewal_failed", remaining_attempts=2),
        call("user-1", "renewal_failed", remaining_attempts=1),
        call("user-1", "subscription_suspended"),
    ]
    assert sub.history == [
        "failed: card declined",
        "failed: card declined",
        "suspended: card declined",
    ]


def test_retry_after_payment_error_reuses_key_and_recovers_on_success(
    service, repo, payments, make_subscription
):
    payments.charge.side_effect = [PaymentError("card declined"), "tx-retry"]
    sub = make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))
    repo.find_due.return_value = [sub]

    first = service.renew_due_subscriptions()
    second = service.renew_due_subscriptions()

    assert first.failed == ["sub-1"]
    assert second.renewed == ["sub-1"]
    assert payments.charge.call_args_list == [
        call("user-1", 9900, idempotency_key="sub-1:20250301"),
        call("user-1", 9900, idempotency_key="sub-1:20250301"),
    ]
    assert sub.failed_attempts == 0
    assert sub.expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)
    assert sub.history == ["failed: card declined", "renewed: tx-retry"]


# --- 결제사 장애: PaymentGatewayUnavailable ----------------------------------


def test_gateway_unavailable_leaves_subscription_untouched(
    service, repo, payments, notifier, make_subscription
):
    payments.charge.side_effect = PaymentGatewayUnavailable("timeout")
    sub = make_subscription(
        failed_attempts=2,
        expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    repo.find_due.return_value = [sub]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=[], failed=[], suspended=[], skipped=[])
    assert sub.failed_attempts == 2
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.history == []
    assert sub.expires_at == datetime(2025, 3, 1, tzinfo=timezone.utc)
    repo.save.assert_not_called()
    notifier.send.assert_not_called()


def test_gateway_unavailable_aborts_rest_of_batch_but_keeps_earlier_results(
    service, repo, payments, make_subscription
):
    declined = make_subscription(id="sub-a", user_id="user-a")
    renewed = make_subscription(id="sub-b", user_id="user-b")
    outage = make_subscription(id="sub-c", user_id="user-c")
    not_reached = make_subscription(id="sub-d", user_id="user-d")
    repo.find_due.return_value = [declined, renewed, outage, not_reached]
    payments.charge.side_effect = [
        PaymentError("card declined"),
        "tx-b",
        PaymentGatewayUnavailable("503"),
        "tx-d",
    ]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(renewed=["sub-b"], failed=["sub-a"], suspended=[], skipped=[])
    assert payments.charge.call_args_list == [
        call("user-a", 9900, idempotency_key="sub-a:20250301"),
        call("user-b", 9900, idempotency_key="sub-b:20250301"),
        call("user-c", 9900, idempotency_key="sub-c:20250301"),
    ]
    assert repo.save.call_args_list == [call(declined), call(renewed)]
    assert not_reached.expires_at == datetime(2025, 3, 1, tzinfo=timezone.utc)


def test_gateway_unavailable_logs_warning_with_subscription_id(
    service, repo, payments, make_subscription, caplog
):
    payments.charge.side_effect = PaymentGatewayUnavailable("timeout")
    repo.find_due.return_value = [make_subscription(id="sub-9")]

    with caplog.at_level(logging.WARNING, logger="billing.renewal"):
        service.renew_due_subscriptions()

    assert "aborting batch at sub-9" in caplog.text


# --- 혼합 배치 ---------------------------------------------------------------


def test_classifies_each_subscription_of_mixed_batch_in_order(
    service, repo, payments, make_subscription
):
    repo.find_due.return_value = [
        make_subscription(id="ok-1", user_id="u1"),
        make_subscription(id="cancelled", status=SubscriptionStatus.CANCELLED),
        make_subscription(id="declined", user_id="u2", failed_attempts=0),
        make_subscription(id="ok-2", user_id="u3"),
        make_subscription(id="last-chance", user_id="u4", failed_attempts=2),
        make_subscription(id="future", expires_at=datetime(2025, 4, 1, tzinfo=timezone.utc)),
    ]
    payments.charge.side_effect = [
        "tx-1",
        PaymentError("declined"),
        "tx-2",
        PaymentError("declined"),
    ]

    report = service.renew_due_subscriptions()

    assert report == RenewalReport(
        renewed=["ok-1", "ok-2"],
        failed=["declined"],
        suspended=["last-chance"],
        skipped=["cancelled", "future"],
    )
