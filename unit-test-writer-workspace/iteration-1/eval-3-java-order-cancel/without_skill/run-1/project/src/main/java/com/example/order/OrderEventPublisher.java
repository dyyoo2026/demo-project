package com.example.order;

public interface OrderEventPublisher {
    void publish(OrderCancelledEvent event);
}
