from datetime import datetime, timezone


def test_is_due_when_expired(make_subscription):
    sub = make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))

    assert sub.is_due(datetime(2025, 3, 1, tzinfo=timezone.utc)) is True


def test_is_not_due_before_expiry(make_subscription):
    sub = make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))

    assert sub.is_due(datetime(2025, 2, 28, 23, 59, tzinfo=timezone.utc)) is False


def test_extend_adds_days(make_subscription):
    sub = make_subscription(expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc))

    sub.extend(30)

    assert sub.expires_at == datetime(2025, 3, 31, tzinfo=timezone.utc)
