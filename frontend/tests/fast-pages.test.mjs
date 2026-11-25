import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { test } from 'node:test'
import './dom-setup.mjs'

// Record every module the first page needs, the same graph Vite puts in the main file (CMPS271-137).
const loaded = new Set()
registerHooks({
  load(url, context, nextLoad) {
    loaded.add(url)
    if (url.endsWith('.svg')) return { format: 'module', source: `export default ${JSON.stringify(url)}`, shortCircuit: true }
    return nextLoad(url, context)
  },
})

const { routes } = await import('../src/routes.tsx')
const firstPage = new Set(loaded)

// Signed-in, sign-in and staff pages, which a first-time visitor on a slow connection does not need.
const later = {
  passport: 'src/pages/Passport.tsx',
  'passport/:uploadId': 'src/pages/Passport.tsx',
  apply: 'src/pages/Apply.tsx',
  'apply/:applicationId': 'src/pages/Apply.tsx',
  dashboard: 'src/dashboard/Dashboard.tsx',
  'dashboard/:applicationId': 'src/dashboard/ApplicationDetail.tsx',
  account: 'src/AuthRoute.tsx',
  'sign-in': 'src/AuthRoute.tsx',
  'reset-password': 'src/AuthRoute.tsx',
  admin: 'src/admin/AdminLayout.tsx',
  'users/:userId': 'src/admin/AdminUser.tsx',
  backoffice: 'src/pages/AdminDashboard.tsx',
}

function byPath(list, found = new Map()) {
  for (const route of list) {
    if (route.path) found.set(route.path, route)
    if (route.children) byPath(route.children, found)
  }
  return found
}
const routesByPath = byPath(routes)
const wasLoaded = (set, file) => [...set].some(url => url.endsWith('/' + file))

test('the first page does not download signed-in or staff pages', () => {
  for (const [path, file] of Object.entries(later)) {
    const route = routesByPath.get(path)
    assert.equal(typeof route?.lazy, 'function', path)
    assert.equal(route.element, undefined, path)
    assert.equal(wasLoaded(firstPage, file), false, `${file} is in the first download`)
  }
  assert.equal(wasLoaded(firstPage, 'src/admin/AdminUsers.tsx'), false)
})

test('public first pages are ready without a second download', () => {
  assert.ok(routes[0].children[0].children.find(route => route.index).element)
  for (const path of ['faq', 'visas', 'visas/:guideId']) assert.ok(routesByPath.get(path).element, path)
})

test('a page downloads when opened and keeps its settings', async () => {
  const apply = await routesByPath.get('apply').lazy()
  assert.ok(apply.element)
  assert.ok(wasLoaded(loaded, 'src/pages/Apply.tsx'))

  const account = await routesByPath.get('account').lazy()
  assert.deepEqual(account.element.props, { accountPage: true, embedded: true })
  assert.deepEqual((await routesByPath.get('sign-in').lazy()).element.props, {})
})
