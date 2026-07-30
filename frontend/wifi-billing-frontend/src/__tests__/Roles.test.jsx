import { screen } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { renderWithProviders } from '../test-utils'
import ProtectedRoute from '../components/ProtectedRoute'
import {
  homeFor, isPlatformRole,
  PLATFORM_OWNER, PLATFORM_STAFF, TENANT_ADMIN, TENANT_STAFF, CUSTOMER,
  OPERATOR_ROLES, OPERATOR_ADMIN_ROLES, PLATFORM_ROLES,
} from '../constants/roles'

function signedInAs(role) {
  localStorage.setItem('access_token', 'test-token')
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'u', role }))
}

function renderGuarded(allowedRoles, route = '/guarded') {
  return renderWithProviders(
    <Routes>
      <Route
        path="/guarded"
        element={
          <ProtectedRoute allowedRoles={allowedRoles}>
            <p>secret</p>
          </ProtectedRoute>
        }
      />
      <Route path="/login" element={<p>login page</p>} />
      <Route path="/platform" element={<p>platform home</p>} />
      <Route path="/admin/dashboard" element={<p>operator home</p>} />
      <Route path="/customer/pppoe" element={<p>subscriber home</p>} />
    </Routes>,
    { route }
  )
}

beforeEach(() => localStorage.clear())

describe('role constants', () => {
  test('operator roles include platform staff, so support can act for them', () => {
    expect(OPERATOR_ROLES).toEqual(
      expect.arrayContaining([TENANT_ADMIN, TENANT_STAFF, PLATFORM_OWNER, PLATFORM_STAFF])
    )
    expect(OPERATOR_ADMIN_ROLES).not.toContain(TENANT_STAFF)
  })

  test('only platform roles count as platform', () => {
    expect(isPlatformRole(PLATFORM_OWNER)).toBe(true)
    expect(isPlatformRole(PLATFORM_STAFF)).toBe(true)
    expect(isPlatformRole(TENANT_ADMIN)).toBe(false)
    expect(isPlatformRole(CUSTOMER)).toBe(false)
  })

  test('each role has a home', () => {
    expect(homeFor(PLATFORM_OWNER)).toBe('/platform')
    expect(homeFor(TENANT_ADMIN)).toBe('/admin/dashboard')
    expect(homeFor(CUSTOMER)).toBe('/customer/pppoe')
  })
})

describe('ProtectedRoute', () => {
  test('sends anonymous visitors to login', () => {
    renderGuarded(OPERATOR_ROLES)
    expect(screen.getByText('login page')).toBeInTheDocument()
  })

  // The regression that made the whole operator dashboard unreachable: the
  // backend renamed roles and these literals stopped matching anything.
  test.each([TENANT_ADMIN, TENANT_STAFF, PLATFORM_OWNER, PLATFORM_STAFF])(
    'admits %s to operator routes',
    (role) => {
      signedInAs(role)
      renderGuarded(OPERATOR_ROLES)
      expect(screen.getByText('secret')).toBeInTheDocument()
    }
  )

  test('keeps subscribers out of operator routes', () => {
    signedInAs(CUSTOMER)
    renderGuarded(OPERATOR_ROLES)
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
    expect(screen.getByText('subscriber home')).toBeInTheDocument()
  })

  test('keeps operators out of platform routes', () => {
    signedInAs(TENANT_ADMIN)
    renderGuarded(PLATFORM_ROLES)
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
    expect(screen.getByText('operator home')).toBeInTheDocument()
  })

  test('keeps ordinary operator staff out of admin-only routes', () => {
    signedInAs(TENANT_STAFF)
    renderGuarded(OPERATOR_ADMIN_ROLES)
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
  })

  test('a stored session with no role goes back to login', () => {
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'u' }))
    renderGuarded(OPERATOR_ROLES)
    expect(screen.getByText('login page')).toBeInTheDocument()
  })

  test('does not loop when a role is barred from its own home', () => {
    // What the stale role names caused: an operator was redirected to
    // /admin/dashboard, which failed the very same check, forever. The guard
    // has to be mounted *at* that home for the loop to be reachable.
    signedInAs(TENANT_ADMIN)
    renderWithProviders(
      <Routes>
        <Route
          path="/admin/dashboard"
          element={
            <ProtectedRoute allowedRoles={PLATFORM_ROLES}>
              <p>secret</p>
            </ProtectedRoute>
          }
        />
        <Route path="/login" element={<p>login page</p>} />
      </Routes>,
      { route: '/admin/dashboard' }
    )
    expect(screen.getByText('login page')).toBeInTheDocument()
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
  })
})
