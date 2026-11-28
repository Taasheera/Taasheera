import { expect, test } from '@playwright/test'

// CMPS271-16: the home page, FAQ and header switch to Arabic, right to left, and the choice lasts.
test('the language button switches to Arabic, right to left, and remembers the choice', async ({ page }) => {
  await page.goto('/')
  const html = page.locator('html')
  await expect(html).toHaveAttribute('dir', 'ltr')

  await page.getByRole('button', { name: 'العربية' }).click()
  await expect(html).toHaveAttribute('lang', 'ar')
  await expect(html).toHaveAttribute('dir', 'rtl')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('أوراق التأشيرة')

  // Remembered after a reload and on the FAQ.
  await page.reload()
  await expect(html).toHaveAttribute('dir', 'rtl')
  await page.goto('/faq')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('الأسئلة الشائعة')

  // A page that is not translated yet stays English, left to right.
  await page.goto('/visas')
  await expect(html).toHaveAttribute('dir', 'ltr')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('What each visa needs')

  await page.goto('/')
  await page.getByRole('button', { name: 'English' }).click()
  await expect(html).toHaveAttribute('dir', 'ltr')
  await page.reload()
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Visa paperwork')
})
