import { screen, fireEvent, within } from '@testing-library/react'
import { rest } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders } from '../test-utils'
import MonthlyEarnings from '../components/admin/MonthlyEarnings'

const API = 'http://127.0.0.1:8000/api'

test('shows closed months, marks the one in progress, and the change', async () => {
  server.use(
    rest.get(`${API}/reports/revenue/yearly/`, (_req, res, ctx) =>
      res(ctx.json({ results: [
        { year: 2026, revenue: 846790, payments: 63770, customers: 4100, comps: 234,
          by_method: { mpesa: 839790, cash: 7000 }, change: null, change_against: 'same point last year', in_progress: true },
        { year: 2025, revenue: 120000, payments: 9000, customers: 800, comps: 0,
          by_method: { mpesa: 120000 }, change: null, change_against: 'previous year', in_progress: false },
      ] }))
    ),
    rest.get(`${API}/reports/revenue/monthly/`, (_req, res, ctx) =>
      res(ctx.json({ results: [
        { month: '2026-11', revenue: 1200, payments: 4, customers: 3, comps: 0,
          by_method: { mpesa: 1200 }, change: 20, change_against: 'first 2 days of last month', in_progress: true },
        { month: '2026-10', revenue: 48500, payments: 160, customers: 90, comps: 2,
          by_method: { mpesa: 46000, cash: 2500 }, change: -5.2, change_against: 'previous month', in_progress: false },
        { month: '2026-09', revenue: 0, payments: 0, customers: 0, comps: 0,
          by_method: {}, change: null, change_against: 'previous month', in_progress: false },
      ] }))
    ),
  )
  renderWithProviders(<MonthlyEarnings />)

  expect(await screen.findByText(/october 2026/i)).toBeInTheDocument()
  expect(screen.getByText('KES 48,500')).toBeInTheDocument()
  // On the month in progress and on the year's total row.
  expect(screen.getAllByText(/so far/i)).toHaveLength(2)
  expect(screen.getByText(/5\.2%/)).toBeInTheDocument()
  // September's empty month, and the year total (first year: no comparison).
  expect(screen.getAllByTitle('Nothing to compare with')).toHaveLength(2)
})

test("shows this year's total under the months, and a row per year", async () => {
  server.use(
    rest.get(`${API}/reports/revenue/monthly/`, (_req, res, ctx) =>
      res(ctx.json({ results: [
        { month: '2026-11', revenue: 1200, payments: 4, customers: 3, comps: 0,
          by_method: { mpesa: 1200 }, change: null, change_against: 'same point last month', in_progress: true },
      ] }))
    ),
    rest.get(`${API}/reports/revenue/yearly/`, (_req, res, ctx) =>
      res(ctx.json({ results: [
        { year: 2026, revenue: 846790, payments: 63770, customers: 4100, comps: 234,
          by_method: { mpesa: 839790, cash: 7000 }, change: null, change_against: 'same point last year', in_progress: true },
        { year: 2025, revenue: 120000, payments: 9000, customers: 800, comps: 0,
          by_method: { mpesa: 120000 }, change: null, change_against: 'previous year', in_progress: false },
      ] }))
    ),
  )
  const { container } = renderWithProviders(<MonthlyEarnings />)

  // The total row under the months: the whole year.
  await screen.findByText(/2026 total/i)
  const foot = container.querySelector('tfoot')
  expect(within(foot).getByText('KES 846,790')).toBeInTheDocument()

  // A closed year keeps its own row.
  fireEvent.click(screen.getByRole('tab', { name: /by year/i }))
  expect(await screen.findByText('2025')).toBeInTheDocument()
  // Earnings, and the M-Pesa column beside it.
  expect(screen.getAllByText('KES 120,000').length).toBeGreaterThan(0)
  expect(container.querySelector('tfoot')).toBeNull()
})
