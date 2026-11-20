package com.example.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.willThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.verifyNoMoreInteractions;

import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class OrderServiceTest {

    private static final long ORDER_ID = 1L;
    private static final long CUSTOMER_ID = 10L;
    private static final String REASON = "단순 변심";

    // 상품 금액 합계 = 2 * 5,000 + 1 * 3,000 = 13,000 / 배송비 = 2,500 / 총액 = 15,500
    private static final List<OrderLine> LINES = List.of(
            new OrderLine("P-1", 2, 5_000),
            new OrderLine("P-2", 1, 3_000));
    private static final long SHIPPING_FEE = 2_500;
    private static final long TOTAL_AMOUNT = 15_500;

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
        given(orderRepository.findById(ORDER_ID)).willReturn(Optional.of(order));
        return order;
    }

    @Nested
    @DisplayName("결제 완료(PAID) 주문을 취소하면")
    class PaidOrder {

        @Test
        @DisplayName("배송비를 포함한 전액을 환불한다")
        void refundsTotalAmountIncludingShippingFee() {
            // given
            givenOrder(OrderStatus.PAID);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            verify(paymentGateway).refund(ORDER_ID, TOTAL_AMOUNT);
            verifyNoMoreInteractions(paymentGateway);
        }

        @Test
        @DisplayName("모든 주문 라인의 재고를 복구한다")
        void releasesInventoryForEveryLine() {
            // given
            givenOrder(OrderStatus.PAID);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            verify(inventoryClient).release("P-1", 2);
            verify(inventoryClient).release("P-2", 1);
            verifyNoMoreInteractions(inventoryClient);
        }

        @Test
        @DisplayName("주문을 CANCELLED 상태와 취소 사유로 저장한다")
        void savesOrderAsCancelledWithReason() {
            // given
            givenOrder(OrderStatus.PAID);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            ArgumentCaptor<Order> saved = ArgumentCaptor.forClass(Order.class);
            verify(orderRepository).save(saved.capture());
            assertThat(saved.getValue().getId()).isEqualTo(ORDER_ID);
            assertThat(saved.getValue().getStatus()).isEqualTo(OrderStatus.CANCELLED);
            assertThat(saved.getValue().getCancelReason()).isEqualTo(REASON);
        }

        @Test
        @DisplayName("환불 금액을 담은 OrderCancelledEvent 를 발행한다")
        void publishesEventWithRefundedAmount() {
            // given
            givenOrder(OrderStatus.PAID);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            verify(eventPublisher).publish(
                    new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, TOTAL_AMOUNT, REASON));
            verifyNoMoreInteractions(eventPublisher);
        }

        @Test
        @DisplayName("환불 → 재고 복구 → 저장 → 이벤트 발행 순서로 처리한다")
        void processesStepsInOrder() {
            // given
            Order order = givenOrder(OrderStatus.PAID);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            InOrder inOrder = inOrder(paymentGateway, inventoryClient, orderRepository, eventPublisher);
            inOrder.verify(paymentGateway).refund(ORDER_ID, TOTAL_AMOUNT);
            inOrder.verify(inventoryClient).release("P-1", 2);
            inOrder.verify(inventoryClient).release("P-2", 1);
            inOrder.verify(orderRepository).save(order);
            inOrder.verify(eventPublisher).publish(any(OrderCancelledEvent.class));
        }
    }

    @Nested
    @DisplayName("결제 대기(PENDING_PAYMENT) 주문을 취소하면")
    class PendingPaymentOrder {

        @Test
        @DisplayName("환불하지 않는다")
        void doesNotRefund() {
            // given
            givenOrder(OrderStatus.PENDING_PAYMENT);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            verifyNoInteractions(paymentGateway);
        }

        @Test
        @DisplayName("재고를 복구하고 CANCELLED 로 저장한다")
        void releasesInventoryAndSavesAsCancelled() {
            // given
            Order order = givenOrder(OrderStatus.PENDING_PAYMENT);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            verify(inventoryClient).release("P-1", 2);
            verify(inventoryClient).release("P-2", 1);
            verify(orderRepository).save(order);
            assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
            assertThat(order.getCancelReason()).isEqualTo(REASON);
        }

        @Test
        @DisplayName("환불 금액 0 으로 OrderCancelledEvent 를 발행한다")
        void publishesEventWithZeroRefund() {
            // given
            givenOrder(OrderStatus.PENDING_PAYMENT);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            verify(eventPublisher).publish(new OrderCancelledEvent(ORDER_ID, CUSTOMER_ID, 0L, REASON));
        }
    }

    @Nested
    @DisplayName("이미 취소된(CANCELLED) 주문을 다시 취소하면")
    class AlreadyCancelledOrder {

        @Test
        @DisplayName("중복 환불하지 않는다")
        void doesNotRefundAgain() {
            // given
            givenOrder(OrderStatus.CANCELLED);

            // when
            orderService.cancelOrder(ORDER_ID, REASON);

            // then
            verifyNoInteractions(paymentGateway);
        }

        @Test
        @DisplayName("예외 없이 재고 복구·저장·이벤트 발행도 하지 않는다 (멱등)")
        void isNoOp() {
            // given
            Order order = givenOrder(OrderStatus.CANCELLED);

            // when
            orderService.cancelOrder(ORDER_ID, "다른 사유");

            // then
            verifyNoInteractions(inventoryClient, eventPublisher);
            verify(orderRepository, never()).save(any());
            assertThat(order.getStatus()).isEqualTo(OrderStatus.CANCELLED);
            assertThat(order.getCancelReason()).isNull();
        }
    }

    @Nested
    @DisplayName("배송이 시작된 주문은")
    class ShippedOrder {

        @ParameterizedTest(name = "{0} 상태면 IllegalStateException 을 던지고 아무것도 변경하지 않는다")
        @EnumSource(value = OrderStatus.class, names = {"SHIPPED", "DELIVERED"})
        void cannotBeCancelled(OrderStatus status) {
            // given
            Order order = givenOrder(status);

            // when / then
            assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining(String.valueOf(ORDER_ID));

            assertThat(order.getStatus()).isEqualTo(status);
            assertThat(order.getCancelReason()).isNull();
            verify(orderRepository, never()).save(any());
            verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
        }
    }

    @Nested
    @DisplayName("환불이 실패하면")
    class RefundFailure {

        @Test
        @DisplayName("PaymentException 을 그대로 던지고 주문 상태와 재고는 변경하지 않는다")
        void propagatesExceptionWithoutSideEffects() {
            // given
            Order order = givenOrder(OrderStatus.PAID);
            PaymentException failure = new PaymentException("PG timeout");
            willThrow(failure).given(paymentGateway).refund(anyLong(), anyLong());

            // when / then
            assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                    .isSameAs(failure);

            assertThat(order.getStatus()).isEqualTo(OrderStatus.PAID);
            assertThat(order.getCancelReason()).isNull();
            verify(inventoryClient, never()).release(anyString(), anyInt());
            verify(orderRepository, never()).save(any());
            verifyNoInteractions(eventPublisher);
        }
    }

    @Nested
    @DisplayName("주문이 존재하지 않으면")
    class OrderNotFound {

        @Test
        @DisplayName("OrderNotFoundException 을 던지고 아무것도 변경하지 않는다")
        void throwsOrderNotFoundException() {
            // given
            given(orderRepository.findById(ORDER_ID)).willReturn(Optional.empty());

            // when / then
            assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, REASON))
                    .isInstanceOf(OrderNotFoundException.class)
                    .hasMessageContaining(String.valueOf(ORDER_ID));

            verify(orderRepository, never()).save(any());
            verifyNoInteractions(paymentGateway, inventoryClient, eventPublisher);
        }
    }

    @Nested
    @DisplayName("취소 사유가 비어 있으면")
    class BlankReason {

        @ParameterizedTest(name = "reason = [{0}]")
        @NullAndEmptySource
        @ValueSource(strings = {" ", "   ", "\t", "\n"})
        @DisplayName("IllegalArgumentException 을 던지고 주문을 조회하지도 않는다")
        void throwsIllegalArgumentException(String reason) {
            // when / then
            assertThatThrownBy(() -> orderService.cancelOrder(ORDER_ID, reason))
                    .isInstanceOf(IllegalArgumentException.class);

            verifyNoInteractions(orderRepository, paymentGateway, inventoryClient, eventPublisher);
        }
    }
}
