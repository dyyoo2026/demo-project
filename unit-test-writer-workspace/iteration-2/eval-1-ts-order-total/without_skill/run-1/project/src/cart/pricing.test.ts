import { describe, expect, it } from 'vitest';
import { calculateOrderTotal } from './pricing';
import type { CartItem, Coupon, MembershipTier } from './types';

const item = (sku: string, unitPrice: number, quantity = 1): CartItem => ({ sku, unitPrice, quantity });

describe('calculateOrderTotal', () => {
  describe('소계', () => {
    it('단가 × 수량을 모든 상품에 대해 합산한다', () => {
      const result = calculateOrderTotal([
        item('A', 12_000, 2), // 24,000
        item('B', 3_500, 3), // 10,500
        item('C', 800, 1), //      800
      ]);

      expect(result).toEqual({
        subtotal: 35_300,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 3_000,
        total: 38_300,
      });
    });

    it('등급을 생략하면 BASIC(할인 0%)으로 계산한다', () => {
      const items = [item('A', 100_000)];

      expect(calculateOrderTotal(items)).toEqual(calculateOrderTotal(items, 'BASIC'));
      expect(calculateOrderTotal(items).membershipDiscount).toBe(0);
    });
  });

  describe('수량 검증', () => {
    it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])(
      '수량이 %s 이면 RangeError 를 던진다',
      (quantity) => {
        expect(() => calculateOrderTotal([item('SKU-BAD', 1_000, quantity)])).toThrow(RangeError);
      },
    );

    it('에러 메시지에 문제가 된 SKU 가 포함된다', () => {
      expect(() => calculateOrderTotal([item('SKU-BAD', 1_000, 0)])).toThrow(/SKU-BAD/);
    });

    it('정상 상품 사이에 잘못된 수량이 하나라도 있으면 RangeError 를 던진다', () => {
      const items = [item('OK-1', 1_000, 1), item('OK-2', 2_000, 2), item('SKU-BAD', 3_000, 0)];

      expect(() => calculateOrderTotal(items)).toThrow(RangeError);
    });

    it('단가가 0원이어도 수량이 잘못되면 RangeError 를 던진다', () => {
      expect(() => calculateOrderTotal([item('FREEBIE', 0, 0)])).toThrow(RangeError);
    });

    it('수량 1은 유효하다', () => {
      expect(() => calculateOrderTotal([item('A', 1_000, 1)])).not.toThrow();
    });
  });

  describe('빈 장바구니', () => {
    it('모든 금액이 0원이고 배송비도 0원이다', () => {
      expect(calculateOrderTotal([])).toEqual({
        subtotal: 0,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 0,
        total: 0,
      });
    });

    it('등급과 쿠폰이 있어도 모든 금액이 0원이다', () => {
      const result = calculateOrderTotal([], 'GOLD', { kind: 'FIXED', amount: 5_000 });

      expect(result.total).toBe(0);
      expect(result.shippingFee).toBe(0);
      expect(result.couponDiscount).toBe(0);
    });
  });

  describe('멤버십 할인', () => {
    it.each<[MembershipTier, number]>([
      ['BASIC', 0],
      ['SILVER', 3_000],
      ['GOLD', 5_000],
    ])('%s 등급은 소계 100,000원에서 %i원을 할인한다', (tier, expectedDiscount) => {
      const result = calculateOrderTotal([item('A', 100_000)], tier);

      expect(result.subtotal).toBe(100_000);
      expect(result.membershipDiscount).toBe(expectedDiscount);
      expect(result.total).toBe(100_000 - expectedDiscount); // 5만원 이상 → 무료배송
    });

    it.each<[MembershipTier, number]>([
      ['SILVER', 329], // 10,999 × 3% = 329.97
      ['GOLD', 549], //   10,999 × 5% = 549.95
    ])('%s 등급 할인액의 원 단위 미만은 절사한다', (tier, expectedDiscount) => {
      expect(calculateOrderTotal([item('A', 10_999)], tier).membershipDiscount).toBe(expectedDiscount);
    });
  });

  describe('쿠폰 할인', () => {
    it('쿠폰이 없으면 쿠폰 할인은 0원이다', () => {
      expect(calculateOrderTotal([item('A', 60_000)], 'GOLD').couponDiscount).toBe(0);
    });

    describe('PERCENT 쿠폰', () => {
      it('멤버십 할인 후 금액에 비율을 적용한다', () => {
        // 100,000 - GOLD 5,000 = 95,000 → 10% = 9,500 (소계 기준이면 10,000)
        const result = calculateOrderTotal([item('A', 100_000)], 'GOLD', { kind: 'PERCENT', percent: 10 });

        expect(result.membershipDiscount).toBe(5_000);
        expect(result.couponDiscount).toBe(9_500);
        expect(result.total).toBe(85_500);
      });

      it('원 단위 미만은 절사한다', () => {
        // 12,345 × 10% = 1,234.5
        const result = calculateOrderTotal([item('A', 12_345)], 'BASIC', { kind: 'PERCENT', percent: 10 });

        expect(result.couponDiscount).toBe(1_234);
        expect(result.total).toBe(12_345 - 1_234 + 3_000);
      });

      it('나누어 떨어지는 금액은 1원도 덜 할인하지 않는다 (부동소수점 오차)', () => {
        // 11,000 × 35% = 3,850 (정확히 정수). 절사할 소수점이 없으므로 3,850원이어야 한다.
        const result = calculateOrderTotal([item('A', 11_000)], 'BASIC', { kind: 'PERCENT', percent: 35 });

        expect(result.couponDiscount).toBe(3_850);
      });
    });

    describe('FIXED 쿠폰', () => {
      it('고정 금액을 할인한다', () => {
        const result = calculateOrderTotal([item('A', 60_000)], 'BASIC', { kind: 'FIXED', amount: 5_000 });

        expect(result.couponDiscount).toBe(5_000);
        expect(result.total).toBe(55_000);
      });

      it('할인액은 멤버십 할인 후 금액을 넘지 않는다 (상품 금액이 0원 미만이 되지 않음)', () => {
        // 20,000 - SILVER 600 = 19,400 → 25,000원 쿠폰이어도 19,400원까지만 할인
        const result = calculateOrderTotal([item('A', 20_000)], 'SILVER', { kind: 'FIXED', amount: 25_000 });

        expect(result.membershipDiscount).toBe(600);
        expect(result.couponDiscount).toBe(19_400);
        // 할인 후 상품 금액 0원(< 50,000원)이므로 배송비는 부과된다
        expect(result.shippingFee).toBe(3_000);
        expect(result.total).toBe(3_000);
      });
    });

    describe('최소 주문 금액(minOrderAmount)', () => {
      const coupon: Coupon = { kind: 'PERCENT', percent: 10, minOrderAmount: 30_000 };

      it('할인 후 금액이 최소 주문 금액과 같으면 적용한다', () => {
        expect(calculateOrderTotal([item('A', 30_000)], 'BASIC', coupon).couponDiscount).toBe(3_000);
      });

      it('할인 후 금액이 최소 주문 금액보다 1원이라도 적으면 적용하지 않는다', () => {
        const result = calculateOrderTotal([item('A', 29_999)], 'BASIC', coupon);

        expect(result.couponDiscount).toBe(0);
        expect(result.total).toBe(29_999 + 3_000);
      });

      it('소계가 아니라 멤버십 할인 후 금액을 기준으로 판단한다', () => {
        // 소계 30,000 은 기준을 만족하지만, GOLD 할인 후 28,500 은 만족하지 못한다
        const result = calculateOrderTotal([item('A', 30_000)], 'GOLD', {
          kind: 'FIXED',
          amount: 2_000,
          minOrderAmount: 30_000,
        });

        expect(result.membershipDiscount).toBe(1_500);
        expect(result.couponDiscount).toBe(0);
        expect(result.total).toBe(28_500 + 3_000);
      });
    });
  });

  describe('배송비', () => {
    it('할인 후 상품 금액이 50,000원 미만이면 배송비 3,000원을 부과한다', () => {
      const result = calculateOrderTotal([item('A', 49_999)]);

      expect(result.shippingFee).toBe(3_000);
      expect(result.total).toBe(52_999);
    });

    it('할인 후 상품 금액이 정확히 50,000원이면 무료배송이다', () => {
      const result = calculateOrderTotal([item('A', 50_000)]);

      expect(result.shippingFee).toBe(0);
      expect(result.total).toBe(50_000);
    });

    it('할인 후 상품 금액이 50,000원을 넘으면 무료배송이다', () => {
      const result = calculateOrderTotal([item('A', 50_001)]);

      expect(result.shippingFee).toBe(0);
      expect(result.total).toBe(50_001);
    });

    it('소계가 아니라 멤버십·쿠폰 할인 후 금액으로 무료배송 여부를 판단한다', () => {
      // 소계 52,000 → GOLD 2,600 할인 → 49,400 (< 50,000)
      expect(calculateOrderTotal([item('A', 52_000)], 'GOLD').shippingFee).toBe(3_000);
      // 소계 52,000 → FIXED 5,000 할인 → 47,000 (< 50,000)
      expect(
        calculateOrderTotal([item('A', 52_000)], 'BASIC', { kind: 'FIXED', amount: 5_000 }).shippingFee,
      ).toBe(3_000);
    });
  });

  it('멤버십 + 쿠폰 + 무료배송이 모두 적용된 전체 계산', () => {
    // 소계 75,000 → GOLD 3,750 → 71,250 → 10% 쿠폰 7,125 → 64,125 (무료배송)
    const result = calculateOrderTotal(
      [item('SHIRT', 30_000, 2), item('SOCKS', 15_000, 1)],
      'GOLD',
      { kind: 'PERCENT', percent: 10, minOrderAmount: 50_000 },
    );

    expect(result).toEqual({
      subtotal: 75_000,
      membershipDiscount: 3_750,
      couponDiscount: 7_125,
      shippingFee: 0,
      total: 64_125,
    });
  });
});
