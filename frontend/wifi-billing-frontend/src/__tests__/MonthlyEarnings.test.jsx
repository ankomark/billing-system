import { screen } from '@testing-library/react'
import { rest } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders } from '../test-utils'
import MonthlyEarnings from '../components/admin/MonthlyEarnings'

const API = 'http://127.0.0.1:8000/api'

test('shows closed months, marks the one in progress, and the change', async () => {
  server.use(
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
  expect(screen.getByText(/so far/i)).toBeInTheDocument()
  expect(screen.getByText(/5\.2%/)).toBeInTheDocument()
  expect(screen.getByTitle('Nothing to compare with')).toBeInTheDocument()
})
