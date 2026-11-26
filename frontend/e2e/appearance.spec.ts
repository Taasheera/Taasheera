import { expect, test } from '@playwright/test'

test('the theme button follows the device until pressed, then remembers the choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  const background = page.locator('html')
  await expect(background).toHaveCSS('background-color', 'rgb(20, 20, 21)')

  // One press switches straight to the other theme: no menu to open.
  await page.getByRole('button', { name: 'Switch to light mode' }).click()
  await expect(background).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await page.goto('/sign-in')
  await expect(page.getByRole('button', { name: 'Switch to dark mode' })).toBeVisible()
  await expect(background).toHaveCSS('background-color', 'rgb(255, 255, 255)')

  // The saved choice wins over the device setting, including after a reload.
  await page.emulateMedia({ colorScheme: 'light' })
  await page.getByRole('button', { name: 'Switch to dark mode' }).click()
  await page.reload()
  await expect(background).toHaveCSS('background-color', 'rgb(20, 20, 21)')
  await expect(page.getByRole('button', { name: 'Switch to light mode' })).toBeVisible()
})
