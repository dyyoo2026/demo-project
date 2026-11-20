import { describe, expect, it } from 'vitest';
import { calculateOrderTotal } from './pricing';
import type { CartItem } from './types';

const item = (unitPrice: number, quantity = 1, sku = 'SKU-1'): CartItem => ({
  sku,
  unitPrice,
  quantity,
});

describe('calculateOrderTotal', () => {
  describe('수량 검증', () => {
    it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])(
      '수량이 %s 이면 RangeError를 던진다',
      (quantity) => {
        expect(() => calculateOrderTotal([item(1_000, quantity)])).toThrow(RangeError);
      },
    );

    it('첫 번째가 아닌 상품의 수량이 잘못돼도 RangeError를 던지고, 메시지에 해당 SKU를 담는다', () => {
      const items = [item(1_000, 2, 'SKU-A'), item(2_000, 0, 'SKU-B')];

      expect(() => calculateOrderTotal(items)).toThrow(RangeError);
      expect(() => calculateOrderTotal(items)).toThrow(/SKU-B/);
    });
  });

  describe('소계', () => {
    it('빈 장바구니는 배송비를 포함해 모든 금액이 0원이다', () => {
      expect(calculateOrderTotal([])).toEqual({
        subtotal: 0,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 0,
        total: 0,
      });
    });

    it('모든 상품의 단가 × 수량을 합산하고, 5만원 미만이면 배송비 3,000원을 더한다', () => {
      const items = [item(12_000, 2, 'SKU-A'), item(5_500, 3, 'SKU-B')];

      expect(calculateOrderTotal(items)).toEqual({
        subtotal: 40_500,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 3_000,
        total: 43_500,
      });
    });

    // 현재 구현은 소계가 0원이면(무료 상품만 담긴 경우 포함) 빈 장바구니와 똑같이 취급한다.
    // JSDoc은 "빈 장바구니"만 언급하므로 의도가 모호하다 — 현재 동작을 고정해 둔다.
    it('단가 0원 상품만 담긴 장바구니도 빈 장바구니처럼 배송비 없이 0원이다', () => {
      expect(calculateOrderTotal([item(0, 2)])).toEqual({
        subtotal: 0,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 0,
        total: 0,
      });
    });
  });

  describe('멤버십 할인', () => {
    it('등급을 생략하면 BASIC으로 보고 할인하지 않는다', () => {
      const result = calculateOrderTotal([item(100_000)]);

      expect(result.membershipDiscount).toBe(0);
      expect(result.total).toBe(100_000);
    });

    it.each([
      ['BASIC', 0, 100_000],
      ['SILVER', 3_000, 97_000],
      ['GOLD', 5_000, 95_000],
    ] as const)(
      '%s 등급은 소계 100,000원에서 %i원을 할인한다',
      (tier, expectedDiscount, expectedTotal) => {
        const result = calculateOrderTotal([item(100_000)], tier);

        expect(result.membershipDiscount).toBe(expectedDiscount);
        expect(result.total).toBe(expectedTotal);
      },
    );

    it.each([
      // 10,050 × 3% = 301.5
      ['SILVER', 10_050, 301],
      // 10,010 × 5% = 500.5
      ['GOLD', 10_010, 500],
    ] as const)(
      '%s 할인액의 원 단위 미만은 절사한다 (소계 %i원 → %i원)',
      (tier, subtotal, expectedDiscount) => {
        expect(calculateOrderTotal([item(subtotal)], tier).membershipDiscount).toBe(
          expectedDiscount,
        );
      },
    );
  });

  describe('쿠폰', () => {
    it('PERCENT 쿠폰은 멤버십 할인 후 금액에 적용한다', () => {
      const result = calculateOrderTotal([item(100_000)], 'GOLD', { kind: 'PERCENT', percent: 10 });

      expect(result).toEqual({
        subtotal: 100_000,
        membershipDiscount: 5_000,
        couponDiscount: 9_500,
        shippingFee: 0,
        total: 85_500,
      });
    });

    it('PERCENT 쿠폰 할인액의 원 단위 미만은 절사한다', () => {
      // 12,345 × 10% = 1,234.5
      const result = calculateOrderTotal([item(12_345)], 'BASIC', { kind: 'PERCENT', percent: 10 });

      expect(result.couponDiscount).toBe(1_234);
    });

    it.fails(
      'PERCENT 쿠폰 할인액이 정확히 정수면 그대로 할인한다 (BUG: 22,000원 × 35% 기대 7,700원, 실제 7,699원 — 부동소수점 오차)',
      () => {
        const result = calculateOrderTotal([item(22_000)], 'BASIC', { kind: 'PERCENT', percent: 35 });

        expect(result.couponDiscount).toBe(7_700);
        expect(result.total).toBe(17_300);
      },
    );

    it('FIXED 쿠폰은 쿠폰 금액만큼 할인한다', () => {
      const result = calculateOrderTotal([item(40_000)], 'BASIC', { kind: 'FIXED', amount: 5_000 });

      expect(result).toEqual({
        subtotal: 40_000,
        membershipDiscount: 0,
        couponDiscount: 5_000,
        shippingFee: 3_000,
        total: 38_000,
      });
    });

    it('FIXED 쿠폰이 할인 후 금액보다 크면 그 금액까지만 할인하고, 배송비는 그대로 부과한다', () => {
      const result = calculateOrderTotal([item(10_000)], 'SILVER', { kind: 'FIXED', amount: 20_000 });

      expect(result).toEqual({
        subtotal: 10_000,
        membershipDiscount: 300,
        couponDiscount: 9_700,
        shippingFee: 3_000,
        total: 3_000,
      });
    });

    it.each([
      [30_000, 3_000],
      [29_999, 0],
    ])(
      'minOrderAmount 30,000원 쿠폰은 금액이 그 이상일 때만 적용한다 (소계 %i원 → 할인 %i원)',
      (subtotal, expectedCouponDiscount) => {
        const result = calculateOrderTotal([item(subtotal)], 'BASIC', {
          kind: 'FIXED',
          amount: 3_000,
          minOrderAmount: 30_000,
        });

        expect(result.couponDiscount).toBe(expectedCouponDiscount);
      },
    );

    it('minOrderAmount는 소계가 아니라 멤버십 할인 후 금액과 비교한다', () => {
      // 소계 50,000원이지만 GOLD 할인 후 47,500원 → 최소 주문 금액 50,000원 미달
      const result = calculateOrderTotal([item(50_000)], 'GOLD', {
        kind: 'PERCENT',
        percent: 10,
        minOrderAmount: 50_000,
      });

      expect(result).toEqual({
        subtotal: 50_000,
        membershipDiscount: 2_500,
        couponDiscount: 0,
        shippingFee: 3_000,
        total: 50_500,
      });
    });
  });

  describe('배송비', () => {
    it.each([
      [49_999, 3_000, 52_999],
      [50_001, 0, 50_001],
    ])(
      '상품 금액 %i원이면 배송비는 %i원이다',
      (subtotal, expectedShippingFee, expectedTotal) => {
        const result = calculateOrderTotal([item(subtotal)]);

        expect(result.shippingFee).toBe(expectedShippingFee);
        expect(result.total).toBe(expectedTotal);
      },
    );

    it.fails(
      '상품 금액이 정확히 50,000원이면 무료배송이다 (BUG: "50,000원 이상 무료배송"인데 배송비 3,000원이 부과됨)',
      () => {
        const result = calculateOrderTotal([item(50_000)]);

        expect(result.shippingFee).toBe(0);
        expect(result.total).toBe(50_000);
      },
    );

    it('무료배송 여부는 소계가 아니라 모든 할인 후 상품 금액으로 판단한다', () => {
      // 소계 60,000원 - 쿠폰 15,000원 = 45,000원 → 5만원 미만
      const result = calculateOrderTotal([item(60_000)], 'BASIC', { kind: 'FIXED', amount: 15_000 });

      expect(result.shippingFee).toBe(3_000);
      expect(result.total).toBe(48_000);
    });
  });
});
