`OrderService.cancelOrder` 단위 테스트 21개를 추가했는데, 그중 2개가 지금 실패합니다. 실제 버그를 잡은 것입니다. 이미 취소된 주문을 다시 취소하면 **전액이 한 번 더 환불됩니다.**

## 발견한 버그: 이미 취소된 주문이 중복 환불됨

Javadoc에는 "이미 취소된 주문이면 아무 작업도 하지 않는다 (멱등)"라고 되어 있습니다. 하지만 코드에는 CANCELLED일 때 바로 빠져나가는 처리가 없고, 오히려 환불 조건에 `CANCELLED`가 들어가 있습니다.

```java
// OrderService.java:48
if (order.getStatus() == OrderStatus.PAID || order.getStatus() == OrderStatus.CANCELLED) {
```

그래서 이미 취소된 주문을 다시 취소하면 다음이 모두 다시 일어납니다.
- 전액 재환불
- 재고 재복구
- 다시 저장되면서 취소 사유가 새 값으로 바뀜
- `OrderCancelledEvent` 재발행

재시도나 사용자의 따닥 클릭만으로도 돈이 두 번 나갈 수 있는 경로입니다.

**제안하는 수정** (아직 적용하지 않았습니다):

```java
if (order.getStatus() == OrderStatus.CANCELLED) {
    return; // 멱등
}
// ... SHIPPED/DELIVERED 체크 ...
if (order.getStatus() == OrderStatus.PAID) {   // CANCELLED 제거
```

이 수정을 임시로 넣고 돌려 보면 21개가 모두 통과합니다. 확인한 뒤 원래 코드로 되돌렸습니다. 수정은 요청 범위 밖이라 운영 코드는 그대로 두었습니다. 원하시면 바로 적용하겠습니다.

## 테스트 실행 결과

`mvn test` 결과는 21개 중 19개 통과, 2개 실패입니다. 21개에는 기존 `OrderTest` 1개가 포함됩니다.

실패한 2개는 모두 위 버그 때문입니다.
- `AlreadyCancelledOrder.doesNotRefundAgain`: `paymentGateway.refund`가 호출됨
- `AlreadyCancelledOrder.isNoOp`: 저장, 재고 복구, 이벤트 발행이 일어남

## 테스트가 확인하는 것

새로 만든 파일은 `src/test/java/com/example/order/OrderServiceTest.java` 하나입니다. Javadoc에 적힌 규칙을 기준으로 시나리오별 `@Nested` 그룹으로 나눴습니다.

| 그룹 | 검증 내용 |
|---|---|
| 결제 완료(PAID) | 배송비 포함 전액(15,500) 환불, 주문 라인마다 재고 복구, CANCELLED와 취소 사유로 저장, 환불액이 담긴 이벤트 발행, 환불 → 재고 → 저장 → 발행 순서 |
| 결제 대기(PENDING_PAYMENT) | 환불하지 않음, 재고 복구 후 CANCELLED로 저장, 환불액 0인 이벤트 발행 |
| 이미 취소됨(CANCELLED) | 중복 환불 없음, 재고 복구·저장·발행 없음 (**현재 실패**) |
| 배송 시작(SHIPPED, DELIVERED) | `IllegalStateException`, 주문 상태 그대로, 외부 호출 없음 |
| 환불 실패 | 던져진 `PaymentException`을 그대로 다시 던짐, 상태·재고·저장·이벤트 모두 변경 없음 |
| 주문 없음 | `OrderNotFoundException`(메시지에 주문 ID 포함), 외부 호출 없음 |
| 취소 사유 없음 | null, 빈 문자열, 공백, 탭, 개행이면 `IllegalArgumentException`, 저장소 조회도 하지 않음 |

## 테스트를 쓰면서 정한 것

- **기준은 Javadoc**: 지금 코드가 하는 동작이 아니라 Javadoc에 적힌 동작을 기준으로 삼았습니다. 그래서 멱등성 테스트가 실패합니다. 이 테스트를 `@Disabled`로 끄면 버그가 묻히기 때문에 실패하는 상태로 남겼습니다. 빌드를 당장 통과시켜야 한다면 위 수정을 적용하는 쪽을 권합니다.
- **Mock 대상**: `Order`와 `OrderLine`은 단순한 도메인 객체라 mock 없이 실제 객체를 썼습니다. mock은 4개 협력 객체(Repository, PaymentGateway, InventoryClient, EventPublisher)에만 썼습니다.
- **스타일**: 기존 `OrderTest`에 맞췄습니다. AssertJ, 한글 `@DisplayName`, given/when/then 주석을 썼고, Mockito는 BDD 스타일(`given`/`willThrow`)로 썼습니다.
- **pom.xml**: 변경하지 않았습니다. 필요한 의존성은 이미 들어 있습니다.
