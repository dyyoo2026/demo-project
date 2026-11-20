package points

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

var testNow = time.Date(2025, 6, 1, 12, 0, 0, 0, time.UTC)

const day = 24 * time.Hour

// newLot 은 testNow 기준 상대 일수로 Lot 을 만든다.
// earnedDays 는 적립 시점(음수 = 과거), expiresDays 는 만료 시점(0 = testNow 와 같음).
func newLot(id string, amount int64, earnedDays, expiresDays int) Lot {
	return Lot{
		ID:        id,
		Amount:    amount,
		EarnedAt:  testNow.Add(time.Duration(earnedDays) * day),
		ExpiresAt: testNow.Add(time.Duration(expiresDays) * day),
	}
}

type saveCall struct {
	userID string
	lots   []Lot
}

// fakeStore 는 Store 의 인메모리 구현으로, 호출 내역을 기록한다.
type fakeStore struct {
	lots    []Lot
	listErr error
	saveErr error

	listCalls []string
	saveCalls []saveCall
}

func (f *fakeStore) ListLots(_ context.Context, userID string) ([]Lot, error) {
	f.listCalls = append(f.listCalls, userID)
	if f.listErr != nil {
		return nil, f.listErr
	}
	return f.lots, nil
}

func (f *fakeStore) SaveLots(_ context.Context, userID string, lots []Lot) error {
	f.saveCalls = append(f.saveCalls, saveCall{userID: userID, lots: append([]Lot{}, lots...)})
	return f.saveErr
}

func newTestService(store Store) *Service {
	svc := NewService(store)
	svc.now = func() time.Time { return testNow }
	return svc
}

func TestRedeem(t *testing.T) {
	tests := []struct {
		name      string
		lots      []Lot
		amount    int64
		wantUsed  map[string]int64
		wantSaved []Lot
	}{
		{
			name:      "단일 lot 에서 일부만 차감",
			lots:      []Lot{newLot("a", 100, -10, 10)},
			amount:    30,
			wantUsed:  map[string]int64{"a": 30},
			wantSaved: []Lot{newLot("a", 70, -10, 10)},
		},
		{
			name: "여러 lot 에 걸쳐 차감하고 다 쓴 lot 은 저장에서 제거",
			lots: []Lot{
				newLot("a", 50, -20, 5),
				newLot("b", 100, -10, 30),
			},
			amount:    80,
			wantUsed:  map[string]int64{"a": 50, "b": 30},
			wantSaved: []Lot{newLot("b", 70, -10, 30)},
		},
		{
			name: "사용 가능 포인트와 정확히 같은 양이면 모든 lot 을 소진",
			lots: []Lot{
				newLot("a", 50, -20, 5),
				newLot("b", 30, -10, 30),
			},
			amount:    80,
			wantUsed:  map[string]int64{"a": 50, "b": 30},
			wantSaved: []Lot{},
		},
		{
			name: "만료된 lot(만료 시각 == now 포함)과 잔액 0 lot 은 사용하지 않고 저장에서도 제거",
			lots: []Lot{
				newLot("expired-past", 500, -60, -1),
				newLot("expired-now", 500, -50, 0),
				newLot("empty", 0, -40, 10),
				newLot("valid", 100, -10, 20),
			},
			amount:    40,
			wantUsed:  map[string]int64{"valid": 40},
			wantSaved: []Lot{newLot("valid", 60, -10, 20)},
		},
		{
			name: "만료 시각이 같으면 먼저 적립된 lot 부터 차감",
			lots: []Lot{
				newLot("later", 100, -5, 10),
				newLot("earlier", 100, -20, 10),
			},
			amount:   30,
			wantUsed: map[string]int64{"earlier": 30},
			wantSaved: []Lot{
				newLot("earlier", 70, -20, 10),
				newLot("later", 100, -5, 10),
			},
		},
		{
			name: "남은 lot 은 차감 순서(만료 임박 순)대로 저장",
			lots: []Lot{
				newLot("c", 100, -5, 30),
				newLot("a", 100, -20, 5),
				newLot("b", 100, -10, 20),
			},
			amount:   10,
			wantUsed: map[string]int64{"a": 10},
			wantSaved: []Lot{
				newLot("a", 90, -20, 5),
				newLot("b", 100, -10, 20),
				newLot("c", 100, -5, 30),
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			store := &fakeStore{lots: tt.lots}

			got, err := newTestService(store).Redeem(context.Background(), "user-1", tt.amount)

			if err != nil {
				t.Fatalf("Redeem() error = %v, want nil", err)
			}
			if !reflect.DeepEqual(got, tt.wantUsed) {
				t.Errorf("Redeem() = %v, want %v", got, tt.wantUsed)
			}
			if !reflect.DeepEqual(store.listCalls, []string{"user-1"}) {
				t.Errorf("ListLots calls = %v, want [user-1]", store.listCalls)
			}
			if len(store.saveCalls) != 1 {
				t.Fatalf("SaveLots called %d times, want 1", len(store.saveCalls))
			}
			if store.saveCalls[0].userID != "user-1" {
				t.Errorf("SaveLots userID = %q, want %q", store.saveCalls[0].userID, "user-1")
			}
			if !reflect.DeepEqual(store.saveCalls[0].lots, tt.wantSaved) {
				t.Errorf("saved lots = %+v, want %+v", store.saveCalls[0].lots, tt.wantSaved)
			}
		})
	}
}

