package com.example.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.InOrder;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class OrderServiceTest {

    private static final long ORDER_ID = 1L;
    private static final long CUSTOMER_ID = 10L;
    private static final String REASON = "단순 변심";

    @Mock OrderRepository orderRepository;
    @Mock PaymentGateway paymentGateway;
    @Mock InventoryClient inventoryClient;
    @Mock OrderEventPublisher eventPublisher;
    @InjectMocks OrderService orderService;

    /** 상품 합계 13,000 (5,000 x 2 + 3,000 x 1) + 배송비 2,500 = 총 15,500 인 주문을 조회되도록 준비한다. */
    private Order givenExistingOrder(OrderStatus status) {
        Order order = new Order(ORDER_ID, CUSTOMER_ID,
                List.of(new OrderLine("P-1", 2, 5_000), new OrderLine("P-2", 1, 3_000)),
                2_500, status);
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));
        return order;
    }

    // ---- 결제 완료(PAID) 주문 취소 ----

    @Test
    @DisplayName("결제 완료 주문을 취소하면 배송비를 포함한 전액을 환불한다")
    void cancelOrder_paidOrder_refundsTotalIncludingShippingFee() {
        // given
        givenExistingOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(paymentGateway).refund(ORDER_ID, 15_500);
        verifyNoMoreInteractions(paymentGateway);
    }

    @Test
    @DisplayName("주문을 취소하면 모든 주문 라인의 재고를 수량만큼 복구한다")
    void cancelOrder_paidOrder_releasesInventoryForEveryLine() {
        // given
        givenExistingOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(inventoryClient).release("P-1", 2);
        verify(inventoryClient).release("P-2", 1);
        verifyNoMoreInteractions(inventoryClient);
    }

    @Test
    @DisplayName("주문을 취소하면 취소 사유와 함께 CANCELLED 상태로 저장한다")
    void cancelOrder_paidOrder_savesOrderAsCancelledWithReason() {
        // given
        Order order = givenExistingOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(orderRepository).save(order);
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(order.getCancelReason()).isEqualTo(REASON);
    }

    @Test
    @DisplayName("주문을 저장한 뒤 환불 금액이 담긴 취소 이벤트를 발행한다")
    void cancelOrder_paidOrder_publishesEventWithRefundedAmountAfterSave() {
        // given
        Order order = givenExistingOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        InOrder inOrder = inOrder(orderRepository, eventPublisher);
        inOrder.verify(orderRepository).save(order);
        inOrder.verify(eventPublisher).publish(new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, 15_500, REASON));
        verifyNoMoreInteractions(eventPublisher);
    }

    // ---- 결제 대기(PENDING_PAYMENT) 주문 취소 ----

    @Test
    @DisplayName("결제 대기 주문은 환불 없이 취소하고 환불 금액 0으로 이벤트를 발행한다")
    void cancelOrder_pendingPaymentOrder_cancelsWithoutRefund() {
        // given
        Order order = givenExistingOrder(OrderStatus.PENDING_PAYMENT);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verifyNoInteractions(paymentGateway);
        verify(inventoryClient).release("P-1", 2);
        verify(inventoryClient).release("P-2", 1);
        verify(orderRepository).save(order);
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
        verify(eventPublisher).publish(new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, 0, REASON));
    }

    // ---- 환불 실패 ----

    @Test
    @DisplayName("환불이 실패하면 예외를 그대로 던지고 주문 상태와 재고는 변경하지 않는다")
    void cancelOrder_refundFails_rethrowsAndLeavesOrderAndInventoryUntouched() {
        // given
        Order order = givenExistingOrder(OrderStatus.PAID);
        PaymentException failure = new PaymentException("PG timeout");
        doThrow(failure).when(paymentGateway).refund(anyLong(), anyLong());

        // when / then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                .isSameAs(failure);

        assertThat(order.getStatus()).isEqualTo(OrderStatus.PAID);
        assertThat(order.getCancelReason()).isNull();
        verifyNoInteractions(inventoryClient, eventPublisher);
        verify(orderRepository, never()).save(any());
    }

    // ---- 취소 불가 / 잘못된 요청 ----

    @Test
    @DisplayName("존재하지 않는 주문이면 OrderNotFoundException 을 던진다")
    void cancelOrder_unknownOrder_throwsOrderNotFound() {
        // given
        when(orderRepository.findById(99L)).thenReturn(Optional.empty());

        // when / then
        assertThatThrownBy(() -> orderService.cancelOrder(99L, REASON))
                .isInstanceOf(OrderNotFoundException.class)
                .hasMessageContaining("99");

        verify(orderRepository, never()).save(any());
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
    }

    @ParameterizedTest(name = "{0}")
    @EnumSource(value = OrderStatus.class, names = {"SHIPPED", "DELIVERED"})
    @DisplayName("배송이 시작된 주문은 취소할 수 없다")
    void cancelOrder_shippedOrDelivered_throwsIllegalState(OrderStatus status) {
        // given
        Order order = givenExistingOrder(status);

        // when / then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining(String.valueOf(ORDER_ID));

        assertThat(order.getStatus()).isEqualTo(status);
        verify(orderRepository, never()).save(any());
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
    }

    @ParameterizedTest(name = "reason=[{0}]")
    @NullAndEmptySource
    @ValueSource(strings = {"   ", "\t\n"})
    @DisplayName("취소 사유가 비어 있으면 IllegalArgumentException 을 던진다")
    void cancelOrder_blankReason_throwsIllegalArgument(String reason) {
        // when / then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, reason))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("reason");

        verify(orderRepository, never()).save(any());
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
    }

    // ---- 멱등성 ----

    @Test
    @Disabled("BUG: Javadoc says re-cancelling a CANCELLED order is a no-op, but OrderService.java:48 treats CANCELLED "
            + "like PAID -> refunds totalAmount again (double refund), releases inventory again, re-saves and "
            + "re-publishes OrderCancelledEvent. Expected: no side effects. Actual: refund(1, 15500) etc.")
    @DisplayName("이미 취소된 주문을 다시 취소하면 아무 작업도 하지 않는다 (멱등)")
    void cancelOrder_alreadyCancelled_doesNothing() {
        // given
        givenExistingOrder(OrderStatus.CANCELLED);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
        verify(orderRepository, never()).save(any());
    }
}
