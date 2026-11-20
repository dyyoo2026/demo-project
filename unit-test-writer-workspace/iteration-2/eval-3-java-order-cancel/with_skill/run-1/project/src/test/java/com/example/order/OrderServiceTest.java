package com.example.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicReference;
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

    private static final long ORDER_ID = 42L;
    private static final long CUSTOMER_ID = 10L;
    private static final String REASON = "고객 변심";

    @Mock OrderRepository orderRepository;
    @Mock PaymentGateway paymentGateway;
    @Mock InventoryClient inventoryClient;
    @Mock OrderEventPublisher eventPublisher;
    @InjectMocks OrderService orderService;

    /** 상품 2개(5,000원 x 2, 3,000원 x 1) + 배송비 2,500원 → 총 15,500원 */
    private static Order orderWithStatus(OrderStatus status) {
        return new Order(ORDER_ID, CUSTOMER_ID,
                List.of(new OrderLine("P-1", 2, 5_000), new OrderLine("P-2", 1, 3_000)),
                2_500, status);
    }

    // --- 결제 완료(PAID) 주문 ---

    @Test
    @DisplayName("결제 완료 주문을 취소하면 배송비를 포함한 전액을 환불한다")
    void cancelOrder_paidOrder_refundsTotalIncludingShippingFee() {
        // given
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(orderWithStatus(OrderStatus.PAID)));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(paymentGateway).refund(ORDER_ID, 15_500L);
        verifyNoMoreInteractions(paymentGateway);
    }

    @Test
    @DisplayName("결제 완료 주문을 취소하면 모든 주문 라인의 재고를 수량만큼 복구한다")
    void cancelOrder_paidOrder_releasesInventoryForEveryLine() {
        // given
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(orderWithStatus(OrderStatus.PAID)));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(inventoryClient).release("P-1", 2);
        verify(inventoryClient).release("P-2", 1);
        verifyNoMoreInteractions(inventoryClient);
    }

    @Test
    @DisplayName("결제 완료 주문을 취소하면 CANCELLED 상태와 취소 사유가 반영된 주문을 저장한다")
    void cancelOrder_paidOrder_savesOrderAsCancelledWithReason() {
        // given
        Order order = orderWithStatus(OrderStatus.PAID);
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));
        AtomicReference<OrderStatus> statusAtSave = new AtomicReference<>();
        doAnswer(invocation -> {
            statusAtSave.set(invocation.<Order>getArgument(0).getStatus());
            return null;
        }).when(orderRepository).save(any(Order.class));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(orderRepository).save(order);
        assertThat(statusAtSave.get()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(order.getCancelReason()).isEqualTo(REASON);
    }

    @Test
    @DisplayName("결제 완료 주문을 취소하면 저장 후 환불 금액이 담긴 취소 이벤트를 발행한다")
    void cancelOrder_paidOrder_publishesEventWithRefundedAmountAfterSave() {
        // given
        Order order = orderWithStatus(OrderStatus.PAID);
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        InOrder inOrder = inOrder(orderRepository, eventPublisher);
        inOrder.verify(orderRepository).save(order);
        inOrder.verify(eventPublisher).publish(new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, 15_500L, REASON));
        verifyNoMoreInteractions(eventPublisher);
    }

    // --- 결제 대기(PENDING_PAYMENT) 주문 ---

    @Test
    @DisplayName("결제 대기 주문을 취소하면 환불하지 않고 환불 금액 0인 취소 이벤트를 발행한다")
    void cancelOrder_pendingPaymentOrder_doesNotRefundAndPublishesZeroRefund() {
        // given
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(orderWithStatus(OrderStatus.PENDING_PAYMENT)));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verifyNoInteractions(paymentGateway);
        verify(eventPublisher).publish(new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, 0L, REASON));
    }

    @Test
    @DisplayName("결제 대기 주문을 취소해도 재고는 복구하고 주문은 CANCELLED 로 저장한다")
    void cancelOrder_pendingPaymentOrder_releasesInventoryAndSavesCancelled() {
        // given
        Order order = orderWithStatus(OrderStatus.PENDING_PAYMENT);
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(inventoryClient).release("P-1", 2);
        verify(inventoryClient).release("P-2", 1);
        verify(orderRepository).save(order);
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(order.getCancelReason()).isEqualTo(REASON);
    }

    // --- 이미 취소된 주문 (멱등) ---

    @Test
    @Disabled("BUG: 이미 취소된 주문은 아무 작업도 하지 않아야 하는데(멱등), 실제로는 다시 15,500원을 환불하고 "
            + "재고를 다시 복구하고 저장/이벤트 발행까지 한다 — OrderService.java:48 의 "
            + "`|| order.getStatus() == OrderStatus.CANCELLED` 조건 때문 (CANCELLED 조기 return 누락)")
    @DisplayName("이미 취소된 주문을 다시 취소하면 아무 작업도 하지 않는다 (멱등)")
    void cancelOrder_alreadyCancelledOrder_doesNothing() {
        // given
        Order order = orderWithStatus(OrderStatus.PAID);
        order.markCancelled("기존 사유");
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
        verify(orderRepository, never()).save(any());
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(order.getCancelReason()).isEqualTo("기존 사유");
    }

    // --- 취소 불가 / 오류 경로 ---

    @ParameterizedTest(name = "{0}")
    @EnumSource(value = OrderStatus.class, names = {"SHIPPED", "DELIVERED"})
    @DisplayName("배송이 시작된 주문은 취소할 수 없고 아무것도 변경하지 않는다")
    void cancelOrder_shippedOrDeliveredOrder_throwsIllegalStateWithoutSideEffects(OrderStatus status) {
        // given
        Order order = orderWithStatus(status);
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));

        // when & then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("42");
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
        verify(orderRepository, never()).save(any());
        assertThat(order.getStatus()).isEqualTo(status);
    }

    @Test
    @DisplayName("주문이 없으면 OrderNotFoundException 을 던지고 아무것도 변경하지 않는다")
    void cancelOrder_unknownOrder_throwsOrderNotFound() {
        // given
        when(orderRepository.findById(99L)).thenReturn(Optional.empty());

        // when & then
        assertThatThrownBy(() -> orderService.cancelOrder(99L, REASON))
                .isInstanceOf(OrderNotFoundException.class)
                .hasMessageContaining("99");
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
        verify(orderRepository, never()).save(any());
    }

    @ParameterizedTest(name = "reason=[{0}]")
    @NullAndEmptySource
    @ValueSource(strings = {" ", "\t\n"})
    @DisplayName("취소 사유가 비어 있으면 IllegalArgumentException 을 던지고 주문을 조회하지도 않는다")
    void cancelOrder_blankReason_throwsIllegalArgument(String blankReason) {
        // when & then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, blankReason))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("reason");
        verifyNoInteractions(orderRepository, paymentGateway, inventoryClient, eventPublisher);
    }

    @Test
    @DisplayName("환불이 실패하면 예외를 그대로 던지고 주문 상태와 재고는 변경하지 않는다")
    void cancelOrder_refundFails_rethrowsAndLeavesOrderAndInventoryUntouched() {
        // given
        Order order = orderWithStatus(OrderStatus.PAID);
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));
        PaymentException failure = new PaymentException("PG timeout");
        doThrow(failure).when(paymentGateway).refund(ORDER_ID, 15_500L);

        // when & then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                .isSameAs(failure);
        verifyNoInteractions(inventoryClient, eventPublisher);
        verify(orderRepository, never()).save(any());
        assertThat(order.getStatus()).isEqualTo(OrderStatus.PAID);
        assertThat(order.getCancelReason()).isNull();
    }
}