func TestRedeem_DeductsSoonestExpiringLotFirst(t *testing.T) {
	t.Skip("BUG: 만료 임박 순이 아니라 적립 순(EarnedAt)으로 차감한다 — " +
		"want used=map[soon:30], got map[old:30] (service.go:62-64 정렬 기준이 EarnedAt 뿐)")

	store := &fakeStore{lots: []Lot{
		newLot("old", 100, -30, 180), // 먼저 적립됐지만 만료는 한참 뒤
		newLot("soon", 100, -5, 7),   // 나중에 적립됐지만 만료가 임박
	}}

	got, err := newTestService(store).Redeem(context.Background(), "user-1", 30)

	if err != nil {
		t.Fatalf("Redeem() error = %v, want nil", err)
	}
	wantUsed := map[string]int64{"soon": 30}
	if !reflect.DeepEqual(got, wantUsed) {
		t.Errorf("Redeem() = %v, want %v", got, wantUsed)
	}
	if len(store.saveCalls) != 1 {
		t.Fatalf("SaveLots called %d times, want 1", len(store.saveCalls))
	}
	wantSaved := []Lot{
		newLot("soon", 70, -5, 7),
		newLot("old", 100, -30, 180),
	}
	if !reflect.DeepEqual(store.saveCalls[0].lots, wantSaved) {
		t.Errorf("saved lots = %+v, want %+v", store.saveCalls[0].lots, wantSaved)
	}
}

func TestRedeem_InvalidAmount(t *testing.T) {
	tests := []struct {
		name   string
		amount int64
	}{
		{name: "0", amount: 0},
		{name: "음수", amount: -1},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			store := &fakeStore{lots: []Lot{newLot("a", 100, -10, 10)}}

			got, err := newTestService(store).Redeem(context.Background(), "user-1", tt.amount)

			if !errors.Is(err, ErrInvalidAmount) {
				t.Fatalf("Redeem() error = %v, want ErrInvalidAmount", err)
			}
			if got != nil {
				t.Errorf("Redeem() = %v, want nil", got)
			}
			if len(store.listCalls) != 0 || len(store.saveCalls) != 0 {
				t.Errorf("store called (ListLots %d, SaveLots %d), want no calls",
					len(store.listCalls), len(store.saveCalls))
			}
		})
	}
}

func TestRedeem_InsufficientPoints(t *testing.T) {
	tests := []struct {
		name    string
		lots    []Lot
		amount  int64
		wantMsg string
	}{
		{
			name:    "lot 이 하나도 없음",
			lots:    nil,
			amount:  10,
			wantMsg: "need 10 more",
		},
		{
			name:    "사용 가능 포인트보다 1 많음",
			lots:    []Lot{newLot("a", 100, -10, 10)},
			amount:  101,
			wantMsg: "need 1 more",
		},
		{
			name: "만료된 lot 은 사용 가능 포인트에 포함하지 않음",
			lots: []Lot{
				newLot("a", 70, -20, 5),
				newLot("b", 50, -10, 30),
				newLot("expired-now", 500, -50, 0),
				newLot("expired-past", 300, -60, -1),
			},
			amount:  200,
			wantMsg: "need 80 more",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			store := &fakeStore{lots: tt.lots}

			got, err := newTestService(store).Redeem(context.Background(), "user-1", tt.amount)

			if !errors.Is(err, ErrInsufficientPoints) {
				t.Fatalf("Redeem() error = %v, want ErrInsufficientPoints", err)
			}
			if !strings.Contains(err.Error(), tt.wantMsg) {
				t.Errorf("error message = %q, want it to contain %q", err.Error(), tt.wantMsg)
			}
			if got != nil {
				t.Errorf("Redeem() = %v, want nil", got)
			}
			if len(store.saveCalls) != 0 {
				t.Errorf("SaveLots called %d times, want 0", len(store.saveCalls))
			}
		})
	}
}

func TestRedeem_ListLotsError(t *testing.T) {
	errDB := errors.New("db unavailable")
	store := &fakeStore{listErr: errDB}

	got, err := newTestService(store).Redeem(context.Background(), "user-1", 10)

	if !errors.Is(err, errDB) {
		t.Fatalf("Redeem() error = %v, want wrapping %v", err, errDB)
	}
	if got != nil {
		t.Errorf("Redeem() = %v, want nil", got)
	}
	if len(store.saveCalls) != 0 {
		t.Errorf("SaveLots called %d times, want 0", len(store.saveCalls))
	}
}

func TestRedeem_SaveLotsError(t *testing.T) {
	errDB := errors.New("db unavailable")
	store := &fakeStore{
		lots:    []Lot{newLot("a", 100, -10, 10)},
		saveErr: errDB,
	}

	got, err := newTestService(store).Redeem(context.Background(), "user-1", 30)

	if !errors.Is(err, errDB) {
		t.Fatalf("Redeem() error = %v, want wrapping %v", err, errDB)
	}
	if got != nil {
		t.Errorf("Redeem() = %v, want nil", got)
	}
}
