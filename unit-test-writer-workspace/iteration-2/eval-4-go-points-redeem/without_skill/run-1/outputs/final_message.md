`Redeem` 테스트를 `service_test.go`에 작성했습니다. 테스트 22개 중 20개가 통과하고 2개가 실패합니다. 실패한 2개는 테스트 오류가 아니라 `Redeem`의 실제 버그 때문입니다.

## 버그: 만료가 임박한 lot부터 차감하지 않음

`Redeem` 주석에는 "만료가 가장 임박한 lot 부터 차감한다. 만료 시각이 같으면 먼저 적립된(EarnedAt 이 이른) lot 부터"라고 되어 있습니다. 하지만 실제 코드(`service.go` 62-64행)는 `EarnedAt`으로만 정렬하고 `ExpiresAt`은 보지 않습니다.

```go
sort.SliceStable(usable, func(i, j int) bool {
	return usable[i].EarnedAt.Before(usable[j].EarnedAt)
})
```

그래서 먼저 적립됐지만 늦게 만료되는 lot이 있으면 그 lot이 먼저 차감됩니다. 그동안 곧 만료될 lot은 남아 있다가 사용되지 못한 채 소멸합니다. 실패하는 테스트는 아래 2개입니다.

- `만료가_가장_임박한_lot_부터_차감`: 결과가 `map[x:50]`로 나왔고, 기대값은 `map[y:50]`입니다.
- `만료_임박_순으로_여러_lot_에_걸쳐_차감`: 결과가 `map[late:60]`로 나왔고, 기대값은 `map[mid:20 soon:40]`입니다.

**고치는 방법:** 정렬 함수를 아래처럼 바꾸면 됩니다.

```go
sort.SliceStable(usable, func(i, j int) bool {
	if !usable[i].ExpiresAt.Equal(usable[j].ExpiresAt) {
		return usable[i].ExpiresAt.Before(usable[j].ExpiresAt)
	}
	return usable[i].EarnedAt.Before(usable[j].EarnedAt)
})
```

이 수정을 적용하면 전체 테스트가 통과하는 것을 확인했습니다. 확인할 때 `service.go`는 바꾸지 않았고, `go test -overlay`로 수정본을 임시로 대신 넣어 돌렸습니다. 테스트 작성만 요청하셨기 때문에 프로덕션 코드는 수정하지 않았습니다. 원하시면 바로 반영하겠습니다.

## 테스트 내용

- **잘못된 금액:** amount가 0이거나 음수면 `ErrInvalidAmount`를 반환하고, Store는 호출하지 않습니다.
- **정상 차감 (table-driven, 12개 케이스):**
  - 단일 lot에서 일부만 차감합니다.
  - lot을 정확히 다 쓰는 경우와 잔액 전부를 쓰는 경우를 확인합니다.
  - 여러 lot에 걸쳐 차감합니다.
  - 차감에 쓰이지 않은 lot은 결과 map에 없고, 그대로 저장됩니다.
  - 만료가 임박한 순서로 차감하고, 만료 시각이 같으면 먼저 적립된 lot부터 차감합니다.
  - 저장 순서는 Store가 돌려준 순서가 아니라 차감 순서입니다.
  - 만료된 lot과 만료 시각이 now와 같은 lot은 사용하지 않고 저장할 때 제거합니다.
  - 잔액이 0인 lot은 제외합니다.
  - 각 케이스에서 반환된 map, SaveLots 호출 횟수, 저장된 lot 목록과 순서를 모두 검증합니다.
- **포인트 부족:**
  - `errors.Is(err, ErrInsufficientPoints)`로 판별됩니다.
  - 에러 메시지에 부족한 포인트 수가 들어 있습니다.
  - SaveLots는 호출하지 않습니다.
  - 만료된 lot은 잔액에 포함하지 않습니다.
  - lot이 하나도 없는 경우도 확인합니다.
- **Store 에러:** ListLots나 SaveLots가 실패하면 원인 에러를 `errors.Is`로 판별할 수 있습니다. ListLots가 실패하면 SaveLots는 호출하지 않습니다.
- **전달값:** Redeem에 넘긴 `ctx`와 `userID`가 ListLots와 SaveLots에 그대로 전달됩니다.

## 테스트를 작성하며 정한 것

- **기준:** 기대값은 현재 구현이 아니라 `Redeem` 주석에 적힌 명세를 기준으로 잡았습니다.
- **외부 라이브러리 없음:** `go.mod`에 의존성이 없어서 testify 같은 라이브러리 없이 표준 라이브러리만 썼습니다. Store는 직접 만든 fake로 대체했습니다.
- **시간 고정:** 같은 패키지 안의 테스트이므로 `Service.now`를 고정 시각으로 바꿔 시간을 제어했습니다.
- **빈 슬라이스:** 저장 결과에서 nil과 빈 슬라이스는 같은 것으로 봤습니다. 명세에 둘의 구분이 없기 때문입니다.
- **부족 포인트 메시지 검사:** 메시지는 `need 25 more` 같은 정확한 문구가 아니라 부족한 포인트 수가 독립된 숫자로 들어 있는지만 정규식 `\b25\b`로 확인합니다. 테스트 숫자는 서로 겹치지 않게 골랐습니다. 예를 들어 요청 120, 잔액 95일 때 부족분 25를 찾으므로, 메시지에 잘못된 숫자가 들어가면 테스트가 잡아냅니다.
- **기존 파일:** `lot_test.go`는 그대로 두었습니다. 테스트 이름은 그 파일 스타일에 맞춰 한국어로 지었습니다.

## 파일

- 새로 만든 파일: `service_test.go`
- 마지막 테스트 실행 로그: `outputs/test_run.txt` (`go test -v -count=1 ./...`, exit status 1)
