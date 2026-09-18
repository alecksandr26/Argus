import { createHash } from 'node:crypto'
import { test, expect, type Page } from '@playwright/test'
import { ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD, BACKEND_URL, login, logout, uniqueSuffix } from './helpers'

/**
 * Exercises `POST /api/users` through the real Access screen, and — for the one rule a UI-only
 * test can't fully prove — directly against `/api/users` too. `app/routers/users.py`'s
 * `_ALLOWED_ROLES`/`_require_admin_scope` restrict a non-root_admin `admin` actor to creating
 * `guardian`-role accounts only; `Access.tsx` mirrors that client-side by disabling the role
 * picker, but disabling a `<select>` proves nothing about the server, so the third test below
 * calls the endpoint directly instead of trusting the UI not to send a disallowed role.
 */

function sha256Hex(raw: string): string {
  return createHash('sha256').update(raw).digest('hex')
}

async function createUserViaAccessPanel(
  page: Page,
  opts: {
    addButtonName: string
    firstName: string
    lastName: string
    email: string
    phone: string
    role?: 'root_admin' | 'admin' | 'guardian' | 'truck_driver'
    password: string
  },
) {
  // Skip the navigation if we're already there — a caller may have just landed on /access via
  // login()'s own redirect (see helpers.ts), and an unnecessary extra full-page reload right on
  // top of that turned out to be a real source of flakiness running this for real.
  if (!page.url().endsWith('/access')) {
    await page.goto('/access')
  }
  await page.getByRole('button', { name: opts.addButtonName }).click()
  await page.getByLabel('First name').fill(opts.firstName)
  await page.getByLabel('Last name').fill(opts.lastName)
  await page.getByLabel('Email').fill(opts.email)
  await page.getByLabel('Phone').fill(opts.phone)
  if (opts.role) {
    await page.getByLabel('Role').selectOption(opts.role)
  }
  await page.getByLabel('Password').fill(opts.password)
  await page.getByRole('button', { name: 'Create user' }).click()
  // Wait for the create to actually round-trip before returning — a caller that immediately
  // signs out (as several specs here do, to test the new account) can otherwise race the
  // `POST /api/users` fetch and abort it before the backend ever sees it, a real bug hit
  // running this for real (see users.spec.ts's git history / the account "created" here
  // silently never existing, then 401ing on the very next login).
  await expect(page.getByText(opts.email, { exact: true })).toBeVisible()
}

test('root_admin creates an admin account, who can log in and is scoped to guardians only', async ({
  page,
}) => {
  const suffix = uniqueSuffix()
  const adminEmail = `operator-${suffix}@argus.dev`
  const adminPassword = 'operator-pass-123'

  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  await expect(page).toHaveURL('/')

  await createUserViaAccessPanel(page, {
    addButtonName: 'Add user',
    firstName: 'Ops',
    lastName: 'Operator',
    email: adminEmail,
    phone: '5551234567',
    role: 'admin',
    password: adminPassword,
  })

  await logout(page)
  await login(page, adminEmail, adminPassword)
  // Signed out from /access, so ProtectedRoute's "return to where you were headed" behavior
  // (already covered by auth.spec.ts's deep-link test) lands the re-login back on /access, not
  // '/' — this admin is allowed there, so RequireRole doesn't bounce it away.
  await expect(page).toHaveURL('/access')
  await expect(page.getByText('Ops Operator')).toBeVisible()

  // As this admin, Access's create button reads "Add guardian" (not "Add user") and the Role
  // picker is locked to guardian-only — the server's scoping mirrored client-side.
  await page.getByRole('button', { name: 'Add guardian' }).click()
  await expect(page.getByLabel('Role')).toBeDisabled()
  await expect(page.getByLabel('Role')).toHaveValue('guardian')
})

test('an admin creates a guardian, who is then locked out of Access entirely', async ({
  page,
}) => {
  const suffix = uniqueSuffix()
  const adminEmail = `operator-${suffix}@argus.dev`
  const adminPassword = 'operator-pass-123'
  const guardianEmail = `guardian-${suffix}@argus.dev`
  const guardianPassword = 'guardian-pass-123'

  // Setup: root_admin creates the admin this test actually exercises.
  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  await createUserViaAccessPanel(page, {
    addButtonName: 'Add user',
    firstName: 'Ops',
    lastName: 'Operator',
    email: adminEmail,
    phone: '5551234567',
    role: 'admin',
    password: adminPassword,
  })
  await logout(page)

  // The admin creates a guardian — the one role this account is actually allowed to create.
  await login(page, adminEmail, adminPassword)
  await createUserViaAccessPanel(page, {
    addButtonName: 'Add guardian',
    firstName: 'Gary',
    lastName: 'Guardian',
    email: guardianEmail,
    phone: '5559876543',
    password: guardianPassword,
  })
  await logout(page)

  // The new guardian can log in, but RequireRole bounces /access straight back to '/' — a
  // guardian never even sees the screen that created it.
  await login(page, guardianEmail, guardianPassword)
  await expect(page).toHaveURL('/')
  await page.goto('/access')
  await expect(page).toHaveURL('/')
})

test('REST: an admin actor is rejected (403) creating a non-guardian user directly against the API', async ({
  page,
  request,
}) => {
  const suffix = uniqueSuffix()
  const adminEmail = `operator-${suffix}@argus.dev`
  const adminPassword = 'operator-pass-123'

  // Setup via the UI (root_admin creating the admin under test) — same as the other specs here,
  // kept consistent rather than reaching for a backend-only shortcut for setup.
  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  await createUserViaAccessPanel(page, {
    addButtonName: 'Add user',
    firstName: 'Ops',
    lastName: 'Operator',
    email: adminEmail,
    phone: '5551234567',
    role: 'admin',
    password: adminPassword,
  })
  await logout(page)

  // From here on, talk to the backend directly — this is the part `Access.tsx`'s disabled
  // <select> can never prove: that the server rejects the request even if a client sends it.
  const loginRes = await request.post(`${BACKEND_URL}/api/auth/login`, {
    data: { email: adminEmail, password: sha256Hex(adminPassword) },
  })
  expect(loginRes.status()).toBe(200)
  const { access_token: token } = await loginRes.json()

  const createRes = await request.post(`${BACKEND_URL}/api/users`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      email: `escalation-${suffix}@argus.dev`,
      password: sha256Hex('whatever-123'),
      role: 'admin',
      first_name: 'Should',
      last_name: 'Fail',
      phone_number: '5550000000',
    },
  })
  expect(createRes.status()).toBe(403)
})
