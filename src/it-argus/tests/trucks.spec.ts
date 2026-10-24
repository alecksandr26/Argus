import { test, expect } from '@playwright/test'
import {
  ROOT_ADMIN_EMAIL,
  ROOT_ADMIN_PASSWORD,
  gotoLoadedList,
  login,
  logout,
  submitCreate,
  uniqueSuffix,
} from './helpers'

/**
 * Exercises `POST /api/trucks` through the real Fleet screen, and the write-gating that keeps a
 * `guardian` session to a read-only view of the same screen (`Fleet.tsx`'s `canWrite`/`readOnly`
 * — already unit-tested in `ui-argus`'s own `Fleet.test.tsx`, but never before against a real
 * browser + real backend together).
 */

test('root_admin creates a truck via the Fleet screen', async ({ page }) => {
  const plate = `IT-${uniqueSuffix()}`

  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)

  await gotoLoadedList(page, '/fleet', '/api/trucks')
  await page.getByRole('button', { name: 'Add truck' }).click()
  await page.getByLabel('Plate').fill(plate)
  await page.getByLabel('Make').fill('Kenworth')
  await page.getByLabel('Model').fill('T680')
  await page.getByLabel('Unit no.').fill(`UNIT-${uniqueSuffix()}`)
  await submitCreate(page, 'Create truck', '/api/trucks')

  await expect(page.getByText(plate, { exact: true })).toBeVisible()
})

test('a guardian sees Fleet read-only: no create button, existing truck fields disabled', async ({
  page,
}) => {
  const suffix = uniqueSuffix()
  const plate = `IT-${suffix}`
  const guardianEmail = `guardian-${suffix}@argus.dev`
  const guardianPassword = 'guardian-pass-123'

  await page.goto('/login')
  await login(page, ROOT_ADMIN_EMAIL, ROOT_ADMIN_PASSWORD)

  // Seed a truck this guardian will view.
  await gotoLoadedList(page, '/fleet', '/api/trucks')
  await page.getByRole('button', { name: 'Add truck' }).click()
  await page.getByLabel('Plate').fill(plate)
  await page.getByLabel('Make').fill('Kenworth')
  await page.getByLabel('Model').fill('T680')
  await page.getByLabel('Unit no.').fill(`UNIT-${suffix}`)
  await submitCreate(page, 'Create truck', '/api/trucks')
  await expect(page.getByText(plate, { exact: true })).toBeVisible()

  // Seed the guardian account itself via Access.
  await gotoLoadedList(page, '/access', '/api/users')
  await page.getByRole('button', { name: 'Add user' }).click()
  await page.getByLabel('First name').fill('Gary')
  await page.getByLabel('Last name').fill('Guardian')
  await page.getByLabel('Email').fill(guardianEmail)
  await page.getByLabel('Phone').fill('5559876543')
  await page.getByLabel('Role').selectOption('guardian')
  await page.getByLabel('Password').fill(guardianPassword)
  await submitCreate(page, 'Create user', '/api/users')
  await expect(page.getByText(guardianEmail, { exact: true })).toBeVisible()

  await logout(page)
  await login(page, guardianEmail, guardianPassword)

  await gotoLoadedList(page, '/fleet', '/api/trucks')
  await expect(page.getByRole('button', { name: 'Add truck' })).toHaveCount(0)

  await page.getByText(plate, { exact: true }).click()
  await expect(page.getByText('View truck')).toBeVisible()
  await expect(page.getByLabel('Plate')).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Save changes' })).toHaveCount(0)
  // Two buttons share the accessible name "Close" — the panel's icon close button
  // (aria-label="Close") and the text button replacing "Cancel" in read-only mode; `.last()`
  // is the latter, which comes after in the DOM.
  await expect(page.getByRole('button', { name: 'Close' }).last()).toBeVisible()
})
