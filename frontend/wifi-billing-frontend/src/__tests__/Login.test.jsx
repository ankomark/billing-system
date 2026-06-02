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
})
