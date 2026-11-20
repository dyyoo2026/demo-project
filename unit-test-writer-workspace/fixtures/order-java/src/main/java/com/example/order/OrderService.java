package com.example.order;

public class OrderService {

    private final OrderRepository orderRepository;
    private final PaymentGateway paymentGateway;
    private final InventoryClient inventoryClient;
    private final OrderEventPublisher eventPublisher;

    public OrderService(OrderRepository orderRepository,
                        PaymentGateway paymentGateway,
                        InventoryClient inventoryClient,
                        OrderEventPublisher eventPublisher) {
        this.orderRepository = orderRepository;
        this.paymentGateway = paymentGateway;
        this.inventoryClient = inventoryClient;
        this.eventPublisher = eventPublisher;
    }

    /**
     * 주문을 취소한다.
     *
     * <ul>
     *   <li>주문이 없으면 {@link OrderNotFoundException}.</li>
     *   <li>이미 취소된 주문이면 아무 작업도 하지 않는다 (멱등).</li>
     *   <li>배송이 시작된 주문(SHIPPED, DELIVERED)은 취소할 수 없다 → {@link IllegalStateException}.</li>
     *   <li>결제 완료(PAID) 주문은 배송비를 포함한 전액을 환불한다. 결제 대기(PENDING_PAYMENT) 주문은 환불하지 않는다.</li>
     *   <li>환불이 실패하면({@link PaymentException}) 주문 상태와 재고는 변경하지 않고 예외를 그대로 던진다.</li>
     *   <li>취소 시 모든 주문 라인의 재고를 복구하고, 주문을 CANCELLED 로 저장한 뒤
     *       {@link OrderCancelledEvent}(환불 금액 포함)를 발행한다.</li>
     * </ul>
     *
     * @param reason 취소 사유. 비어 있으면 안 된다 → {@link IllegalArgumentException}.
     */
    public void cancelOrder(long orderId, String reason) {
        if (reason == null || reason.isBlank()) {
            throw new IllegalArgumentException("cancel reason must not be blank");
        }

        Order order = orderRepository.findById(orderId)
                .orElseThrow(() -> new OrderNotFoundException(orderId));

        if (order.getStatus() == OrderStatus.SHIPPED || order.getStatus() == OrderStatus.DELIVERED) {
            throw new IllegalStateException("Order already shipped: " + orderId);
        }

        long refundedAmount = 0;
        if (order.getStatus() == OrderStatus.PAID || order.getStatus() == OrderStatus.CANCELLED) {
            refundedAmount = order.totalAmount();
            paymentGateway.refund(orderId, refundedAmount);
        }

        for (OrderLine line : order.getLines()) {
            inventoryClient.release(line.productId(), line.quantity());
        }

        order.markCancelled(reason);
        orderRepository.save(order);
        eventPublisher.publish(new OrderCancelledEvent(orderId, order.getCustomerId(), refundedAmount, reason));
    }
}
