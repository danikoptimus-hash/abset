import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'

// Keycloak OIDC SSO — сквозные браузерные сценарии против НАСТОЯЩЕГО Keycloak
// (docker-compose.keycloak.yml, реалм abset-dev).
//
// Запуск ТОЛЬКО так:  bash scripts/e2e.sh --keycloak
// Без флага E2E_KEYCLOAK_URL не выставлен и весь файл пропускается — обычный
// прогон e2e остается быстрым и не требует поднятого IdP.
//
// Пользователи реалма: alice -> abset-admins, bob -> abset-editors,
// carol -> без групп, dave -> email не подтвержден. Пароль у всех "password".

const KEYCLOAK = process.env.E2E_KEYCLOAK_URL ?? ''
const REALM = 'abset-dev'

test.skip(!KEYCLOAK, 'SSO specs need the dev Keycloak — run: bash scripts/e2e.sh --keycloak')

// Keycloak поднимается медленнее остального стека, а первый вход еще и
// прогревает JWKS/discovery.
test.describe.configure({ mode: 'serial' })

/** Токен админа мастер-реалма — им управляем пользователями/группами. */
async function kcAdminToken(request: APIRequestContext): Promise<string> {
  const resp = await request.post(`${KEYCLOAK}/realms/master/protocol/openid-connect/token`, {
    form: { client_id: 'admin-cli', username: 'admin', password: 'admin', grant_type: 'password' },
  })
  if (!resp.ok()) throw new Error(`keycloak admin login failed: ${resp.status()}`)
  return (await resp.json()).access_token as string
}

async function kcUserId(request: APIRequestContext, token: string, username: string): Promise<string> {
  const resp = await request.get(`${KEYCLOAK}/admin/realms/${REALM}/users?username=${username}&exact=true`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  const users = await resp.json()
  if (!users.length) throw new Error(`keycloak user '${username}' not found`)
  return users[0].id as string
}

async function kcSetEnabled(request: APIRequestContext, username: string, enabled: boolean) {
  const token = await kcAdminToken(request)
  const id = await kcUserId(request, token, username)
  const resp = await request.put(`${KEYCLOAK}/admin/realms/${REALM}/users/${id}`, {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: { enabled },
  })
  expect(resp.status()).toBe(204)
}

async function kcGroupMembership(
  request: APIRequestContext,
  username: string,
  group: string,
  action: 'join' | 'leave',
) {
  const token = await kcAdminToken(request)
  const id = await kcUserId(request, token, username)
  const groups = await (
    await request.get(`${KEYCLOAK}/admin/realms/${REALM}/groups`, {
      headers: { Authorization: `Bearer ${token}` },
    })
  ).json()
  const target = groups.find((g: { name: string }) => g.name === group)
  if (!target) throw new Error(`keycloak group '${group}' not found`)
  const url = `${KEYCLOAK}/admin/realms/${REALM}/users/${id}/groups/${target.id}`
  const headers = { Authorization: `Bearer ${token}` }
  const resp = action === 'join'
    ? await request.put(url, { headers })
    : await request.delete(url, { headers })
  expect(resp.status()).toBe(204)
}

/** Проходит форму логина Keycloak, если она показалась. */
async function signInAt(page: Page, username: string, password = 'password') {
  await page.waitForLoadState('domcontentloaded')
  if (await page.locator('#username').count()) {
    await page.fill('#username', username)
    await page.fill('#password', password)
    await page.click('#kc-login')
    await page.waitForLoadState('networkidle')
  }
}

/** Полный цикл: страница логина ABSet -> кнопка SSO -> Keycloak -> назад. */
async function ssoSignIn(page: Page, username: string, password = 'password') {
  await page.goto('/login')
  await page.getByRole('link', { name: 'Sign in with SSO' }).click()
  await signInAt(page, username, password)
}

async function currentUser(page: Page): Promise<Record<string, unknown> | null> {
  const resp = await page.request.get('/api/v1/auth/me')
  return resp.ok() ? await resp.json() : null
}

/** Записи журнала по действию, прочитанные break-glass админом.
 *
 * Отдельный APIRequestContext, а не `page.request`: у страницы в этот момент
 * может быть сессия SSO-пользователя (или не быть никакой), а /api/v1/audit —
 * admin-only. Собственный контекст со своей cookie-банкой не трогает сессию,
 * которую проверяет сам тест. */
async function auditEntries(
  request: APIRequestContext,
  action: string,
): Promise<Record<string, unknown>[]> {
  const login = await request.post('/api/v1/auth/login', {
    data: { email: 'admin@e2e.test', password: 'e2epass123' },
  })
  expect(login.ok()).toBeTruthy()
  const resp = await request.get(`/api/v1/audit?action=${encodeURIComponent(action)}&page_size=50`)
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).items ?? []
}

