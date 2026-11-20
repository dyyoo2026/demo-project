package points

import (
	"testing"
	"time"
)

func TestBalance(t *testing.T) {
	now := time.Date(2025, 6, 1, 12, 0, 0, 0, time.UTC)

	tests := []struct {
		name string
		lots []Lot
		want int64
	}{
		{name: "빈 목록", lots: nil, want: 0},
		{
			name: "유효한 lot 만 합산",
			lots: []Lot{
				{ID: "a", Amount: 100, ExpiresAt: now.Add(time.Hour)},
				{ID: "b", Amount: 50, ExpiresAt: now.Add(-time.Hour)},
			},
			want: 100,
		},
		{
			name: "만료 시각이 now 와 같으면 제외",
			lots: []Lot{{ID: "a", Amount: 100, ExpiresAt: now}},
			want: 0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := Balance(tt.lots, now); got != tt.want {
				t.Errorf("Balance() = %d, want %d", got, tt.want)
			}
		})
	}
}
