import { screen, fireEvent, waitFor } from '@testing-library/react'
import { renderWithProviders } from '../test-utils'
import SystemSettings from '../pages/admin/SystemSettings'
import {
  fetchSystemSettings,
  updateSystemSettings,
  fetchSmsBalance,
} from '../services/settings'
import { fetchPackages } from '../services/packages'

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}))

jest.mock('../components/admin/AdminLayout', () => ({ children }) => <>{children}</>)

jest.mock('../services/settings', () => ({
  fetchSystemSettings: jest.fn(),
  updateSystemSettings: jest.fn(),
  fetchSmsBalance: jest.fn(),
  testMpesa: jest.fn(),
  testSms: jest.fn(),
  testWhatsapp: jest.fn(),
}))

jest.mock('../services/packages', () => ({ fetchPackages: jest.fn() }))

// Set here rather than in the factories: CRA's jest config resets mocks
// before every test, which would wipe them.
beforeEach(() => {
  fetchSystemSettings.mockResolvedValue({
    HOTSPOT_NOTICE: 'Till number has changed to 123456',
  })
  updateSystemSettings.mockResolvedValue({})
  fetchSmsBalance.mockResolvedValue(null)
  fetchPackages.mockResolvedValue({ results: [] })
})

const notice = () => screen.findByDisplayValue(/Till number has changed/)

/**
 * The switch used to be read off the text -- on meant "has text" -- so
 * clearing the notice to rewrite it switched it off and disabled the box
 * under the cursor, and nothing could be typed back in.
 */
describe('Portal notice', () => {
  test('clearing the wording leaves the box editable', async () => {
    renderWithProviders(<SystemSettings />)
    const box = await notice()

    fireEvent.change(box, { target: { value: '' } })
    expect(box).toBeEnabled()
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true')

    fireEvent.change(box, { target: { value: 'Back at 6pm' } })
    expect(box).toHaveValue('Back at 6pm')
  })

  test('the rewritten wording is what gets saved', async () => {
    renderWithProviders(<SystemSettings />)
    const box = await notice()

    fireEvent.change(box, { target: { value: '' } })
    fireEvent.change(box, { target: { value: 'Back at 6pm' } })
    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(updateSystemSettings).toHaveBeenCalled())
    expect(updateSystemSettings.mock.calls[0][0].HOTSPOT_NOTICE).toBe('Back at 6pm')
  })

  test('switching on with nothing written stays on and can be typed into', async () => {
    fetchSystemSettings.mockResolvedValue({ HOTSPOT_NOTICE: '' })
    renderWithProviders(<SystemSettings />)

    const toggle = await screen.findByRole('switch')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-checked', 'true')

    const box = screen.getByPlaceholderText(/M-Pesa message to reconnect/)
    expect(box).toBeEnabled()
    fireEvent.change(box, { target: { value: 'Closed Sunday' } })
    expect(box).toHaveValue('Closed Sunday')
  })

  test('switching off still keeps the wording and disables the box', async () => {
    renderWithProviders(<SystemSettings />)
    const box = await notice()

    fireEvent.click(screen.getByRole('switch'))
    expect(box).toBeDisabled()
    expect(box).toHaveValue('Till number has changed to 123456')
  })
})
