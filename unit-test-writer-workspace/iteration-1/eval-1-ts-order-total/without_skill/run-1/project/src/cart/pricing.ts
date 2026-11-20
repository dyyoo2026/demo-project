import type { CartItem, Coupon, MembershipTier, OrderTotal } from './types';

export const FREE_SHIPPING_THRESHOLD = 50_000;
export const SHIPPING_FEE = 3_000;

const MEMBERSHIP_RATE: Record<MembershipTier, number> = {
  BASIC: 0,
  SILVER: 0.03,
  GOLD: 0.05,
};

/**
 * 장바구니 주문 금액을 계산한다.
 *
 * 규칙:
 * 1. 소계 = Σ(단가 × 수량). 수량은 1 이상의 정수여야 하며, 아니면 RangeError.
 * 2. 멤버십 할인은 소계에 적용한다 (BASIC 0%, SILVER 3%, GOLD 5%). 원 단위 미만 절사.
 * 3. 쿠폰은 멤버십 할인 후 금액에 적용한다.
 *    - 쿠폰의 minOrderAmount 가 있으면, 멤버십 할인 후 금액이 그 이상일 때만 적용.
 *    - PERCENT 쿠폰: 원 단위 미만 절사. FIXED 쿠폰: 할인 후 금액보다 클 수 없다 (0원 미만 불가).
 * 4. 할인 후 상품 금액이 50,000원 이상이면 무료배송, 아니면 배송비 3,000원.
 *    빈 장바구니는 배송비도 0원.
 */
export function calculateOrderTotal(
  items: CartItem[],
  tier: MembershipTier = 'BASIC',
  coupon?: Coupon,
): OrderTotal {
  for (const item of items) {
    if (!Number.isInteger(item.quantity) || item.quantity < 1) {
      throw new RangeError(`Invalid quantity for ${item.sku}: ${item.quantity}`);
    }
  }

  const subtotal = items.reduce((sum, item) => sum + item.unitPrice * item.quantity, 0);
  if (subtotal === 0) {
    return { subtotal: 0, membershipDiscount: 0, couponDiscount: 0, shippingFee: 0, total: 0 };
  }

  const membershipDiscount = Math.floor(subtotal * MEMBERSHIP_RATE[tier]);
  const afterMembership = subtotal - membershipDiscount;

  let couponDiscount = 0;
  if (coupon && afterMembership >= (coupon.minOrderAmount ?? 0)) {
    if (coupon.kind === 'PERCENT') {
      couponDiscount = Math.floor(afterMembership * (coupon.percent / 100));
    } else {
      couponDiscount = Math.min(coupon.amount, afterMembership);
    }
  }

  const merchandiseTotal = afterMembership - couponDiscount;
  const shippingFee = merchandiseTotal > FREE_SHIPPING_THRESHOLD ? 0 : SHIPPING_FEE;

  return {
    subtotal,
    membershipDiscount,
    couponDiscount,
    shippingFee,
    total: merchandiseTotal + shippingFee,
  };
}
