import { test, expect } from '@playwright/test'

/**
 * The first real integration spec — drives the actual `ui-argus` dev server against the actual
 * `backend-argus` + Mongo, proving the login work in this plan genuinely works end to end, not
 * just against each side's own mocks/fixtures. Credentials match the root_admin bootstrap
 * defaults (`ROOT_ADMIN_EMAIL`/`ROOT_ADMIN_PASSWORD`, see `src/backend-argus/app/auth/
 * bootstrap.py`), which this module's own `docker-compose.yml` leaves at their checked-in
 * defaults on purpose.
 */
const ROOT_ADMIN_EMAIL = 'admin@argus.dev'
const ROOT_ADMIN_PASSWORD = 'changeme123'

async function login(page: import('@playwright/test').Page, email: string, password: string) {
  await page.getByLabel('Email address').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

test('logs in as the bootstrapped root admin and lands on the dashboard', async ({ page }) => {
  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)

  await expect(page).toHaveURL('/')
  // `getByText` would match both the sidebar nav link and the page's own <h1> — the heading
  // role disambiguates to the actual page content, not just the nav.
  await expect(page.getByRole('heading', { name: 'Live operations' })).toBeVisible()
  // The bootstrapped account's name (ROOT_ADMIN_FIRST_NAME/LAST_NAME defaults) shows in the
  // sidebar footer, proving the real session — not just a redirect — round-tripped.
  await expect(page.getByText('Root Admin')).toBeVisible()
})

test('wrong password shows an inline error and does not navigate', async ({ page }) => {
  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, 'definitely-the-wrong-password')

  await expect(page.getByRole('alert')).toHaveText(/invalid email or password/i)
  await expect(page).toHaveURL('/login')
})

test('an unauthenticated deep link redirects to login and back after signing in', async ({
  page,
}) => {
  await page.goto('/fleet')
  await expect(page).toHaveURL('/login')

  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)

  // Proves ProtectedRoute's "return to where you were headed" behavior, not just "redirect to /".
  await expect(page).toHaveURL('/fleet')
  await expect(page.getByRole('heading', { name: 'Fleet' })).toBeVisible()
})

test('signing out clears the session and protects routes again', async ({ page }) => {
  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)
  await expect(page).toHaveURL('/')

  await page.getByText('Sign out').click()
  await expect(page).toHaveURL('/login')

  await page.goto('/fleet')
  await expect(page).toHaveURL('/login')
})
