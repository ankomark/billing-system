import { screen, fireEvent, waitFor } from '@testing-library/react'
import { rest } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders } from '../test-utils'
import Customers from '../pages/admin/Customers'

jest.mock('../components/admin/AdminLayout', () =>
  function AdminLayout({ children }) { return <div>{children}</div> }
)

// Auto-confirm any confirm dialogs
jest.mock('../components/ui/ConfirmModal', () => ({
  useConfirm: () => ({
    confirm: jest.fn().mockResolvedValue(true),
    ConfirmDialog: () => null,
  }),
}))

const mockNavigate = jest.fn()
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}))

describe('Customers', () => {
  beforeEach(() => mockNavigate.mockReset())

  test('renders customer rows from paginated API response', async () => {
    renderWithProviders(<Customers />)
    await waitFor(() => {
      expect(screen.getByText('John Doe')).toBeInTheDocument()
      expect(screen.getByText('254712345678')).toBeInTheDocument()
    })
  })

  test('shows total customer count in page header', async () => {
    renderWithProviders(<Customers />)
    await waitFor(() => {
      expect(screen.getByText(/1 customer total/i)).toBeInTheDocument()
    })
  })

  test('shows empty state when no customers match filters', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/customers/', (_req, res, ctx) =>
        res(ctx.json({ count: 0, total_pages: 1, current_page: 1, results: [] }))
      )
    )
    renderWithProviders(<Customers />)
    await waitFor(() => {
      expect(screen.getByText(/no customers found/i)).toBeInTheDocument()
    })
  })

  test('calls DELETE endpoint after confirming delete dialog', async () => {
    let deleted = false
    server.use(
      rest.delete('http://127.0.0.1:8000/api/customers/:id/', (_req, res, ctx) => {
        deleted = true
        return res(ctx.status(204))
      })
    )
    renderWithProviders(<Customers />)
    const deleteBtn = await screen.findByTitle('Delete customer')
    fireEvent.click(deleteBtn)
    await waitFor(() => expect(deleted).toBe(true))
  })

  test('navigates to customer detail when view button is clicked', async () => {
    renderWithProviders(<Customers />)
    const viewBtn = await screen.findByTitle('View details')
    fireEvent.click(viewBtn)
    expect(mockNavigate).toHaveBeenCalledWith('/admin/customers/1')
  })

  test('shows error banner when API fails', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/customers/', (_req, res, ctx) =>
        res(ctx.status(500))
      )
    )
    renderWithProviders(<Customers />)
    await waitFor(() => {
      expect(screen.getByText(/failed to load customers/i)).toBeInTheDocument()
    })
  })
})
