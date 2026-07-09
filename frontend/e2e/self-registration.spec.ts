import { test, expect } from '@playwright/test'

// ABKIT_ALLOW_SELF_REGISTRATION не задан в e2e-стеке (см. .github/workflows/ci.yml
// и docker-compose .env) -> по умолчанию false, ничего похожего на регистрацию
// в UI быть не должно. Оба состояния флага на уровне API/сервиса — backend/
// tests/test_auth.py::test_config_reports_self_registration_* и
// tests/test_auth.py::test_self_register_* (там же — включенное состояние).
test('registration is not hinted at anywhere on the login page when disabled', async ({ page }) => {
  await page.goto('/login')

  await expect(page.getByText('Register')).toHaveCount(0)
  await expect(page.getByText('Create Account')).toHaveCount(0)
})

test('register API returns 403 when self-registration is disabled', async ({ request }) => {
  const base = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'
  const resp = await request.post(`${base}/auth/register`, {
    data: { email: 'blocked@e2e.test', first_name: 'Blocked', password: 'pw12345678' },
  })
  expect(resp.status()).toBe(403)
})
