package com.example.order;

public class OrderNotFoundException extends RuntimeException {
    public OrderNotFoundException(long orderId) {
        super("Order not found: " + orderId);
    }
}
