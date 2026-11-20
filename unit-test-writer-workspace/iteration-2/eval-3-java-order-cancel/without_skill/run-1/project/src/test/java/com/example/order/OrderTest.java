package com.example.order;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class OrderTest {

    @Test
    @DisplayName("총 결제 금액은 상품 금액 합계에 배송비를 더한 값이다")
    void totalAmount_includesShippingFee() {
        // given
        Order order = new Order(1L, 10L,
                List.of(new OrderLine("P-1", 2, 5_000), new OrderLine("P-2", 1, 3_000)),
                2_500, OrderStatus.PAID);

        // when
        long total = order.totalAmount();

        // then
        assertThat(total).isEqualTo(15_500);
    }
}
