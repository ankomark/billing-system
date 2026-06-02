import { rest } from 'msw'

const API = 'http://127.0.0.1:8000/api'

export const handlers = [
  // ── Auth ──────────────────────────────────────────────────────────────────
  rest.post(`${API}/auth/login/`, (_req, res, ctx) =>
    res(ctx.json({ access: 'test-access-token', refresh: 'test-refresh-token' }))
  ),

  rest.get(`${API}/auth/profile/`, (_req, res, ctx) =>
    res(ctx.json({ id: 1, username: 'admin', role: 'admin' }))
  ),

  // ── Invoices ──────────────────────────────────────────────────────────────
  rest.get(`${API}/dashboard/invoices/unpaid/`, (_req, res, ctx) =>
    res(ctx.json({
      count: 2,
      total_pages: 1,
      current_page: 1,
      results: [
        { id: 1, invoice_number: 'INV-001', customer_name: 'John Doe',   total_amount: '500.00',  created_at: '2024-01-15T10:00:00Z' },
        { id: 2, invoice_number: 'INV-002', customer_name: 'Jane Smith', total_amount: '1500.00', created_at: '2024-01-16T10:00:00Z' },
      ],
    }))
  ),

  // ── Packages ──────────────────────────────────────────────────────────────
  rest.get(`${API}/packages/`, (_req, res, ctx) =>
    res(ctx.json({
      count: 1,
      total_pages: 1,
      current_page: 1,
      results: [
        { id: 1, name: 'Basic 30d', download_speed: 5, upload_speed: 2, duration_value: 30, duration_unit: 'days', price: '500.00', monthly_data_cap_gb: 10, is_hotspot: false },
      ],
    }))
  ),

  rest.get(`${API}/packages/:id/`, (_req, res, ctx) =>
    res(ctx.json({
      id: 1, name: 'Basic 30d', download_speed: 5, upload_speed: 2,
      duration_value: 30, duration_unit: 'days', price: '500.00',
      monthly_data_cap_gb: 10, is_hotspot: false,
    }))
  ),

  rest.patch(`${API}/packages/:id/`, (_req, res, ctx) =>
    res(ctx.json({ id: 1 }))
  ),

  rest.post(`${API}/packages/`, (_req, res, ctx) =>
    res(ctx.status(201), ctx.json({ id: 2 }))
  ),

  // ── Customers ────────────────────────────────────────────────────────────
  rest.get(`${API}/customers/`, (_req, res, ctx) =>
    res(ctx.json({
      count: 1,
      total_pages: 1,
      current_page: 1,
      results: [
        { id: 1, full_name: 'John Doe', phone: '254712345678', connection_type: 'pppoe', status: 'active', created_at: '2024-01-01T00:00:00Z' },
      ],
    }))
  ),

  rest.delete(`${API}/customers/:id/`, (_req, res, ctx) =>
    res(ctx.status(204))
  ),
]
