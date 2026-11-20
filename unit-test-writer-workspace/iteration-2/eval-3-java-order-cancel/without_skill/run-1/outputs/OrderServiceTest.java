package com.example.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
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

    // 상품 금액 13,000 (5,000 x 2 + 3,000 x 1) + 배송비 2,500 = 15,500
    private static final List<OrderLine> LINES = List.of(
            new OrderLine("P-1", 2, 5_000),
            new OrderLine("P-2", 1, 3_000));
    private static final long SHIPPING_FEE = 2_500;
    private static final long TOTAL_WITH_SHIPPING = 15_500;

    @Mock
    private OrderRepository orderRepository;
    @Mock
    private PaymentGateway paymentGateway;
    @Mock
    private InventoryClient inventoryClient;
    @Mock
    private OrderEventPublisher eventPublisher;

    @InjectMocks
    private OrderService orderService;

    private Order givenOrder(OrderStatus status) {
        Order order = new Order(ORDER_ID, CUSTOMER_ID, LINES, SHIPPING_FEE, status);
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));
        return order;
    }

    // ---------------------------------------------------------------- 입력 검증

    @ParameterizedTest(name = "reason = [{0}]")
    @NullAndEmptySource
    @ValueSource(strings = {" ", "   ", "\t\n"})
    @DisplayName("취소 사유가 null 이거나 공백이면 IllegalArgumentException 을 던지고 아무것도 조회/변경하지 않는다")
    void cancelOrder_blankReason_throwsIllegalArgument(String reason) {
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, reason))
                .isInstanceOf(IllegalArgumentException.class);

        verifyNoInteractions(orderRepository, paymentGateway, inventoryClient, eventPublisher);
    }

    // ---------------------------------------------------------------- 주문 없음

    @Test
    @DisplayName("주문이 없으면 OrderNotFoundException 을 던지고 환불/재고/저장/이벤트는 일어나지 않는다")
    void cancelOrder_orderNotFound_throwsOrderNotFound() {
        // given
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.empty());

        // when & then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                .isInstanceOf(OrderNotFoundException.class)
                .hasMessageContaining(String.valueOf(ORDER_ID));

        verify(orderRepository, never()).save(any());
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
    }

    // ---------------------------------------------------------------- 배송 시작된 주문

    @ParameterizedTest(name = "status = {0}")
    @EnumSource(value = OrderStatus.class, names = {"SHIPPED", "DELIVERED"})
    @DisplayName("배송이 시작된 주문(SHIPPED, DELIVERED)은 IllegalStateException 을 던지고 상태를 바꾸지 않는다")
    void cancelOrder_shippedOrDelivered_throwsIllegalState(OrderStatus status) {
        // given
        Order order = givenOrder(status);

        // when & then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                .isInstanceOf(IllegalStateException.class);

        assertThat(order.getStatus()).isEqualTo(status);
        assertThat(order.getCancelReason()).isNull();
        verify(orderRepository, never()).save(any());
        verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
    }

    // ---------------------------------------------------------------- 결제 완료(PAID) 주문

    @Test
    @DisplayName("결제 완료 주문은 배송비를 포함한 전액을 환불한다")
    void cancelOrder_paid_refundsTotalIncludingShippingFee() {
        // given
        givenOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(paymentGateway).refund(ORDER_ID, TOTAL_WITH_SHIPPING);
        verifyNoMoreInteractions(paymentGateway);
    }

    @Test
    @DisplayName("결제 완료 주문을 취소하면 모든 라인의 재고를 복구하고 CANCELLED 로 저장한다")
    void cancelOrder_paid_releasesInventoryAndSavesCancelled() {
        // given
        Order order = givenOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(inventoryClient).release("P-1", 2);
        verify(inventoryClient).release("P-2", 1);
        verifyNoMoreInteractions(inventoryClient);

        verify(orderRepository).save(order);
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(order.getCancelReason()).isEqualTo(REASON);
    }

    @Test
    @DisplayName("결제 완료 주문을 취소하면 환불 금액이 담긴 OrderCancelledEvent 를 발행한다")
    void cancelOrder_paid_publishesEventWithRefundedAmount() {
        // given
        givenOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(eventPublisher).publish(
                new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, TOTAL_WITH_SHIPPING, REASON));
        verifyNoMoreInteractions(eventPublisher);
    }

    @Test
    @DisplayName("주문은 저장되는 시점에 이미 CANCELLED 상태여야 한다")
    void cancelOrder_orderIsCancelledAtTimeOfSave() {
        // given
        givenOrder(OrderStatus.PAID);
        AtomicReference<OrderStatus> statusAtSave = new AtomicReference<>();
        AtomicReference<String> reasonAtSave = new AtomicReference<>();
        doAnswer(invocation -> {
            Order saved = invocation.getArgument(0);
            statusAtSave.set(saved.getStatus());
            reasonAtSave.set(saved.getCancelReason());
            return null;
        }).when(orderRepository).save(any(Order.class));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        assertThat(statusAtSave.get()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(reasonAtSave.get()).isEqualTo(REASON);
    }

    @Test
    @DisplayName("재고 복구 → 주문 저장 → 이벤트 발행 순서로 처리한다")
    void cancelOrder_releasesThenSavesThenPublishes() {
        // given
        Order order = givenOrder(OrderStatus.PAID);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        InOrder inOrder = inOrder(inventoryClient, orderRepository, eventPublisher);
        inOrder.verify(inventoryClient).release("P-1", 2);
        inOrder.verify(inventoryClient).release("P-2", 1);
        inOrder.verify(orderRepository).save(order);
        inOrder.verify(eventPublisher).publish(any(OrderCancelledEvent.class));
    }

    // ---------------------------------------------------------------- 결제 대기(PENDING_PAYMENT) 주문

    @Test
    @DisplayName("결제 대기 주문은 환불하지 않고, 재고 복구/저장 후 환불 금액 0 으로 이벤트를 발행한다")
    void cancelOrder_pendingPayment_doesNotRefund() {
        // given
        Order order = givenOrder(OrderStatus.PENDING_PAYMENT);

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verifyNoInteractions(paymentGateway);

        verify(inventoryClient).release("P-1", 2);
        verify(inventoryClient).release("P-2", 1);
        verifyNoMoreInteractions(inventoryClient);

        verify(orderRepository).save(order);
        assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(order.getCancelReason()).isEqualTo(REASON);

        verify(eventPublisher).publish(new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, 0L, REASON));
    }

    // ---------------------------------------------------------------- 환불 실패

    @Test
    @DisplayName("환불이 실패하면 PaymentException 을 그대로 던지고 주문 상태/재고/이벤트는 변경하지 않는다")
    void cancelOrder_refundFails_propagatesAndLeavesOrderUntouched() {
        // given
        Order order = givenOrder(OrderStatus.PAID);
        PaymentException failure = new PaymentException("PG 오류");
        doThrow(failure).when(paymentGateway).refund(anyLong(), anyLong());

        // when & then
        assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                .isSameAs(failure);

        assertThat(order.getStatus()).isEqualTo(OrderStatus.PAID);
        assertThat(order.getCancelReason()).isNull();
        verify(orderRepository, never()).save(any());
        verifyNoInteractions(inventoryClient, eventPublisher);
    }

    // ---------------------------------------------------------------- 이미 취소된 주문 (멱등)

    @Test
    @DisplayName("이미 취소된 주문은 아무 작업도 하지 않는다 (멱등) - 환불/재고 복구/저장/이벤트 없음")
    void cancelOrder_alreadyCancelled_isNoOp() {
        // given
        Order order = new Order(ORDER_ID, CUSTOMER_ID, LINES, SHIPPING_FEE, OrderStatus.PAID);
        order.markCancelled("최초 취소 사유");
        when(orderRepository.findById(ORDER_ID)).thenReturn(Optional.of(order));

        // when
        orderService.cancelOrder(ORDER_ID, REASON);

        // then
        verify(paymentGateway, never()).refund(anyLong(), anyLong());
        verify(inventoryClient, never()).release(anyString(), anyInt());
        verify(orderRepository, never()).save(any());
        verifyNoInteractions(eventPublisher);

        assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
        assertThat(order.getCancelReason()).isEqualTo("최초 취소 사유");
    }
}
