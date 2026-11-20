package points

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"regexp"
	"testing"
	"time"
)

// fakeStore 는 테스트용 Store 구현이다. 호출 내역을 기록하고, 지정한 에러를 돌려준다.
type fakeStore struct {
	lots    []Lot
	listErr error
	saveErr error

	listCalls  int
	listUserID string
	listCtx    context.Context

	saveCalls  int
	saveUserID string
	saveCtx    context.Context
	savedLots  []Lot
}

func (f *fakeStore) ListLots(ctx context.Context, userID string) ([]Lot, error) {
	f.listCalls++
	f.listUserID = userID
	f.listCtx = ctx
	if f.listErr != nil {
		return nil, f.listErr
	}
	// 호출자가 내부 상태를 건드리지 못하도록 복사본을 돌려준다.
	return append([]Lot(nil), f.lots...), nil
}

func (f *fakeStore) SaveLots(ctx context.Context, userID string, lots []Lot) error {
	f.saveCalls++
	f.saveUserID = userID
	f.saveCtx = ctx
	f.savedLots = append([]Lot(nil), lots...)
	return f.saveErr
}

// 테스트 기준 시각.
var testNow = time.Date(2025, 6, 1, 12, 0, 0, 0, time.UTC)

const testUserID = "user-1"

func newTestService(store Store) *Service {
	s := NewService(store)
	s.now = func() time.Time { return testNow }
	return s
}

// lot 은 testNow 기준 상대 시각으로 Lot 을 만든다.
func lot(id string, amount int64, earnedAgo, expiresIn time.Duration) Lot {
	return Lot{
		ID:        id,
		Amount:    amount,
		EarnedAt:  testNow.Add(-earnedAgo),
		ExpiresAt: testNow.Add(expiresIn),
	}
}

const day = 24 * time.Hour

func TestRedeem_InvalidAmount(t *testing.T) {
	tests := []struct {
		name   string
		amount int64
	}{
		{name: "0", amount: 0},
		{name: "음수", amount: -100},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			store := &fakeStore{lots: []Lot{lot("a", 1000, day, day)}}
			svc := newTestService(store)

			got, err := svc.Redeem(context.Background(), testUserID, tt.amount)

			if !errors.Is(err, ErrInvalidAmount) {
				t.Fatalf("Redeem() error = %v, want %v", err, ErrInvalidAmount)
			}
			if got != nil {
				t.Errorf("Redeem() = %v, want nil", got)
			}
			if store.listCalls != 0 || store.saveCalls != 0 {
				t.Errorf("store 가 호출되면 안 된다: ListLots %d회, SaveLots %d회", store.listCalls, store.saveCalls)
			}
		})
	}
}

