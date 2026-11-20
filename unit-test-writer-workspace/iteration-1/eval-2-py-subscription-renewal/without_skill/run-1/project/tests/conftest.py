from datetime import datetime, timezone

import pytest

from billing.models import Subscription, SubscriptionStatus


@pytest.fixture
def make_subscription():
    """테스트용 구독 객체 팩토리. 필요한 필드만 덮어써서 사용한다."""

    def _make(**overrides):
        defaults = dict(
            id="sub-1",
            user_id="user-1",
            plan_price=9900,
            expires_at=datetime(2025, 3, 1, tzinfo=timezone.utc),
            status=SubscriptionStatus.ACTIVE,
            failed_attempts=0,
        )
        defaults.update(overrides)
        return Subscription(**defaults)

    return _make
