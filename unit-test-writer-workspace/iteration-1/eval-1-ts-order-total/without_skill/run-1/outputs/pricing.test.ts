import { describe, expect, it } from 'vitest';
import { calculateOrderTotal } from './pricing';
import type { CartItem, Coupon, MembershipTier } from './types';

const item = (unitPrice: number, quantity = 1, sku = 'SKU-1'): CartItem => ({
  sku,
  unitPrice,
  quantity,
});

describe('calculateOrderTotal', () => {
  describe('소계', () => {
    it('Σ(단가 × 수량)으로 소계를 계산한다', () => {
      const result = calculateOrderTotal([
        item(10_000, 2, 'A'),
        item(3_500, 3, 'B'),
        item(1_200, 1, 'C'),
      ]);

      expect(result.subtotal).toBe(31_700);
    });

    it('등급을 생략하면 BASIC으로 간주해 멤버십 할인이 없다', () => {
      const result = calculateOrderTotal([item(30_000)]);

      expect(result).toEqual({
        subtotal: 30_000,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 3_000,
        total: 33_000,
      });
    });
  });

  describe('수량 검증', () => {
    it.each([
      ['0', 0],
      ['음수', -1],
      ['소수', 1.5],
      ['NaN', Number.NaN],
      ['Infinity', Number.POSITIVE_INFINITY],
    ])('수량이 %s이면 RangeError를 던진다', (_label, quantity) => {
      expect(() => calculateOrderTotal([item(1_000, quantity, 'BAD-SKU')])).toThrow(RangeError);
      expect(() => calculateOrderTotal([item(1_000, quantity, 'BAD-SKU')])).toThrow(/BAD-SKU/);
    });

    it('여러 상품 중 하나만 잘못돼도 RangeError를 던진다', () => {
      const items = [item(1_000, 1, 'OK'), item(2_000, 0, 'BROKEN')];

      expect(() => calculateOrderTotal(items)).toThrow(RangeError);
      expect(() => calculateOrderTotal(items)).toThrow(/BROKEN/);
    });

    it('수량 1은 허용한다', () => {
      expect(() => calculateOrderTotal([item(1_000, 1)])).not.toThrow();
    });
  });

  describe('빈 장바구니', () => {
    it('모든 금액과 배송비가 0원이다', () => {
      expect(calculateOrderTotal([])).toEqual({
        subtotal: 0,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 0,
        total: 0,
      });
    });

    it('등급과 쿠폰이 있어도 모든 금액이 0원이다', () => {
      const coupon: Coupon = { kind: 'FIXED', amount: 5_000 };

      expect(calculateOrderTotal([], 'GOLD', coupon)).toEqual({
        subtotal: 0,
        membershipDiscount: 0,
        couponDiscount: 0,
        shippingFee: 0,
        total: 0,
      });
    });
  });

  describe('멤버십 할인', () => {
    it.each<[MembershipTier, number]>([
      ['BASIC', 0],
      ['SILVER', 3_000],
      ['GOLD', 5_000],
    ])('%s 등급은 소계 100,000원에서 %i원을 할인한다', (tier, expected) => {
      expect(calculateOrderTotal([item(100_000)], tier).membershipDiscount).toBe(expected);
    });

    it('SILVER 할인의 원 단위 미만은 절사한다 (10,001 × 3% = 300.03 → 300)', () => {
      expect(calculateOrderTotal([item(10_001)], 'SILVER').membershipDiscount).toBe(300);
    });

    it('GOLD 할인의 원 단위 미만은 절사한다 (10,019 × 5% = 500.95 → 500)', () => {
      expect(calculateOrderTotal([item(10_019)], 'GOLD').membershipDiscount).toBe(500);
    });
  });

  describe('쿠폰', () => {
    it('쿠폰이 없으면 쿠폰 할인은 0원이다', () => {
      expect(calculateOrderTotal([item(30_000)], 'BASIC').couponDiscount).toBe(0);
    });

    describe('PERCENT 쿠폰', () => {
      it('멤버십 할인 후 금액에 비율을 적용한다', () => {
        // 100,000 - GOLD 5,000 = 95,000 → 10% = 9,500
        const result = calculateOrderTotal([item(100_000)], 'GOLD', { kind: 'PERCENT', percent: 10 });

        expect(result).toEqual({
          subtotal: 100_000,
          membershipDiscount: 5_000,
          couponDiscount: 9_500,
          shippingFee: 0,
          total: 85_500,
        });
      });

      it('원 단위 미만은 절사한다 (12,345 × 10% = 1,234.5 → 1,234)', () => {
        const result = calculateOrderTotal([item(12_345)], 'BASIC', { kind: 'PERCENT', percent: 10 });

        expect(result.couponDiscount).toBe(1_234);
        expect(result.total).toBe(12_345 - 1_234 + 3_000);
      });

      it('정확히 나누어떨어지는 금액은 1원도 덜 할인하지 않는다 (2,800 × 35% = 980)', () => {
        const result = calculateOrderTotal([item(2_800)], 'BASIC', { kind: 'PERCENT', percent: 35 });

        expect(result.couponDiscount).toBe(980);
      });
    });

    describe('FIXED 쿠폰', () => {
      it('고정 금액을 할인한다', () => {
        const result = calculateOrderTotal([item(30_000)], 'BASIC', { kind: 'FIXED', amount: 5_000 });

        expect(result).toEqual({
          subtotal: 30_000,
          membershipDiscount: 0,
          couponDiscount: 5_000,
          shippingFee: 3_000,
          total: 28_000,
        });
      });

      it('멤버십 할인 후 금액보다 클 수 없다 (상품 금액이 0원 미만이 되지 않는다)', () => {
        // 10,000 - SILVER 300 = 9,700 → 20,000원 쿠폰은 9,700원까지만 적용
        const result = calculateOrderTotal([item(10_000)], 'SILVER', { kind: 'FIXED', amount: 20_000 });

        expect(result.couponDiscount).toBe(9_700);
        expect(result.total).toBe(3_000); // 상품 금액 0원 + 배송비
      });
    });

    describe('minOrderAmount', () => {
      const coupon: Coupon = { kind: 'FIXED', amount: 3_000, minOrderAmount: 30_000 };

      it('할인 후 금액이 최소 주문 금액과 같으면 적용한다', () => {
        expect(calculateOrderTotal([item(30_000)], 'BASIC', coupon).couponDiscount).toBe(3_000);
      });

      it('할인 후 금액이 최소 주문 금액 미만이면 적용하지 않는다', () => {
        const result = calculateOrderTotal([item(29_999)], 'BASIC', coupon);

        expect(result.couponDiscount).toBe(0);
        expect(result.total).toBe(29_999 + 3_000);
      });

      it('소계가 아니라 멤버십 할인 후 금액을 기준으로 판단한다', () => {
        // 소계 50,000원은 조건을 넘지만, GOLD 할인 후 47,500원은 50,000원 미만
        const result = calculateOrderTotal([item(50_000)], 'GOLD', {
          kind: 'PERCENT',
          percent: 10,
          minOrderAmount: 50_000,
        });

        expect(result.membershipDiscount).toBe(2_500);
        expect(result.couponDiscount).toBe(0);
        expect(result.total).toBe(47_500 + 3_000);
      });
    });
  });

  describe('배송비', () => {
    it('할인 후 상품 금액이 50,000원 미만이면 3,000원이다', () => {
      const result = calculateOrderTotal([item(49_999)]);

      expect(result.shippingFee).toBe(3_000);
      expect(result.total).toBe(52_999);
    });

    it('할인 후 상품 금액이 정확히 50,000원이면 무료배송이다', () => {
      const result = calculateOrderTotal([item(50_000)]);

      expect(result.shippingFee).toBe(0);
      expect(result.total).toBe(50_000);
    });

    it('할인 후 상품 금액이 50,000원을 넘으면 무료배송이다', () => {
      expect(calculateOrderTotal([item(50_001)]).shippingFee).toBe(0);
    });

    it('소계가 아니라 할인 후 상품 금액을 기준으로 판단한다', () => {
      // 소계 55,000원이지만 쿠폰 적용 후 45,000원 → 배송비 부과
      const result = calculateOrderTotal([item(55_000)], 'BASIC', { kind: 'FIXED', amount: 10_000 });

      expect(result.shippingFee).toBe(3_000);
      expect(result.total).toBe(48_000);
    });
  });

  it('멤버십 → 쿠폰 → 배송비 순서로 모든 규칙을 함께 적용한다', () => {
    // 60,000 - GOLD 3,000 = 57,000 → 10% 쿠폰 5,700 → 51,300 (무료배송)
    const result = calculateOrderTotal(
      [item(20_000, 2, 'A'), item(10_000, 2, 'B')],
      'GOLD',
      { kind: 'PERCENT', percent: 10, minOrderAmount: 50_000 },
    );

    expect(result).toEqual({
      subtotal: 60_000,
      membershipDiscount: 3_000,
      couponDiscount: 5_700,
      shippingFee: 0,
      total: 51_300,
    });
  });
});