func TestRedeem_Success(t *testing.T) {
	tests := []struct {
		name      string
		lots      []Lot
		amount    int64
		wantUsed  map[string]int64
		wantSaved []Lot
	}{
		{
			name:      "단일 lot 에서 일부 차감",
			lots:      []Lot{lot("a", 100, day, day)},
			amount:    30,
			wantUsed:  map[string]int64{"a": 30},
			wantSaved: []Lot{lot("a", 70, day, day)},
		},
		{
			name:      "lot 을 정확히 다 쓰면 저장 목록에서 제거",
			lots:      []Lot{lot("a", 100, day, day)},
			amount:    100,
			wantUsed:  map[string]int64{"a": 100},
			wantSaved: []Lot{},
		},
		{
			name: "여러 lot 에 걸쳐 차감하고 다 쓴 lot 은 제거",
			lots: []Lot{
				lot("a", 50, 2*day, 1*day),
				lot("b", 100, 1*day, 2*day),
			},
			amount:    80,
			wantUsed:  map[string]int64{"a": 50, "b": 30},
			wantSaved: []Lot{lot("b", 70, 1*day, 2*day)},
		},
		{
			name: "사용 가능한 잔액 전부 차감",
			lots: []Lot{
				lot("a", 50, 2*day, 1*day),
				lot("b", 100, 1*day, 2*day),
			},
			amount:    150,
			wantUsed:  map[string]int64{"a": 50, "b": 100},
			wantSaved: []Lot{},
		},
		{
			name: "차감에 쓰이지 않은 lot 은 결과에 없고 그대로 저장",
			lots: []Lot{
				lot("a", 100, 2*day, 1*day),
				lot("b", 100, 1*day, 2*day),
			},
			amount:   30,
			wantUsed: map[string]int64{"a": 30},
			wantSaved: []Lot{
				lot("a", 70, 2*day, 1*day),
				lot("b", 100, 1*day, 2*day),
			},
		},
		{
			// 적립 순서(x 가 먼저)와 만료 순서(y 가 먼저)가 반대인 경우.
			name: "만료가 가장 임박한 lot 부터 차감",
			lots: []Lot{
				lot("x", 100, 10*day, 30*day),
				lot("y", 100, 1*day, 1*day),
			},
			amount:   50,
			wantUsed: map[string]int64{"y": 50},
			wantSaved: []Lot{
				lot("y", 50, 1*day, 1*day),
				lot("x", 100, 10*day, 30*day),
			},
		},
		{
			name: "만료 임박 순으로 여러 lot 에 걸쳐 차감",
			lots: []Lot{
				lot("late", 100, 30*day, 30*day),
				lot("soon", 40, 1*day, 1*day),
				lot("mid", 40, 20*day, 10*day),
			},
			amount:   60,
			wantUsed: map[string]int64{"soon": 40, "mid": 20},
			wantSaved: []Lot{
				lot("mid", 20, 20*day, 10*day),
				lot("late", 100, 30*day, 30*day),
			},
		},
		{
			name: "만료 시각이 같으면 먼저 적립된 lot 부터 차감",
			lots: []Lot{
				lot("newer", 100, 1*day, 7*day),
				lot("older", 100, 5*day, 7*day),
			},
			amount:   30,
			wantUsed: map[string]int64{"older": 30},
			wantSaved: []Lot{
				lot("older", 70, 5*day, 7*day),
				lot("newer", 100, 1*day, 7*day),
			},
		},
		{
			name: "저장 순서는 store 순서가 아니라 차감 순서",
			lots: []Lot{
				lot("c", 10, 3*day, 3*day),
				lot("a", 10, 5*day, 1*day),
				lot("b", 10, 4*day, 2*day),
			},
			amount:   5,
			wantUsed: map[string]int64{"a": 5},
			wantSaved: []Lot{
				lot("a", 5, 5*day, 1*day),
				lot("b", 10, 4*day, 2*day),
				lot("c", 10, 3*day, 3*day),
			},
		},
		{
			name: "만료된 lot 은 사용하지 않고 저장 시 제거",
			lots: []Lot{
				lot("expired", 1000, 30*day, -time.Hour),
				lot("a", 100, 1*day, 1*day),
			},
			amount:    50,
			wantUsed:  map[string]int64{"a": 50},
			wantSaved: []Lot{lot("a", 50, 1*day, 1*day)},
		},
		{
			name: "만료 시각이 now 와 같은 lot 은 사용 불가",
			lots: []Lot{
				lot("boundary", 1000, 30*day, 0),
				lot("a", 100, 1*day, 1*day),
			},
			amount:    50,
			wantUsed:  map[string]int64{"a": 50},
			wantSaved: []Lot{lot("a", 50, 1*day, 1*day)},
		},
		{
			name: "잔액이 0 인 lot 은 결과와 저장 목록에서 제외",
			lots: []Lot{
				lot("empty", 0, 2*day, 1*day),
				lot("a", 100, 1*day, 2*day),
			},
			amount:    10,
			wantUsed:  map[string]int64{"a": 10},
			wantSaved: []Lot{lot("a", 90, 1*day, 2*day)},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			store := &fakeStore{lots: tt.lots}
			svc := newTestService(store)

			got, err := svc.Redeem(context.Background(), testUserID, tt.amount)
			if err != nil {
				t.Fatalf("Redeem() unexpected error: %v", err)
			}

			if !reflect.DeepEqual(got, tt.wantUsed) {
				t.Errorf("Redeem() used = %v, want %v", got, tt.wantUsed)
			}

			if store.saveCalls != 1 {
				t.Fatalf("SaveLots 호출 횟수 = %d, want 1", store.saveCalls)
			}
			if store.saveUserID != testUserID {
				t.Errorf("SaveLots userID = %q, want %q", store.saveUserID, testUserID)
			}
			assertLotsEqual(t, store.savedLots, tt.wantSaved)
		})
	}
}

