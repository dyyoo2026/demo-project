## 테스트 작성 결과

- **파일**: `service_test.go` (테스트 함수 6개, 서브테스트 포함 케이스 14개)
- **실행**: `go vet ./... && go test -count=1 -v ./...` → 통과 13, known-bug(Skip) 1, 실패 0 (기존 `TestBalance`도 통과)
- **컨벤션**: 기존 `lot_test.go`를 따라 `package points`, 표준 라이브러리만 사용, table-driven 방식, 한글 서브테스트 이름을 썼습니다. `Store`는 호출 내역을 기록하는 직접 만든 `fakeStore`로 대체했습니다. 시간은 `svc.now`를 2025-06-01 12:00 UTC로 고정해서, 테스트 결과가 실행 시각에 따라 달라지지 않습니다.

### 커버한 동작 (`Redeem`)
- **정상 차감**: lot 하나에서 일부 차감, 여러 lot에 걸친 차감, 사용 가능 포인트와 정확히 같은 양 차감(경계값). 반환되는 lot ID별 차감량과 저장되는 lot 목록을 둘 다 정확한 값으로 검증합니다.
- **만료/잔액 0 처리**: `ExpiresAt == now`인 lot(경계값), 이미 만료된 lot, Amount가 0인 lot은 차감에 쓰지 않고 저장 목록에서도 빠지는지 확인합니다.
- **정렬**: 만료 시각이 같으면 먼저 적립된 lot부터 차감하는지, 남은 lot이 차감 순서대로 저장되는지(Store가 뒤섞인 순서로 돌려줘도) 확인합니다.
- **`amount` 검증**: 0과 음수면 `ErrInvalidAmount`를 반환하고 Store를 아예 호출하지 않는지 확인합니다.
- **포인트 부족**: `errors.Is(err, ErrInsufficientPoints)`, 메시지에 부족한 양(`need N more`)이 들어가는지, 결과가 nil이고 `SaveLots`를 호출하지 않는지 확인합니다. lot이 없는 경우, 1 모자란 경우, 만료된 lot이 잔액에 포함되지 않는 경우를 다룹니다.
- **Store 에러**: `ListLots`/`SaveLots` 에러가 감싸져서 `errors.Is`로 원인을 판별할 수 있는지 확인합니다. `ListLots`가 실패하면 저장도 하지 않아야 합니다.
- **강도 점검**: 프로젝트를 임시 디렉터리에 복사해서, 버그를 일부러 넣은 11가지 버전(`<=`↔`<` 뒤집기, 만료 필터 제거, `%w`→`%v`, 저장 에러 무시, 정렬 제거 등)으로 돌려봤습니다. 모두 테스트가 실패해서 잡아냈습니다. 원본 `service.go`는 건드리지 않았습니다.

### 발견한 문제: 차감 순서가 주석과 다름
- **위치**: `service.go:62-64`
- **주석(계약)**: "만료가 가장 임박한 lot부터 차감한다. 만료 시각이 같으면 먼저 적립된 lot부터."
- **실제 동작**: `EarnedAt`(적립 순)으로만 정렬합니다. 그래서 먼저 적립됐지만 만료가 한참 남은 lot이, 곧 만료될 lot보다 먼저 차감됩니다. 그 결과 사용자는 곧 만료될 포인트를 날리게 됩니다. 저장 순서도 같은 이유로 틀립니다.
- **재현**: `old`(30일 전 적립, 180일 후 만료)와 `soon`(5일 전 적립, 7일 후 만료)에서 30을 차감하면
  - 기대값: `map[soon:30]`
  - 실제값: `map[old:30]`
- **수정 제안**:
  ```go
  sort.SliceStable(usable, func(i, j int) bool {
      if !usable[i].ExpiresAt.Equal(usable[j].ExpiresAt) {
          return usable[i].ExpiresAt.Before(usable[j].ExpiresAt)
      }
      return usable[i].EarnedAt.Before(usable[j].EarnedAt)
  })
  ```
  임시 복사본에 이 수정을 적용하고 Skip을 풀어 돌려보니 전체 테스트가 통과했습니다.
- **수정 후 할 일**: `service_test.go`의 `TestRedeem_DeductsSoonestExpiringLotFirst` 첫 줄에 있는 `t.Skip("BUG: ...")`를 지워 주세요. 이 테스트는 올바른 동작을 기준으로 작성돼 있습니다. 그동안 suite가 green을 유지하도록 Skip으로 막아 둔 상태입니다.

### 참고
- 부족 메시지는 현재 형식인 `need N more` 문구로 검증합니다. 메시지 형식을 바꾸면 이 테스트도 같이 고쳐야 합니다.
- `ctx`를 Store에 그대로 넘기는지는 검증하지 않았습니다. 넘기기만 하고 다른 로직은 없어서 우선순위가 낮다고 판단했습니다.
