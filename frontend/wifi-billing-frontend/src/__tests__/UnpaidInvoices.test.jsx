import { screen, waitFor } from '@testing-library/react'
import { rest } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders } from '../test-utils'
import UnpaidInvoices from '../pages/admin/UnpaidInvoices'

jest.mock('../components/admin/AdminLayout', () =>
  function AdminLayout({ children }) { return <div>{children}</div> }
)

describe('UnpaidInvoices', () => {
  test('renders a row for each invoice in the results array', async () => {
    renderWithProviders(<UnpaidInvoices />)
    await waitFor(() => {
      expect(screen.getByText('INV-001')).toBeInTheDocument()
      expect(screen.getByText('John Doe')).toBeInTheDocument()
      expect(screen.getByText('INV-002')).toBeInTheDocument()
      expect(screen.getByText('Jane Smith')).toBeInTheDocument()
    })
  })

  test('calculates and displays total outstanding from results array', async () => {
    renderWithProviders(<UnpaidInvoices />)
    // 500 + 1500 = 2000 — verifies the .reduce fix works on data?.results
    await waitFor(() => {
      expect(screen.getByText(/2,000/)).toBeInTheDocument()
    })
  })

  test('shows pending badge with invoice count', async () => {
    renderWithProviders(<UnpaidInvoices />)
    await waitFor(() => {
      expect(screen.getByText('2 pending')).toBeInTheDocument()
    })
  })

  test('shows empty state when API returns zero results', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/dashboard/invoices/unpaid/', (_req, res, ctx) =>
        res(ctx.json({ count: 0, total_pages: 1, current_page: 1, results: [] }))
      )
    )
    renderWithProviders(<UnpaidInvoices />)
    await waitFor(() => {
      expect(screen.getByText(/no unpaid invoices/i)).toBeInTheDocument()
    })
  })

  test('shows error banner when API fails', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/dashboard/invoices/unpaid/', (_req, res, ctx) =>
        res(ctx.status(500))
      )
    )
    renderWithProviders(<UnpaidInvoices />)
    await waitFor(() => {
      expect(screen.getByText(/failed to load invoices/i)).toBeInTheDocument()
    })
  })
})