func TestRedeem_PassesContextAndUserIDToStore(t *testing.T) {
	type ctxKey struct{}
	ctx := context.WithValue(context.Background(), ctxKey{}, "marker")

	store := &fakeStore{lots: []Lot{lot("a", 100, day, day)}}
	svc := newTestService(store)

	if _, err := svc.Redeem(ctx, "user-42", 10); err != nil {
		t.Fatalf("Redeem() unexpected error: %v", err)
	}

	if store.listUserID != "user-42" {
		t.Errorf("ListLots userID = %q, want %q", store.listUserID, "user-42")
	}
	if store.saveUserID != "user-42" {
		t.Errorf("SaveLots userID = %q, want %q", store.saveUserID, "user-42")
	}
	if store.listCtx != ctx {
		t.Errorf("ListLots 에 전달된 context 가 Redeem 에 넘긴 context 와 다르다")
	}
	if store.saveCtx != ctx {
		t.Errorf("SaveLots 에 전달된 context 가 Redeem 에 넘긴 context 와 다르다")
	}
}

func TestRedeem_InsufficientPoints(t *testing.T) {
	tests := []struct {
		name          string
		lots          []Lot
		amount        int64
		wantShortfall int64
	}{
		{
			name: "잔액보다 많이 요청",
			lots: []Lot{
				lot("a", 60, 2*day, 1*day),
				lot("b", 35, 1*day, 2*day),
			},
			amount:        120,
			wantShortfall: 25,
		},
		{
			name: "만료된 lot 은 잔액에 포함되지 않음",
			lots: []Lot{
				lot("a", 40, 1*day, 1*day),
				lot("expired", 500, 30*day, -time.Hour),
				lot("boundary", 500, 30*day, 0),
			},
			amount:        100,
			wantShortfall: 60,
		},
		{
			name:          "lot 이 하나도 없음",
			lots:          nil,
			amount:        7,
			wantShortfall: 7,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			store := &fakeStore{lots: tt.lots}
			svc := newTestService(store)

			got, err := svc.Redeem(context.Background(), testUserID, tt.amount)

			if !errors.Is(err, ErrInsufficientPoints) {
				t.Fatalf("Redeem() error = %v, want errors.Is(err, ErrInsufficientPoints)", err)
			}
			shortfall := regexp.MustCompile(fmt.Sprintf(`\b%d\b`, tt.wantShortfall))
			if !shortfall.MatchString(err.Error()) {
				t.Errorf("에러 메시지 %q 에 부족한 포인트 수 %d 가 없다", err.Error(), tt.wantShortfall)
			}
			if got != nil {
				t.Errorf("Redeem() = %v, want nil", got)
			}
			if store.saveCalls != 0 {
				t.Errorf("포인트가 부족하면 저장하면 안 된다: SaveLots %d회 호출됨", store.saveCalls)
			}
		})
	}
}

func TestRedeem_ListLotsError(t *testing.T) {
	errDB := errors.New("db down")
	store := &fakeStore{listErr: errDB}
	svc := newTestService(store)

	got, err := svc.Redeem(context.Background(), testUserID, 10)

	if !errors.Is(err, errDB) {
		t.Fatalf("Redeem() error = %v, want errors.Is(err, %v)", err, errDB)
	}
	if got != nil {
		t.Errorf("Redeem() = %v, want nil", got)
	}
	if store.saveCalls != 0 {
		t.Errorf("ListLots 실패 시 SaveLots 를 호출하면 안 된다: %d회 호출됨", store.saveCalls)
	}
}

func TestRedeem_SaveLotsError(t *testing.T) {
	errDB := errors.New("write conflict")
	store := &fakeStore{
		lots:    []Lot{lot("a", 100, day, day)},
		saveErr: errDB,
	}
	svc := newTestService(store)

	got, err := svc.Redeem(context.Background(), testUserID, 10)

	if !errors.Is(err, errDB) {
		t.Fatalf("Redeem() error = %v, want errors.Is(err, %v)", err, errDB)
	}
	if got != nil {
		t.Errorf("Redeem() = %v, want nil", got)
	}
}

func assertLotsEqual(t *testing.T, got, want []Lot) {
	t.Helper()
	// nil 과 빈 슬라이스는 같은 것으로 본다.
	if len(got) == 0 && len(want) == 0 {
		return
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("저장된 lots 가 다르다\n got: %s\nwant: %s", formatLots(got), formatLots(want))
	}
}

func formatLots(lots []Lot) string {
	s := "["
	for i, l := range lots {
		if i > 0 {
			s += ", "
		}
		s += fmt.Sprintf("%s:%d(exp %s)", l.ID, l.Amount, l.ExpiresAt.Sub(testNow))
	}
	return s + "]"
}
