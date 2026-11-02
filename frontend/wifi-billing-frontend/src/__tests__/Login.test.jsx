import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { rest } from 'msw'
import { server } from '../mocks/server'
import Login from '../pages/Login'

const mockNavigate = jest.fn()
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}))

function renderLogin() {
  return render(<MemoryRouter><Login /></MemoryRouter>)
}

describe('Login', () => {
  beforeEach(() => {
    mockNavigate.mockReset()
    localStorage.clear()
  })

  test('renders username field, password field and sign-in button', () => {
    renderLogin()
    expect(screen.getByPlaceholderText(/enter your username/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/enter your password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  test('disables button and shows "Signing in…" while request is in flight', async () => {
    renderLogin()
    fireEvent.change(screen.getByPlaceholderText(/enter your username/i), { target: { value: 'admin' } })
    fireEvent.change(screen.getByPlaceholderText(/enter your password/i), { target: { value: 'password' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))
    const btn = screen.getByRole('button', { name: /signing in/i })
    expect(btn).toBeDisabled()
  })

  test('shows error message when credentials are invalid (401)', async () => {
    server.use(
      rest.post('http://127.0.0.1:8000/api/auth/login/', (_req, res, ctx) =>
        res(ctx.status(401), ctx.json({ detail: 'No active account found' }))
      )
    )
    renderLogin()
    fireEvent.change(screen.getByPlaceholderText(/enter your username/i), { target: { value: 'wrong' } })
    fireEvent.change(screen.getByPlaceholderText(/enter your password/i), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => {
      expect(screen.getByText(/invalid username or password/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /sign in/i })).not.toBeDisabled()
  })

  test('redirects admin user to /admin/dashboard on success', async () => {
    renderLogin()
    fireEvent.change(screen.getByPlaceholderText(/enter your username/i), { target: { value: 'admin' } })
    fireEvent.change(screen.getByPlaceholderText(/enter your password/i), { target: { value: 'password' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/admin/dashboard', { replace: true })
    })
  })

  test('redirects customer user to /customer/pppoe on success', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/auth/profile/', (_req, res, ctx) =>
        res(ctx.json({ id: 2, username: 'cust1', role: 'customer' }))
      )
    )
    renderLogin()
    fireEvent.change(screen.getByPlaceholderText(/enter your username/i), { target: { value: 'cust1' } })
    fireEvent.change(screen.getByPlaceholderText(/enter your password/i), { target: { value: 'password' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/customer/pppoe', { replace: true })
    })
  })

  // ── Two-step sign-in ──────────────────────────────────────────────────────

  const API = 'http://127.0.0.1:8000/api'
  const enterPassword = () => {
    fireEvent.change(screen.getByPlaceholderText(/enter your username/i), { target: { value: 'admin' } })
    fireEvent.change(screen.getByPlaceholderText(/enter your password/i), { target: { value: 'password' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))
  }

  test('asks for a code, and only signs in once the code is accepted', async () => {
    let sentCode = null
    server.use(
      rest.post(`${API}/auth/login/`, (_req, res, ctx) =>
        res(ctx.json({ mfa_required: true, ticket: 't-123' }))),
      rest.post(`${API}/auth/login/mfa/`, async (req, res, ctx) => {
        const body = await req.json()
        sentCode = body
        return res(ctx.json({ access: 'a', refresh: 'r', role: 'tenant_admin' }))
      }),
    )
    renderLogin()
    enterPassword()

    await screen.findByText(/two-step verification/i)
    expect(localStorage.getItem('access_token')).toBeNull()

    fireEvent.change(screen.getByLabelText(/verification code/i), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: /verify/i }))

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled())
    expect(sentCode).toEqual({ ticket: 't-123', code: '123456' })
    expect(localStorage.getItem('access_token')).toBe('a')
  })

  test('a role that must have two-step is walked through setting it up', async () => {
    server.use(
      rest.post(`${API}/auth/login/`, (_req, res, ctx) =>
        res(ctx.json({ mfa_setup_required: true, ticket: 't-setup' }))),
      rest.post(`${API}/auth/mfa/setup/`, (_req, res, ctx) =>
        res(ctx.json({ qr: 'data:image/svg+xml;base64,AA==', secret: 'ABCDEF', otpauth_uri: 'otpauth://x' }))),
      rest.post(`${API}/auth/mfa/confirm/`, (_req, res, ctx) =>
        res(ctx.json({ backup_codes: ['AAAAA-BBBBB', 'CCCCC-DDDDD'], access: 'a', refresh: 'r', role: 'tenant_admin' }))),
    )
    renderLogin()
    enterPassword()

    await screen.findByAltText(/qr code/i)
    expect(screen.getByText('ABCDEF')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/verification code/i), { target: { value: '654321' } })
    fireEvent.click(screen.getByRole('button', { name: /turn on and continue/i }))

    await screen.findByText('AAAAA-BBBBB')
    // Not signed in until the codes are acknowledged.
    const go = screen.getByRole('button', { name: /saved them/i })
    expect(go).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(go)
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled())
  })

  test('says so when the account is locked', async () => {
    server.use(
      rest.post(`${API}/auth/login/`, (_req, res, ctx) =>
        res(ctx.status(429), ctx.json({ detail: 'Too many failed sign-in attempts. Try again in 15 minutes.', code: 'locked' }))),
    )
    renderLogin()
    enterPassword()
    await screen.findByText(/try again in 15 minutes/i)
  })
})
