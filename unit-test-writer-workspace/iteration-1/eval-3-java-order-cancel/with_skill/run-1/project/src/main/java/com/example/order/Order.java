package com.example.order;

import java.util.List;

public class Order {
    private final long id;
    private final long customerId;
    private final List<OrderLine> lines;
    private final long shippingFee;
    private OrderStatus status;
    private String cancelReason;

    public Order(long id, long customerId, List<OrderLine> lines, long shippingFee, OrderStatus status) {
        this.id = id;
        this.customerId = customerId;
        this.lines = List.copyOf(lines);
        this.shippingFee = shippingFee;
        this.status = status;
    }

    public long getId() { return id; }
    public long getCustomerId() { return customerId; }
    public List<OrderLine> getLines() { return lines; }
    public long getShippingFee() { return shippingFee; }
    public OrderStatus getStatus() { return status; }
    public String getCancelReason() { return cancelReason; }

    /** 상품 금액 합계 + 배송비 */
    public long totalAmount() {
        return lines.stream().mapToLong(OrderLine::amount).sum() + shippingFee;
    }

    void markCancelled(String reason) {
        this.status = OrderStatus.CANCELLED;
        this.cancelReason = reason;
    }
}
