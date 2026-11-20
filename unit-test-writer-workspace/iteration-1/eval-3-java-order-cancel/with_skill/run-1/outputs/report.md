## 테스트 작성 결과
- 파일: `src/test/java/com/example/order/OrderServiceTest.java` (테스트 메서드 10개, 파라미터 포함 14건 실행)
- 실행: `mvn test` → 전체 15건 중 14 passed, 1 known-bug(`@Disabled`), 0 failed (기존 `OrderTest` 포함)
- 기존 `OrderTest`의 관례를 따랐습니다: JUnit5 + Mockito(`@ExtendWith(MockitoExtension.class)`, `@Mock`/`@InjectMocks`) + AssertJ, 한글 `@DisplayName`, `method_condition_expected` 메서드명, `// given / when / then`. 의존성은 추가하지 않았습니다.

### 커버한 동작 (`cancelOrder`)
- **PAID 주문**: 배송비 포함 전액(15,500) 환불 / 모든 주문 라인 재고를 수량만큼 복구 / 사유와 함께 CANCELLED로 저장 / 저장 **후에** 환불 금액이 담긴 `OrderCancelledEvent` 발행 (InOrder로 순서 검증)
- **PENDING_PAYMENT 주문**: 환불 호출 없음, 재고 복구·저장은 그대로, 이벤트 환불 금액 0
- **환불 실패(`PaymentException`)**: 같은 예외를 그대로 던지고, 주문 상태(PAID)·취소 사유·재고는 그대로, 저장과 이벤트 발행 없음
- **주문 없음**: `OrderNotFoundException`(메시지에 주문 ID 포함), 부수 효과 없음
- **SHIPPED / DELIVERED**: `IllegalStateException`, 부수 효과 없음 (`@EnumSource`)
- **사유가 null / "" / 공백 / 탭·개행**: `IllegalArgumentException`, 부수 효과 없음 (`@NullAndEmptySource` + `@ValueSource`)
- **이미 CANCELLED인 주문(멱등)**: 아무 작업도 하지 않아야 함 → 아래 버그 때문에 `@Disabled`

### 발견한 문제 (버그)
- **이미 취소된 주문을 다시 취소하면 또 환불됩니다 (이중 환불).**
  - 위치: `OrderService.java:48`
    `if (order.getStatus() == OrderStatus.PAID || order.getStatus() == OrderStatus.CANCELLED)`
  - Javadoc에는 "이미 취소된 주문이면 아무 작업도 하지 않는다 (멱등)"라고 되어 있습니다.
  - 기대 동작: 부수 효과 없음
  - 실제 동작: `paymentGateway.refund(1, 15500)`가 다시 호출됩니다. 코드상 그 뒤로 재고 재복구, 재저장, 취소 이벤트 재발행까지 이어집니다.
  - 테스트 `cancelOrder_alreadyCancelled_doesNothing`는 올바른 동작을 검증하는 상태 그대로 두고 `@Disabled("BUG: ...")`로 표시했습니다. 비활성화를 풀고 돌려서 `OrderService.java:50`의 refund 호출 때문에 실패하는 것을 확인했습니다.
  - 수정 제안: 주문 조회 직후 `if (order.getStatus() == OrderStatus.CANCELLED) return;`를 넣고, 48번째 줄 환불 조건에서 `|| ... CANCELLED`를 빼면 됩니다. 수정 후 `@Disabled`만 지우면 해당 테스트가 통과해야 합니다.
  - 프로덕션 코드는 수정하지 않았습니다. 수정 여부는 판단에 맡기겠습니다.

### 참고 / 판단한 부분
- 사유 검증 테스트는 "환불·재고·저장·이벤트가 일어나지 않는다"만 검증합니다. "주문 조회를 하지 않는다"까지는 묶지 않았습니다. 검증 순서는 구현 세부라고 봤습니다.
- 재고 복구 도중 `InventoryClient.release`가 실패하는 경우는 Javadoc에 정해진 동작이 없어서 테스트하지 않았습니다. 이 경우 환불은 이미 끝났는데 주문은 취소되지 않은 상태로 남습니다. 의도된 정책인지(보상 처리 필요 여부) 확인해 보시길 권합니다.
