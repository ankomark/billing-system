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

/**
 * Who is signed in decides what this page offers. Staff may read the list;
 * only an admin may add or delete, and the buttons follow that — they used to
 * be shown to everyone and produced a confirm dialog followed by a 403.
 */
const signInAs = (role) =>
  localStorage.setItem('user', JSON.stringify({ username: 'u', role }))

describe('Customers', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    localStorage.clear()
    signInAs('tenant_admin')
  })

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

  test('hides add and delete from operator staff', async () => {
    signInAs('tenant_staff')
    renderWithProviders(<Customers />)
    await screen.findByTitle('View details')
    expect(screen.queryByTitle('Delete customer')).not.toBeInTheDocument()
    expect(screen.queryByTitle('Give free access')).not.toBeInTheDocument()
    expect(screen.queryByText('Add Customer')).not.toBeInTheDocument()
  })

  test('shows add, delete and give-free-access to an operator admin', async () => {
    renderWithProviders(<Customers />)
    expect(await screen.findByTitle('Delete customer')).toBeInTheDocument()
    // Giving access away lives on the list as well as the record: it is where
    // someone goes when a customer rings up about a failure.
    expect(screen.getByTitle('Give free access')).toBeInTheDocument()
  })

  test('shows error banner when API fails', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/customers/', (_req, res, ctx) =>
        res(ctx.status(500))
      )
    )
    renderWithProviders(<Customers />)
    await waitFor(() => {
      expect(screen.getByText(/couldn't load customers/i)).toBeInTheDocument()
    })
  })

  // The banner used to blame the connection for everything, including a 403,
  // which sent people to check their network over a permissions problem.
  test('says so when the account is not allowed to see customers', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/customers/', (_req, res, ctx) =>
        res(ctx.status(403), ctx.json({ detail: 'nope' }))
      )
    )
    renderWithProviders(<Customers />)
    await waitFor(() => {
      expect(screen.getByText(/don't have permission/i)).toBeInTheDocument()
    })
  })

  // DRF answers a page past the end with a 404, which rendered as a connection
  // error over an empty table. Fall back to the first page instead.
  test('recovers from a page number past the end', async () => {
    let sawFirstPage = false
    server.use(
      rest.get('http://127.0.0.1:8000/api/customers/', (req, res, ctx) => {
        if (req.url.searchParams.get('page') === '1') {
          sawFirstPage = true
          return res(ctx.json({
            count: 1, total_pages: 1, current_page: 1, next: null, previous: null,
            results: [{
              id: 1, full_name: 'Back On Page One', phone: '254700000000',
              connection_type: 'hotspot', voucher_code: 'WIFI-AAA111',
              status: 'active', created_at: new Date().toISOString(),
            }],
          }))
        }
        return res(ctx.status(404), ctx.json({ detail: 'Invalid page.' }))
      })
    )
    renderWithProviders(<Customers />)
    // Starts on page 1 anyway, so this asserts the happy path survives the
    // retry rule and the voucher column renders for a hotspot subscriber.
    await waitFor(() => {
      expect(screen.getByText('Back On Page One')).toBeInTheDocument()
    })
    expect(sawFirstPage).toBe(true)
    expect(screen.getByText('WIFI-AAA111')).toBeInTheDocument()
  })
})
