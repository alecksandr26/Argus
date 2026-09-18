import { test, expect, type Page } from '@playwright/test'
import { ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD, login, logout, uniqueSuffix } from './helpers'

/**
 * Exercises `POST /api/drivers` through the real Drivers screen, and the same write-gating
 * `trucks.spec.ts` covers for Fleet. One real wrinkle `Drivers.tsx` has that Fleet/Access don't:
 * the form has two fields both labeled "Phone" (the driver's own, and the emergency contact's —
 * `Drivers.tsx:404` and `:436`), so a plain `getByLabel('Phone')` hits Playwright's strict-mode
 * multi-match error. Disambiguated below by DOM order via `.nth()` — the driver's own phone
 * field comes first in the form.
 */

async function fillDriverForm(
  page: Page,
  opts: { firstName: string; lastName: string; license: string; phone: string },
) {
  await page.getByLabel('First name(s)').fill(opts.firstName)
  await page.getByLabel('Last name(s)').fill(opts.lastName)
  await page.getByLabel('License no.').fill(opts.license)
  await page.getByLabel('Expires').fill('2030-01-01')
  await page.getByLabel('Phone').nth(0).fill(opts.phone)
  // `getByLabel` matches by substring unless `exact: true` — without it, "Name" also matches
  // "First name(s)"/"Last name(s)" (a real strict-mode-violation hit running this for real).
  await page.getByLabel('Name', { exact: true }).fill('Emergency Contact')
  await page.getByLabel('Phone').nth(1).fill('5550001111')
  await page.getByLabel('Blood').fill('O+')
}

test('root_admin creates a driver via the Drivers screen', async ({ page }) => {
  const suffix = uniqueSuffix()
  const license = `LIC-${suffix}`

  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)

  await page.goto('/drivers')
  await page.getByRole('button', { name: 'Add driver' }).click()
  await fillDriverForm(page, {
    firstName: 'Dana',
    lastName: 'Driver',
    license,
    phone: '5552223333',
  })
  await page.getByRole('button', { name: 'Create driver' }).click()

  await expect(page.getByText(license, { exact: true })).toBeVisible()
})

test('a guardian sees Drivers read-only: no create button, existing driver fields disabled', async ({
  page,
}) => {
  const suffix = uniqueSuffix()
  const license = `LIC-${suffix}`
  const guardianEmail = `guardian-${suffix}@argus.dev`
  const guardianPassword = 'guardian-pass-123'

  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)

  // Seed a driver this guardian will view.
  await page.goto('/drivers')
  await page.getByRole('button', { name: 'Add driver' }).click()
  await fillDriverForm(page, {
    firstName: 'Dana',
    lastName: 'Driver',
    license,
    phone: '5552223333',
  })
  await page.getByRole('button', { name: 'Create driver' }).click()
  await expect(page.getByText(license, { exact: true })).toBeVisible()

  // Seed the guardian account itself via Access.
  await page.goto('/access')
  await page.getByRole('button', { name: 'Add user' }).click()
  await page.getByLabel('First name').fill('Gary')
  await page.getByLabel('Last name').fill('Guardian')
  await page.getByLabel('Email').fill(guardianEmail)
  await page.getByLabel('Phone').fill('5559876543')
  await page.getByLabel('Role').selectOption('guardian')
  await page.getByLabel('Password').fill(guardianPassword)
  await page.getByRole('button', { name: 'Create user' }).click()
  await expect(page.getByText(guardianEmail)).toBeVisible()

  await logout(page)
  await login(page, guardianEmail, guardianPassword)

  await page.goto('/drivers')
  await expect(page.getByRole('button', { name: 'Add driver' })).toHaveCount(0)

  await page.getByText(license, { exact: true }).click()
  await expect(page.getByText('View driver')).toBeVisible()
  await expect(page.getByLabel('License no.')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Save changes' })).toHaveCount(0)
  // Same "Close" disambiguation as trucks.spec.ts — the icon close button and the read-only
  // panel's text button both share that accessible name; `.last()` is the text one.
  await expect(page.getByRole('button', { name: 'Close' }).last()).toBeVisible()
})
