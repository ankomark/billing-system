// Smoke test — verifies the module graph loads without crashing.
// Full component tests live in src/__tests__/.
test('App module imports without throwing', () => {
  expect(() => require('./App')).not.toThrow()
})
