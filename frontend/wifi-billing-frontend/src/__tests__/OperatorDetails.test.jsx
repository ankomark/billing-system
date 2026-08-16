import { screen, fireEvent, waitFor } from '@testing-library/react'
import { rest } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders } from '../test-utils'
import OperatorDetailsPanel from '../components/platform/OperatorDetailsPanel'
import AdminSidebar from '../components/admin/AdminSidebar'

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}))

const API = 'http://127.0.0.1:8000/api'

const operator = {
  // Collapsed for display: the brand, with nothing to say it is not the
  // internal name.
  id: 7,
  name: 'Fibre Kenya',
  details: {
    name: 'Fibre Ltd',
    business_name: 'Fibre Kenya',
    support_phone: '0712345678',
    support_phone_2: '',
    pppoe_prefix: 'NET',
    contact_email: 'ops@fibre.co.ke',
    contact_phone: '0700000000',
  },
}

beforeEach(() => localStorage.clear())

/**
 * Renaming an operator was implemented on the backend and never given a UI, so
 * a name typed wrong at onboarding could only be fixed in the database.
 */
describe('OperatorDetailsPanel', () => {
  test('prefills the two names apart rather than the collapsed display name', () => {
    renderWithProviders(<OperatorDetailsPanel operator={operator} canEdit />)

    // Seeded from the collapsed `name` instead, both boxes would read "Fibre
    // Kenya" and the first save would write the brand over the internal name.
    expect(screen.getByLabelText(/^Business name/i)).toHaveValue('Fibre Kenya')
    expect(screen.getByLabelText(/^Internal name/i)).toHaveValue('Fibre Ltd')
  })

  test('the owner can rename an operator', async () => {
    let sent
    server.use(
      rest.patch(`${API}/platform/operators/7/`, async (req, res, ctx) => {
        sent = await req.json()
        return res(ctx.json({ ...sent }))
      })
    )

    renderWithProviders(<OperatorDetailsPanel operator={operator} canEdit />)

    fireEvent.change(screen.getByLabelText(/^Business name/i), {
      target: { value: 'Fibre Group' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save details/i }))

    await waitFor(() => expect(sent).toBeDefined())
    expect(sent.business_name).toBe('Fibre Group')
    // The internal name goes along untouched, not blanked by a form that only
    // knew about the field being edited.
    expect(sent.name).toBe('Fibre Ltd')
  })

  test('platform staff see the details but cannot save them', () => {
    renderWithProviders(<OperatorDetailsPanel operator={operator} canEdit={false} />)

    expect(screen.getByLabelText(/^Business name/i)).toBeDisabled()
    expect(screen.queryByRole('button', { name: /save details/i })).not.toBeInTheDocument()
  })

  test('renders nothing for a response that predates the raw fields', () => {
    const { container } = renderWithProviders(
      <OperatorDetailsPanel operator={{ id: 7, name: 'Fibre Kenya' }} canEdit />
    )
    expect(container).toBeEmptyDOMElement()
  })

  test('server-side validation lands on the field it belongs to', async () => {
    server.use(
      rest.patch(`${API}/platform/operators/7/`, (_req, res, ctx) =>
        res(ctx.status(400), ctx.json({ name: ['This field may not be blank.'] }))
      )
    )

    renderWithProviders(<OperatorDetailsPanel operator={operator} canEdit />)
    fireEvent.click(screen.getByRole('button', { name: /save details/i }))

    expect(await screen.findByText(/may not be blank/i)).toBeInTheDocument()
  })
})

/**
 * The operator's own console titles itself from the copy of the profile stored
 * at sign-in, which is written once and never refreshed — so a name the owner
 * corrected kept showing the old value until that operator happened to sign
 * out.
 */
describe('the renamed operator sees it on their own dashboard', () => {
  test('the sidebar prefers the live profile over the copy stored at login', async () => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem(
      'user',
      JSON.stringify({ username: 'u', role: 'tenant_admin', tenant_name: 'Old Name' })
    )
    server.use(
      rest.get(`${API}/auth/profile/`, (_req, res, ctx) =>
        res(ctx.json({
          id: 1, username: 'u', role: 'tenant_admin', tenant_name: 'Corrected Name',
        }))
      )
    )

    renderWithProviders(<AdminSidebar open onClose={() => {}} />)

    // Seeded from the cache, so the name never flashes as "Operator"...
    expect(screen.getByText('Old Name')).toBeInTheDocument()
    // ...and then corrects itself without a sign-out.
    expect(await screen.findByText('Corrected Name')).toBeInTheDocument()
  })

  test('impersonation still wins, and asks for no profile of its own', async () => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem(
      'user',
      JSON.stringify({ username: 'staff', role: 'platform_owner', tenant_name: null })
    )
    localStorage.setItem('impersonate', JSON.stringify({ id: 7, name: 'Fibre Ltd' }))

    let asked = false
    server.use(
      rest.get(`${API}/auth/profile/`, (_req, res, ctx) => {
        asked = true
        return res(ctx.json({ id: 1, username: 'staff', tenant_name: null }))
      })
    )

    renderWithProviders(<AdminSidebar open onClose={() => {}} />)

    expect(screen.getByText('Fibre Ltd')).toBeInTheDocument()
    await waitFor(() => expect(asked).toBe(false))
  })
})
