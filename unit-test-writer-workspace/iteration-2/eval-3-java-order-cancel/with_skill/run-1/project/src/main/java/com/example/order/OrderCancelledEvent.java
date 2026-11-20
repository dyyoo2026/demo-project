package com.example.order;

public record OrderCancelledEvent(long orderId, long customerId, long refundedAmount, String reason) {
}
