import { describe, expect, it } from 'vitest';
import { formatKRW } from './format';

describe('formatKRW', () => {
  it('천 단위마다 쉼표를 찍는다', () => {
    expect(formatKRW(1234567)).toBe('1,234,567원');
  });

  it('0원은 "0원"으로 표시한다', () => {
    expect(formatKRW(0)).toBe('0원');
  });
});
