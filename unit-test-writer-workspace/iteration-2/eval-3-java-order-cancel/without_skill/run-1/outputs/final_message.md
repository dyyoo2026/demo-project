`OrderService.cancelOrder` 단위 테스트를 만들었습니다. 실행해 보니 17개 중 16개가 통과하고 1개가 실패합니다. 실패한 1개는 **실제 버그**를 잡은 것입니다.

## 발견한 버그: 이미 취소된 주문을 다시 취소하면 또 환불됩니다

Javadoc에는 "이미 취소된 주문이면 아무 작업도 하지 않는다 (멱등)"이라고 되어 있지만, 실제 코드는 `CANCELLED`를 `PAID`와 똑같이 처리합니다.

```java
// OrderService.java:48
if (order.getStatus() == OrderStatus.PAID || order.getStatus() == OrderStatus.CANCELLED) {
```

그래서 같은 주문에 취소 요청이 두 번 들어오면(재시도, 버튼 중복 클릭 등) 이런 일이 생깁니다.
- 전액이 **한 번 더 환불**됩니다.
- 재고가 **한 번 더 복구**됩니다.
- 처음 입력한 취소 사유가 새 사유로 덮어써집니다.
- `OrderCancelledEvent`가 **중복 발행**됩니다.

실패하는 테스트는 `cancelOrder_alreadyCancelled_isNoOp`입니다. 테스트만 만들어 달라고 하셔서 운영 코드는 수정하지 않았고, 버그가 묻히지 않도록 `@Disabled`도 붙이지 않았습니다. 고치려면 아래처럼 바꾸면 됩니다.

```java
if (order.getStatus() == OrderStatus.CANCELLED) {
    return; // 멱등
}
// ...
if (order.getStatus() == OrderStatus.PAID) {   // "|| CANCELLED" 제거
```

이 수정을 임시로 적용해 돌려 보면 17개가 모두 통과합니다. 확인한 뒤 코드는 원래대로 되돌렸습니다.

## 추가한 파일

`src/test/java/com/example/order/OrderServiceTest.java` (JUnit 5 + Mockito, `@ExtendWith(MockitoExtension.class)`)

| 케이스 | 확인 내용 |
|---|---|
| 취소 사유가 null, "", 공백 (파라미터화 5건) | `IllegalArgumentException`, 어떤 의존성도 호출하지 않음 |
| 주문 없음 | `OrderNotFoundException`, 환불·재고·저장·이벤트 없음 |
| SHIPPED / DELIVERED (파라미터화 2건) | `IllegalStateException`, 주문 상태 그대로 |
| PAID: 환불 금액 | 배송비를 **포함한** 전액(13,000 + 2,500 = 15,500)을 환불 |
| PAID: 재고와 저장 | 모든 주문 라인의 재고를 복구하고, 상태 `CANCELLED`와 사유를 저장 |
| PAID: 이벤트 | `OrderCancelledEvent(orderId, customerId, 15500, reason)` 발행 |
| 저장 시점의 상태 | `save()`가 호출되는 순간 이미 `CANCELLED`여야 함 |
| 처리 순서 | 재고 복구 → 저장 → 이벤트 발행 |
| PENDING_PAYMENT | 환불하지 않고, 재고 복구·저장 후 환불 금액 0으로 이벤트 발행 |
| 환불 실패 | 같은 `PaymentException`을 그대로 던지고, 상태·재고·저장·이벤트 모두 변경 없음 |
| 이미 CANCELLED | 아무 작업도 하지 않음 ← **현재 실패** |

## 판단한 내용
- **기준:** 현재 코드 동작이 아니라 Javadoc 명세를 기준으로 테스트를 작성했습니다. 명세와 코드가 다른 부분은 버그로 보았습니다.
- **목 대상:** 저장소, 결제, 재고, 이벤트 발행 인터페이스 네 개만 목으로 만들었습니다. `Order`와 `OrderLine`은 값 객체라 실제 객체를 사용했습니다.
- **스타일:** pom에 AssertJ가 있고 기존 `OrderTest`도 쓰고 있어서 검증은 AssertJ로 했습니다. 한글 `@DisplayName`과 given/when/then 구조도 기존 스타일에 맞췄습니다.
- **처리 순서:** 명세에 "저장한 뒤 발행"이라고 되어 있어 재고 복구 → 저장 → 발행 순서를 검증합니다. 환불을 먼저 하는지는 따로 순서로 검증하지 않고, "환불이 실패하면 재고를 건드리지 않는다"는 테스트로 확인합니다.
- **테스트 검증:** 테스트가 제대로 잡아내는지 보려고 운영 코드를 일부러 틀리게 바꿔 보았습니다(배송비를 빼고 환불, 발행을 저장보다 먼저, 상태 변경 전에 저장, 환불보다 재고 복구를 먼저). 네 경우 모두 해당 테스트가 실패했고, 확인 후 코드는 원래대로 되돌렸습니다.

## 실행 결과
```
mvn test
Tests run: 17, Failures: 1, Errors: 0, Skipped: 0   (기존 OrderTest 1개 포함)
FAIL  OrderServiceTest.cancelOrder_alreadyCancelled_isNoOp
      → paymentGateway.refund(1, 15500) 가 호출되면 안 되는데 호출됨
```

원하시면 위 버그 수정도 바로 적용해 드리겠습니다.
