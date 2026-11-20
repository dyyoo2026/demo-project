package com.example.order;

public interface PaymentGateway {
    /** 결제 건을 환불한다. 실패 시 PaymentException 을 던진다. */
    void refund(long orderId, long amount);
}
