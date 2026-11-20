package com.example.order;

public interface InventoryClient {
    /** 예약(차감)했던 재고를 복구한다. */
    void release(String productId, int quantity);
}
