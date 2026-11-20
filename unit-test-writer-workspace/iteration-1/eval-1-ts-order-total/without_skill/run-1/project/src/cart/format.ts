/** 금액을 "12,300원" 형식으로 포맷한다. */
export function formatKRW(amount: number): string {
  return `${amount.toLocaleString('ko-KR')}원`;
}
