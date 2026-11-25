import { expect, test } from '@playwright/test'

test('appearance follows the device and remembers an explicit choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/')
  const appearance = page.getByRole('combobox', { name: 'Appearance' })
  const background = page.locator('html')
  await expect(appearance).toHaveValue('system')
  await expect(background).toHaveCSS('background-color', 'rgb(20, 20, 21)')

  await appearance.selectOption('light')
  await expect(background).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await page.goto('/sign-in')
  await expect(appearance).toHaveValue('light')
  await expect(background).toHaveCSS('background-color', 'rgb(255, 255, 255)')

  await page.emulateMedia({ colorScheme: 'light' })
  await appearance.selectOption('dark')
  await page.reload()
  await expect(appearance).toHaveValue('dark')
  await expect(background).toHaveCSS('background-color', 'rgb(20, 20, 21)')

  await appearance.selectOption('system')
  await expect(background).toHaveCSS('background-color', 'rgb(255, 255, 255)')
})
