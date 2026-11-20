import { describe, expect, it } from 'vitest';
import { calculateOrderTotal } from './pricing';
import type { CartItem, MembershipTier } from './types';

const item = (unitPrice: number, quantity = 1, sku = 'SKU-1'): CartItem => ({ sku, unitPrice, quantity });

describe('calculateOrderTotal', () => {
  describe('수량 검증', () => {
    it.each([0, -1, 1.5, Number.NaN])('수량이 %s 인 항목이 있으면 RangeError 를 던진다', (quantity) => {
      const items = [item(1_000, 1, 'OK-1'), item(1_000, quantity, 'BAD-1')];

      expect(() => calculateOrderTotal(items)).toThrow(RangeError);
      expect(() => calculateOrderTotal(items)).toThrow(`Invalid quantity for BAD-1: ${quantity}`);
    });
  });

  describe('소계', () => {
    it('단가 × 수량의 합을 소계로 계산하고, 할인이 없으면 소계 + 배송비가 총액이 된다', () => {
      const items = [item(12_000, 2, 'A'), item(5_500, 3, 'B')];

      const result = calculateOrderTotal(items);

      expect(result).toEqual({
        subtotal: 40_500,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 3_000,
        total: 43_500,
      });
    });
  });

  describe('빈 장바구니', () => {
    it('멤버십·쿠폰과 관계없이 배송비를 포함한 모든 금액이 0원이다', () => {
      const result = calculateOrderTotal([], 'GOLD', { kind: 'FIXED', amount: 5_000 });

      expect(result).toEqual({
        subtotal: 0,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 0,
        total: 0,
      });
    });
  });

  describe('멤버십 할인', () => {
    it('등급을 생략하면 BASIC 으로 취급해 할인하지 않는다', () => {
      const result = calculateOrderTotal([item(33_333)]);

      expect(result.membershipDiscount).toBe(0);
      expect(result.total).toBe(36_333);
    });

    it.each<{ tier: MembershipTier; discount: number; total: number }>([
      { tier: 'BASIC', discount: 0, total: 36_333 },
      { tier: 'SILVER', discount: 999, total: 35_334 }, // 33,333 × 3% = 999.99 → 999
      { tier: 'GOLD', discount: 1_666, total: 34_667 }, // 33,333 × 5% = 1,666.65 → 1,666
    ])('$tier 등급은 소계에서 할인하고 원 단위 미만은 절사한다', ({ tier, discount, total }) => {
      const result = calculateOrderTotal([item(33_333)], tier);

      expect(result.membershipDiscount).toBe(discount);
      expect(result.total).toBe(total);
    });
  });

  describe('쿠폰', () => {
    it('PERCENT 쿠폰은 멤버십 할인 후 금액에 적용하고 원 단위 미만은 절사한다', () => {
      // 33,333 - GOLD 1,666 = 31,667 → 10% = 3,166.7 → 3,166
      const result = calculateOrderTotal([item(33_333)], 'GOLD', { kind: 'PERCENT', percent: 10 });

      expect(result).toEqual({
        subtotal: 33_333,
        membershipDiscount: 1_666,
        couponDiscount: 3_166,
        shippingFee: 3_000,
        total: 31_501,
      });
    });

    it.fails(
      'PERCENT 쿠폰 할인액이 정수로 떨어지면 그대로 적용한다 (BUG: 11,000원 × 35% 가 3,850 이 아니라 부동소수점 오차로 3,849 로 계산됨)',
      () => {
        const result = calculateOrderTotal([item(11_000)], 'BASIC', { kind: 'PERCENT', percent: 35 });

        expect(result.couponDiscount).toBe(3_850);
        expect(result.total).toBe(10_150);
      },
    );

    it('FIXED 쿠폰은 쿠폰 금액만큼 차감한다', () => {
      const result = calculateOrderTotal([item(40_000)], 'BASIC', { kind: 'FIXED', amount: 5_000 });

      expect(result.couponDiscount).toBe(5_000);
      expect(result.total).toBe(38_000);
    });

    it('FIXED 쿠폰 금액이 할인 후 금액보다 크면 할인 후 금액까지만 차감한다', () => {
      const result = calculateOrderTotal([item(4_000)], 'BASIC', { kind: 'FIXED', amount: 10_000 });

      expect(result.couponDiscount).toBe(4_000);
      expect(result.shippingFee).toBe(3_000);
      expect(result.total).toBe(3_000);
    });

    it('멤버십 할인 후 금액이 minOrderAmount 와 정확히 같으면 쿠폰을 적용한다', () => {
      const result = calculateOrderTotal([item(30_000)], 'BASIC', {
        kind: 'FIXED',
        amount: 3_000,
        minOrderAmount: 30_000,
      });

      expect(result.couponDiscount).toBe(3_000);
      expect(result.total).toBe(30_000);
    });

    it('소계가 minOrderAmount 이상이어도 멤버십 할인 후 금액이 미달이면 쿠폰을 적용하지 않는다', () => {
      // 30,000 - GOLD 1,500 = 28,500 < 30,000
      const result = calculateOrderTotal([item(30_000)], 'GOLD', {
        kind: 'PERCENT',
        percent: 10,
        minOrderAmount: 30_000,
      });

      expect(result.couponDiscount).toBe(0);
      expect(result.total).toBe(31_500);
    });
  });

  describe('배송비', () => {
    it('할인 후 상품 금액이 50,000원을 넘으면 무료배송이다', () => {
      const result = calculateOrderTotal([item(50_001)]);

      expect(result.shippingFee).toBe(0);
      expect(result.total).toBe(50_001);
    });

    it('할인 후 상품 금액이 50,000원 미만이면 배송비 3,000원을 더한다', () => {
      const result = calculateOrderTotal([item(49_999)]);

      expect(result.shippingFee).toBe(3_000);
      expect(result.total).toBe(52_999);
    });

    it.fails(
      '할인 후 상품 금액이 정확히 50,000원이면 무료배송이다 (BUG: 문서는 "50,000원 이상" 이지만 코드가 > 로 비교해 배송비 3,000원이 붙음)',
      () => {
        const result = calculateOrderTotal([item(50_000)]);

        expect(result.shippingFee).toBe(0);
        expect(result.total).toBe(50_000);
      },
    );

    it('무료배송 기준은 소계가 아니라 멤버십·쿠폰 할인 후 금액으로 판단한다', () => {
      // 소계 52,000 - SILVER 1,560 = 50,440 - FIXED 1,000 = 49,440 < 50,000
      const result = calculateOrderTotal([item(52_000)], 'SILVER', { kind: 'FIXED', amount: 1_000 });

      expect(result.shippingFee).toBe(3_000);
      expect(result.total).toBe(52_440);
    });
  });
});
