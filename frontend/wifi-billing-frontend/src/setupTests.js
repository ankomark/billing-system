import '@testing-library/jest-dom'
import { server } from './mocks/server'

// react-router v7 uses TextEncoder/TextDecoder which JSDOM doesn't expose
const { TextEncoder, TextDecoder } = require('util')
global.TextEncoder = TextEncoder
global.TextDecoder = TextDecoder

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
