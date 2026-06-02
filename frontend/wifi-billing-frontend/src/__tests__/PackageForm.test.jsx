import { screen, fireEvent, waitFor } from '@testing-library/react'
import { Routes, Route } from 'react-router-dom'
import { rest } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders } from '../test-utils'
import PackageForm from '../pages/admin/PackageForm'

jest.mock('../components/admin/AdminLayout', () =>
  function AdminLayout({ children }) { return <div>{children}</div> }
)

const mockNavigate = jest.fn()
jest.mock('react-router-dom', () => ({
  ...jest.requireActual('react-router-dom'),
  useNavigate: () => mockNavigate,
}))

// Create mode: no :id param → useParams returns {} → isEdit = false
function renderCreate() {
  return renderWithProviders(<PackageForm />)
}

// Edit mode: MemoryRouter navigates to /packages/1 so useParams returns { id: '1' }
function renderEdit() {
  return renderWithProviders(
    <Routes>
      <Route path="/packages/:id" element={<PackageForm />} />
    </Routes>,
    { route: '/packages/1' }
  )
}

describe('PackageForm — create mode', () => {
  beforeEach(() => mockNavigate.mockReset())

  test('renders all fields including monthly data cap and hotspot toggle', () => {
    renderCreate()
    // Use exact placeholder strings (not regex) to avoid ambiguous matches
    expect(screen.getByPlaceholderText('e.g. Basic 5Mbps')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('e.g. 5')).toBeInTheDocument()   // download_speed
    expect(screen.getByPlaceholderText('e.g. 2')).toBeInTheDocument()   // upload_speed
    expect(screen.getByPlaceholderText('e.g. 30')).toBeInTheDocument()  // duration_value
    expect(screen.getByPlaceholderText('e.g. 500')).toBeInTheDocument() // price
    expect(screen.getByPlaceholderText('0 = unlimited')).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
  })

  test('is_hotspot toggle starts unchecked and toggles on click', () => {
    renderCreate()
    const toggle = screen.getByRole('checkbox')
    expect(toggle).not.toBeChecked()
    fireEvent.click(toggle)
    expect(toggle).toBeChecked()
    fireEvent.click(toggle)
    expect(toggle).not.toBeChecked()
  })

  test('calls POST /packages/ on submit and navigates away', async () => {
    let capturedBody = null
    server.use(
      rest.post('http://127.0.0.1:8000/api/packages/', async (req, res, ctx) => {
        capturedBody = await req.json()
        return res(ctx.status(201), ctx.json({ id: 2 }))
      })
    )
    renderCreate()
    fireEvent.change(screen.getByPlaceholderText('e.g. Basic 5Mbps'), { target: { value: 'Test Pkg' } })
    fireEvent.change(screen.getByPlaceholderText('e.g. 5'),           { target: { value: '10'  } })
    fireEvent.change(screen.getByPlaceholderText('e.g. 2'),           { target: { value: '5'   } })
    fireEvent.change(screen.getByPlaceholderText('e.g. 30'),          { target: { value: '30'  } })
    fireEvent.change(screen.getByPlaceholderText('e.g. 500'),         { target: { value: '500' } })
    fireEvent.click(screen.getByRole('button', { name: /create package/i }))
    await waitFor(() => {
      expect(capturedBody).not.toBeNull()
      expect(capturedBody.name).toBe('Test Pkg')
      expect(mockNavigate).toHaveBeenCalledWith('/admin/packages')
    })
  })
})

describe('PackageForm — edit mode', () => {
  beforeEach(() => mockNavigate.mockReset())

  test('pre-fills all fields from fetched data including monthly_data_cap_gb and is_hotspot', async () => {
    renderEdit()
    await waitFor(() => {
      expect(screen.getByDisplayValue('Basic 30d')).toBeInTheDocument()
      expect(screen.getByDisplayValue('500.00')).toBeInTheDocument() // price
      expect(screen.getByDisplayValue('10')).toBeInTheDocument()     // monthly_data_cap_gb
    })
    expect(screen.getByRole('checkbox')).not.toBeChecked()           // is_hotspot: false
  })

  test('pre-fills is_hotspot toggle when package has is_hotspot: true', async () => {
    server.use(
      rest.get('http://127.0.0.1:8000/api/packages/:id/', (_req, res, ctx) =>
        res(ctx.json({
          id: 1, name: 'Hotspot 1hr', download_speed: 5, upload_speed: 2,
          duration_value: 1, duration_unit: 'hours', price: '50.00',
          monthly_data_cap_gb: 0, is_hotspot: true,
        }))
      )
    )
    renderEdit()
    await waitFor(() => {
      expect(screen.getByRole('checkbox')).toBeChecked()
    })
  })

  test('calls PATCH (not PUT) on save and navigates away', async () => {
    let method = null
    server.use(
      rest.patch('http://127.0.0.1:8000/api/packages/:id/', (req, res, ctx) => {
        method = req.method
        return res(ctx.json({ id: 1 }))
      })
    )
    renderEdit()
    await screen.findByDisplayValue('Basic 30d')
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() => {
      expect(method).toBe('PATCH')
      expect(mockNavigate).toHaveBeenCalledWith('/admin/packages')
    })
  })
})
