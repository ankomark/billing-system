import { persistOptions, PERSIST_KEY, clearPersistedCache } from '../lib/persistCache'
import { queryClient } from '../lib/queryClient'
import { logout } from '../services/auth'

const q = (key, status = 'success') => ({ queryKey: [key], state: { status } })
const keep = persistOptions.dehydrateOptions.shouldDehydrateQuery

test('only the dashboard summaries are remembered, never customer data', () => {
  expect(keep(q('dashboard-summary'))).toBe(true)
  expect(keep(q('monthly-revenue'))).toBe(true)
  expect(keep(q('customers'))).toBe(false)
  expect(keep(q('customer'))).toBe(false)
  expect(keep(q('mpesa-transactions'))).toBe(false)
  expect(keep(q('dashboard-summary', 'error'))).toBe(false)
})

test('signing out forgets everything remembered', () => {
  localStorage.setItem(PERSIST_KEY, '{"x":1}')
  queryClient.setQueryData(['dashboard-summary'], { a: 1 })
  logout()
  expect(localStorage.getItem(PERSIST_KEY)).toBeNull()
  expect(queryClient.getQueryData(['dashboard-summary'])).toBeUndefined()
})

test('clearing works when storage is unavailable', () => {
  const spy = jest.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked') })
  expect(() => clearPersistedCache()).not.toThrow()
  spy.mockRestore()
})
