export type MembershipTier = 'BASIC' | 'SILVER' | 'GOLD';

export interface CartItem {
  sku: string;
  /** 단가 (원) */
  unitPrice: number;
  quantity: number;
}

export type Coupon =
  | { kind: 'PERCENT'; percent: number; minOrderAmount?: number }
  | { kind: 'FIXED'; amount: number; minOrderAmount?: number };

export interface OrderTotal {
  subtotal: number;
  membershipDiscount: number;
  couponDiscount: number;
  shippingFee: number;
  total: number;
}
