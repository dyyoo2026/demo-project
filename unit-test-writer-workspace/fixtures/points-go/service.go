package points

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"
)

var (
	ErrInvalidAmount      = errors.New("points: amount must be positive")
	ErrInsufficientPoints = errors.New("points: insufficient points")
)

// Store 는 사용자별 포인트 lot 저장소다.
type Store interface {
	ListLots(ctx context.Context, userID string) ([]Lot, error)
	SaveLots(ctx context.Context, userID string, lots []Lot) error
}

type Service struct {
	store Store
	now   func() time.Time
}

func NewService(store Store) *Service {
	return &Service{store: store, now: time.Now}
}

// Redeem 은 사용자의 포인트를 amount 만큼 차감하고, lot ID 별 차감량을 반환한다.
//
//   - amount 가 0 이하면 ErrInvalidAmount.
//   - 만료된 lot(ExpiresAt <= now)은 사용할 수 없다.
//   - 만료가 가장 임박한 lot 부터 차감한다. 만료 시각이 같으면 먼저 적립된(EarnedAt 이 이른) lot 부터.
//   - 사용 가능한 포인트가 부족하면 아무것도 저장하지 않고 ErrInsufficientPoints 를 감싼 에러를 반환한다
//     (errors.Is 로 판별 가능하고, 메시지에 부족한 포인트 수가 들어간다).
//   - 저장 시 다 쓴 lot(Amount 0)과 만료된 lot 은 제거하고, 나머지는 차감 순서대로 저장한다.
//   - Store 에러는 감싸서 그대로 반환한다(errors.Is 로 원인 판별 가능).
func (s *Service) Redeem(ctx context.Context, userID string, amount int64) (map[string]int64, error) {
	if amount <= 0 {
		return nil, ErrInvalidAmount
	}

	lots, err := s.store.ListLots(ctx, userID)
	if err != nil {
		return nil, fmt.Errorf("list lots for %s: %w", userID, err)
	}

	now := s.now()
	usable := make([]Lot, 0, len(lots))
	for _, l := range lots {
		if l.Usable(now) {
			usable = append(usable, l)
		}
	}

	if available := Balance(usable, now); available < amount {
		return nil, fmt.Errorf("%w: need %d more", ErrInsufficientPoints, amount-available)
	}

	sort.SliceStable(usable, func(i, j int) bool {
		return usable[i].EarnedAt.Before(usable[j].EarnedAt)
	})

	used := make(map[string]int64)
	remaining := amount
	for i := range usable {
		if remaining == 0 {
			break
		}
		take := min(usable[i].Amount, remaining)
		usable[i].Amount -= take
		used[usable[i].ID] = take
		remaining -= take
	}

	kept := make([]Lot, 0, len(usable))
	for _, l := range usable {
		if l.Amount > 0 {
			kept = append(kept, l)
		}
	}

	if err := s.store.SaveLots(ctx, userID, kept); err != nil {
		return nil, fmt.Errorf("save lots for %s: %w", userID, err)
	}
	return used, nil
}
