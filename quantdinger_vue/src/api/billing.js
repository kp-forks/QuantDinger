import request from '@/utils/request'

const billingApi = {
  Plans: '/api/billing/plans',
  Purchase: '/api/billing/purchase',
  UsdtCreate: '/api/billing/usdt/create',
  UsdtOrder: (id) => `/api/billing/usdt/order/${id}`
}

export function getMembershipPlans () {
  return request({
    url: billingApi.Plans,
    method: 'get'
  })
}

export function purchaseMembership (plan) {
  return request({
    url: billingApi.Purchase,
    method: 'post',
    data: { plan }
  })
}

export function createUsdtOrder (plan) {
  return request({
    url: billingApi.UsdtCreate,
    method: 'post',
    data: { plan }
  })
}

export function getUsdtOrder (orderId, refresh = true) {
  return request({
    url: billingApi.UsdtOrder(orderId),
    method: 'get',
    params: { refresh: refresh ? 1 : 0 }
  })
}
