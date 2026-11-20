package com.example.order;

public class PaymentException extends RuntimeException {
    public PaymentException(String message) {
        super(message);
    }
}
