class PaymentError(Exception):
    """결제 대행사에서 결제가 거절되었거나 실패했을 때."""


class PaymentGatewayUnavailable(Exception):
    """결제 대행사와 통신 자체가 불가능할 때 (타임아웃, 5xx 등)."""
