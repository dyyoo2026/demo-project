## 테스트 작성 결과

`OrderService.cancelOrder` 테스트를 Javadoc의 계약 기준으로 작성했습니다. **이미 취소된 주문을 다시 취소하면 또 환불되는 버그**를 하나 찾았습니다. 해당 테스트는 `@Disabled`로 표시해 두었습니다.

- **파일:** `src/test/java/com/example/order/OrderServiceTest.java` (테스트 메서드 11개, 파라미터화 포함 실행 15건)
- **실행:** `mvn test` → 통과 15건(기존 `OrderTest` 포함), 알려진 버그로 skip 1건, 실패 0건
- **스타일:** 기존 `OrderTest`와 같은 방식입니다. 한글 `@DisplayName`, `메서드_조건_결과` 형식의 이름, given/when/then 주석, AssertJ를 썼습니다. Mockito는 `@ExtendWith(MockitoExtension.class)`에 `@Mock`/`@InjectMocks`로 붙였습니다. 의존성은 추가하지 않았습니다.

### 커버한 동작
- **결제 완료(PAID) 주문**
  - 배송비를 포함한 전액(15,500원)을 환불한다.
  - 모든 주문 라인의 재고를 수량만큼 복구한다.
  - 주문을 CANCELLED 상태와 취소 사유가 반영된 채로 저장한다. 저장하는 시점에 이미 CANCELLED인지도 확인합니다.
  - 저장한 **뒤에** 환불 금액이 담긴 `OrderCancelledEvent`를 발행한다.
- **결제 대기(PENDING_PAYMENT) 주문**
  - 환불하지 않고, 환불 금액이 0인 이벤트를 발행한다.
  - 재고는 복구하고 주문은 CANCELLED로 저장한다.
- **취소할 수 없는 경우와 오류 경로**
  - SHIPPED, DELIVERED 주문은 `IllegalStateException`을 던지고 아무것도 바꾸지 않는다.
  - 주문이 없으면 `OrderNotFoundException`을 던진다.
  - 취소 사유가 `null`, `""`, `" "`, `"\t\n"`이면 `IllegalArgumentException`을 던지고 저장소 조회도 하지 않는다.
  - 환불이 실패하면 같은 `PaymentException`을 그대로 던지고, 재고 복구, 저장, 이벤트 발행을 하지 않는다. 주문은 PAID 상태로 남는다.
- **이미 취소된 주문 (멱등):** 아무 작업도 하지 않아야 한다. 이 테스트는 `@Disabled`이며 아래 버그 때문입니다.

### 발견한 문제: 이미 취소된 주문을 다시 취소하면 중복으로 환불됩니다
- **위치:** `src/main/java/com/example/order/OrderService.java:48`
  ```java
  if (order.getStatus() == OrderStatus.PAID || order.getStatus() == OrderStatus.CANCELLED) {
  ```
- **기대 동작:** Javadoc에 "이미 취소된 주문이면 아무 작업도 하지 않는다 (멱등)"라고 적혀 있습니다.
- **실제 동작:** CANCELLED 주문을 다시 취소하면 다음이 모두 일어납니다.
  - 전액(15,500원)을 **다시 환불**한다.
  - 재고를 **다시 복구**한다.
  - 취소 사유를 새 값으로 덮어써서 저장한다.
  - 이벤트를 **다시 발행**한다.

  CANCELLED 주문에서 바로 return하는 처리가 없고, 오히려 환불 조건에 CANCELLED가 들어가 있습니다. 실제 돈과 재고가 걸린 문제라 우선 수정을 권합니다.
- **수정 제안:**
  ```java
  if (order.getStatus() == OrderStatus.CANCELLED) {
      return;
  }

  long refundedAmount = 0;
  if (order.getStatus() == OrderStatus.PAID) {
      ...
  ```
- **확인 내용:** 임시 디렉터리에 프로젝트를 복사해 확인했습니다. 원본 코드는 건드리지 않았습니다.
  - 현재 코드에서 `@Disabled`를 빼면 이 테스트가 실패합니다.
  - 위 수정을 적용하면 15건이 모두 통과합니다.
- **수정 후 할 일:** `OrderServiceTest.cancelOrder_alreadyCancelledOrder_doesNothing`의 `@Disabled(...)` 한 줄을 지워 주세요.

### 판단한 부분
- **오류 메시지:** 메시지 문구 전체 대신 주문 ID나 `"reason"` 같은 핵심어가 들어 있는지만 확인합니다. 문구를 조금 바꿔도 테스트가 깨지지 않게 하려는 것입니다.
- **테스트 강도 점검:** 같은 임시 복사본에 그럴듯한 버그 9가지를 하나씩 넣고 돌렸습니다. 9가지 모두 테스트가 실패해서 잡아냈습니다.
  - 환불 금액에서 배송비를 빼기
  - PENDING_PAYMENT 주문도 환불하기
  - DELIVERED 검사 빼기
  - 환불 전에 재고 복구하기
  - 상태 변경 전에 저장하기
  - 저장 전에 이벤트 발행하기
  - 이벤트의 환불 금액 누락
  - `isBlank`를 `isEmpty`로 바꾸기
  - 복구 수량을 1로 고정하기

### 커버하지 않은 부분
- 주문 라인이 0개인 주문은 테스트하지 않았습니다. 그런 주문이 실제로 생길 수 있는지 정의가 없어서입니다.
- `OrderRepository`와 `PaymentGateway`의 실제 구현(DB, PG 연동)은 단위 테스트 범위가 아니라서 mock으로 대체했습니다.
