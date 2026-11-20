package points

import "time"

// Lot 은 한 번에 적립된 포인트 묶음이다.
type Lot struct {
	ID        string
	Amount    int64 // 남은 포인트
	EarnedAt  time.Time
	ExpiresAt time.Time
}

// Usable 은 now 시점에 사용할 수 있는 lot 인지 반환한다. 만료 시각이 지났거나 같으면 사용할 수 없다.
func (l Lot) Usable(now time.Time) bool {
	return l.Amount > 0 && l.ExpiresAt.After(now)
}

// Balance 는 now 시점에 사용 가능한 포인트 합계를 반환한다.
func Balance(lots []Lot, now time.Time) int64 {
	var sum int64
	for _, l := range lots {
		if l.Usable(now) {
			sum += l.Amount
		}
	}
	return sum
}