test('login page offers SSO as the primary action with password collapsed underneath', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('link', { name: 'Sign in with SSO' })).toBeVisible()
  // Парольная форма есть, но убрана под спойлер — поля не видны сразу.
  await expect(page.getByText('Sign in with password')).toBeVisible()
  await expect(page.getByLabel('Password')).toBeHidden()

  await page.getByText('Sign in with password').click()
  await expect(page.getByLabel('Password')).toBeVisible()
})

test('alice signs in through SSO and is provisioned as admin', async ({ page }) => {
  await ssoSignIn(page, 'alice')
  await expect(page).toHaveURL(/\/experiments$/)

  const me = await currentUser(page)
  expect(me).toMatchObject({
    email: 'alice@abset-dev.test',
    role: 'admin',
    auth_provider: 'oidc',
  })
})

test('bob signs in through SSO and is provisioned as editor', async ({ page }) => {
  await ssoSignIn(page, 'bob')
  await expect(page).toHaveURL(/\/experiments$/)
  expect(await currentUser(page)).toMatchObject({
    email: 'bob@abset-dev.test',
    role: 'editor',
    auth_provider: 'oidc',
  })
})

test('an SSO user has no password-change UI in their profile', async ({ page }) => {
  await ssoSignIn(page, 'alice')
  await page.goto('/profile')
  await expect(page.getByText('This account signs in through corporate SSO')).toBeVisible()
  // Ни одного поля пароля: менять нечего.
  await expect(page.getByLabel('Current Password')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Change Password' })).toHaveCount(0)
})

test('admin list shows which accounts come from SSO and offers no password reset for them', async ({ page }) => {
  await ssoSignIn(page, 'alice') // alice — admin, ей доступна страница Admin
  await page.goto('/admin')
  const bobRow = page.getByRole('row', { name: /alice@abset-dev\.test/ })
  await expect(bobRow.getByText('SSO')).toBeVisible()
  await expect(bobRow.getByRole('button', { name: 'Reset Password' })).toHaveCount(0)
})

test('carol has no mapped group and is rejected with a human error page', async ({ page }) => {
  await ssoSignIn(page, 'carol')
  await expect(page.getByRole('heading', { name: 'Could not sign you in' })).toBeVisible()
  await expect(page.getByText(/not a member of any group/)).toBeVisible()
  // Оба выхода из тупика на месте (ТЗ п.3).
  await expect(page.getByRole('link', { name: 'Try again' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Use password instead' })).toBeVisible()
  expect(await currentUser(page)).toBeNull()
})

test('dave has an unverified email and is rejected', async ({ page }) => {
  await ssoSignIn(page, 'dave')
  await expect(page.getByRole('heading', { name: 'Could not sign you in' })).toBeVisible()
  await expect(page.getByText(/not marked as verified/)).toBeVisible()
  expect(await currentUser(page)).toBeNull()
})

test('"use password instead" returns to the login page with the password form open', async ({ page }) => {
  await ssoSignIn(page, 'carol')
  await page.getByRole('link', { name: 'Use password instead' }).click()
  await expect(page).toHaveURL(/\/login\?sso=failed$/)
  // Форма развернута сразу — второй раз кликать спойлер не нужно.
  await expect(page.getByLabel('Password')).toBeVisible()
})

test('a group change is picked up on the next sign-in', async ({ page, request }) => {
  await ssoSignIn(page, 'bob')
  expect((await currentUser(page))!.role).toBe('editor')
  await page.request.post('/api/v1/auth/logout')

  await kcGroupMembership(request, 'bob', 'abset-admins', 'join')
  try {
    await ssoSignIn(page, 'bob')
    // Побеждает самая высокая роль: bob теперь и editor, и admin.
    expect((await currentUser(page))!.role).toBe('admin')
    const changes = await auditEntries(request, 'auth.oidc_role_changed')
    expect(changes.some((e) => e.user_email === 'bob@abset-dev.test')).toBeTruthy()
  } finally {
    await kcGroupMembership(request, 'bob', 'abset-admins', 'leave')
  }
})

test('password login still works while SSO is enabled', async ({ page }) => {
  // Break-glass админ (заведен паролем в scripts/e2e.sh) обязан входить и при
  // включенном SSO — ТЗ прямо запрещает убирать парольный путь.
  await page.goto('/login')
  await page.getByText('Sign in with password').click()
  await page.getByLabel('Email').fill('admin@e2e.test')
  await page.getByLabel('Password').fill('e2epass123')
  // exact: без него подстрочное совпадение ловит и заголовок спойлера
  // "Sign in with password", который тоже role=button.
  await page.getByRole('button', { name: 'Sign In', exact: true }).click()
  await expect(page).toHaveURL(/\/experiments$/)
  expect(await currentUser(page)).toMatchObject({
    email: 'admin@e2e.test',
    auth_provider: 'password',
  })
})

// ---------------------------------------------------------------------------
// THE FIRING SCENARIO
// ---------------------------------------------------------------------------

test('THE FIRING SCENARIO: a disabled employee loses access and cannot fall back to a password', async ({
  page,
  request,
}) => {
  // 1. Пока bob работает — он входит и получает свою роль.
  await ssoSignIn(page, 'bob')
  expect((await currentUser(page))!.role).toBe('editor')
  await page.request.post('/api/v1/auth/logout')

  // 2. Его увольняют: аккаунт отключают в Keycloak (в проде это делает
  //    AD-синхронизация). С нашей стороны не делается НИЧЕГО.
  await kcSetEnabled(request, 'bob', false)
  try {
    // 3. Следующая попытка входа не проходит. Keycloak отказывает на СВОЕЙ
    //    форме (до нашего callback'а), поэтому пользователь видит ошибку
    //    Keycloak, а не нашу страницу — важно, что сессии ABSet он не
    //    получает ни при каком раскладе.
    await ssoSignIn(page, 'bob')
    expect(await currentUser(page)).toBeNull()
    await expect(page).not.toHaveURL(/\/experiments$/)

    // 4. И отступить на пароль он не может — пароля у SSO-аккаунта нет.
    const passwordAttempt = await page.request.post('/api/v1/auth/login', {
      data: { email: 'bob@abset-dev.test', password: 'password' },
      failOnStatusCode: false,
    })
    expect(passwordAttempt.status()).toBe(401)
    expect((await passwordAttempt.json()).error.message).toContain('corporate SSO')
  } finally {
    await kcSetEnabled(request, 'bob', true)
  }
})

test('FIRING, group-revocation variant: access removed leaves our own audit trail', async ({
  page,
  request,
}) => {
  // Второй способ отобрать доступ — вынуть из групп доступа, не трогая сам
  // аккаунт. Здесь пользователь ДОХОДИТ до нашего callback'а, поэтому именно
  // этот вариант дает нашу страницу ошибки и запись auth.oidc_login_rejected.
  await kcGroupMembership(request, 'bob', 'abset-editors', 'leave')
  try {
    await ssoSignIn(page, 'bob')
    await expect(page.getByRole('heading', { name: 'Could not sign you in' })).toBeVisible()
    await expect(page.getByText(/not a member of any group/)).toBeVisible()
    expect(await currentUser(page)).toBeNull()

    const rejected = await auditEntries(request, 'auth.oidc_login_rejected')
    expect(
      rejected.some(
        (e) =>
          e.user_email === 'bob@abset-dev.test' &&
          (e.details as { reason?: string })?.reason === 'no_role_mapping',
      ),
    ).toBeTruthy()

    // И пароля у него по-прежнему нет.
    const passwordAttempt = await page.request.post('/api/v1/auth/login', {
      data: { email: 'bob@abset-dev.test', password: 'password' },
      failOnStatusCode: false,
    })
    expect(passwordAttempt.status()).toBe(401)
  } finally {
    await kcGroupMembership(request, 'bob', 'abset-editors', 'join')
  }
})

test('a forged Host header cannot move the redirect_uri off ABKIT_PUBLIC_URL', async ({ request }) => {
  const resp = await request.get('/api/v1/auth/oidc/login', {
    headers: { Host: 'evil.example.com', 'X-Forwarded-Host': 'evil.example.com' },
    maxRedirects: 0,
    failOnStatusCode: false,
  })
  expect(resp.status()).toBe(302)
  const location = resp.headers()['location']
  const redirectUri = new URL(location).searchParams.get('redirect_uri')!
  expect(redirectUri).not.toContain('evil.example.com')
  expect(redirectUri).toContain('/api/v1/auth/oidc/callback')
})

test('a tampered state is refused with our error page', async ({ page }) => {
  // Начинаем настоящий вход (получаем tx-cookie), но возвращаемся с чужим state.
  await page.goto('/login')
  await page.getByRole('link', { name: 'Sign in with SSO' }).click()
  await page.waitForLoadState('domcontentloaded')

  await page.goto('/api/v1/auth/oidc/callback?code=stolen&state=not-the-issued-state')
  await expect(page.getByRole('heading', { name: 'Could not sign you in' })).toBeVisible()
  await expect(page.getByText(/state mismatch/)).toBeVisible()
  expect(await currentUser(page)).toBeNull()
})
