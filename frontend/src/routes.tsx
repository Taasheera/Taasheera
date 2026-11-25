import type { ComponentType } from 'react'
import type { RouteObject } from 'react-router'
import { readNavigation } from './navigation'
import Layout from './Layout'
import Faq from './pages/Faq'
import Feature from './Feature'
import VisaGuidePage from './visaGuides/VisaGuidePage'
import VisaGuides from './visaGuides/VisaGuides'
import Home from './pages/Home'
import NotFound from './pages/NotFound'
import ServerError from './pages/ServerError'

// Signed-in, sign-in and staff pages download when first opened, so a visitor's first page
// (home, FAQ, visa guides) loads less code on a slow connection (CMPS271-137).
function later<P extends object>(load: () => Promise<{ default: ComponentType<P> }>, props = {} as P) {
  return async () => {
    const { default: Page } = await load()
    return { element: <Page {...props} /> }
  }
}

// The root errorElement catches a crash in the layout itself; the pathless
// child catches page crashes so the header and footer stay on screen.
export const routes: RouteObject[] = [
  {
    element: <Layout />,
    errorElement: <ServerError standalone />,
    children: [
      {
        errorElement: <ServerError />,
        children: [
          { index: true, element: <Home /> },
          { path: 'faq', element: <Faq /> },
          // Not /visa-guides: that prefix is the API. Behind visa_guide; unknown while the flag is off.
          { path: 'visas', element: <Feature name="visa_guide" fallback={<NotFound />}><VisaGuides /></Feature> },
          { path: 'visas/:guideId', element: <Feature name="visa_guide" fallback={<NotFound />}><VisaGuidePage /></Feature> },
          { path: 'passport', lazy: later(() => import('./pages/Passport')) },
          { path: 'passport/:uploadId', lazy: later(() => import('./pages/Passport')) },
          // Not /applications: that prefix is the API, proxied to the backend.
          { path: 'apply', lazy: later(() => import('./pages/Apply')) },
          { path: 'apply/:applicationId', lazy: later(() => import('./pages/Apply')) },
          // Every application and one application's timeline (US 19, US 20). Same reason as above.
          { path: 'dashboard', lazy: later(() => import('./dashboard/Dashboard')) },
          { path: 'dashboard/:applicationId', lazy: later(() => import('./dashboard/ApplicationDetail')) },
          // The account page uses the same header, sections and assistant as every signed-in page.
          { path: 'account', loader: () => readNavigation(window), lazy: later(() => import('./AuthRoute'), { accountPage: true, embedded: true }) },
          { path: '*', element: <NotFound /> },
        ],
      },
    ],
  },
  // Every /admin page, including unknown ones, sits behind the admin layout's access check.
  {
    path: 'admin',
    lazy: later(() => import('./admin/AdminLayout')),
    errorElement: <ServerError standalone />,
    children: [
      { index: true, lazy: later(() => import('./admin/AdminUsers')) },
      { path: 'users/:userId', lazy: later(() => import('./admin/AdminUser')) },
      { path: '*', element: <NotFound /> },
    ],
  },
  { path: 'sign-in', loader: () => readNavigation(window), lazy: later(() => import('./AuthRoute')) },
  { path: 'backoffice', lazy: later(() => import('./pages/AdminDashboard')), errorElement: <ServerError standalone /> },
  { path: 'reset-password', loader: () => readNavigation(window), lazy: later(() => import('./AuthRoute')) },
]
