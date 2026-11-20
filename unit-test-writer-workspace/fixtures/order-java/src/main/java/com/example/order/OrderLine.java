package com.example.order;

public record OrderLine(String productId, int quantity, long unitPrice) {
    public long amount() {
        return unitPrice * quantity;
    }
}
