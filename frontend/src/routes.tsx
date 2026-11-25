import type { RouteObject } from 'react-router'
import AuthRoute from './AuthRoute'
import AdminLayout from './admin/AdminLayout'
import AdminUser from './admin/AdminUser'
import AdminUsers from './admin/AdminUsers'
import { readNavigation } from './navigation'
import Layout from './Layout'
import Faq from './pages/Faq'
import Feature from './Feature'
import VisaGuidePage from './visaGuides/VisaGuidePage'
import VisaGuides from './visaGuides/VisaGuides'
import Home from './pages/Home'
import Passport from './pages/Passport'
import Apply from './pages/Apply'
import NotFound from './pages/NotFound'
import Dashboard from './dashboard/Dashboard'
import ApplicationDetail from './dashboard/ApplicationDetail'
import ServerError from './pages/ServerError'
import AdminDashboard from './pages/AdminDashboard'

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
          { path: 'passport', element: <Passport /> },
          { path: 'passport/:uploadId', element: <Passport /> },
          // Not /applications: that prefix is the API, proxied to the backend.
          { path: 'apply', element: <Apply /> },
          { path: 'apply/:applicationId', element: <Apply /> },
          // Every application and one application's timeline (US 19, US 20). Same reason as above.
          { path: 'dashboard', element: <Dashboard /> },
          { path: 'dashboard/:applicationId', element: <ApplicationDetail /> },
          // The account page uses the same header, sections and assistant as every signed-in page.
          { path: 'account', loader: () => readNavigation(window), element: <AuthRoute accountPage embedded /> },
          { path: '*', element: <NotFound /> },
        ],
      },
    ],
  },
  // Every /admin page, including unknown ones, sits behind the admin layout's access check.
  {
    path: 'admin',
    element: <AdminLayout />,
    errorElement: <ServerError standalone />,
    children: [
      { index: true, element: <AdminUsers /> },
      { path: 'users/:userId', element: <AdminUser /> },
      { path: '*', element: <NotFound /> },
    ],
  },
  { path: 'sign-in', loader: () => readNavigation(window), element: <AuthRoute /> },
  { path: 'backoffice', element: <AdminDashboard />, errorElement: <ServerError standalone /> },
  { path: 'reset-password', loader: () => readNavigation(window), element: <AuthRoute /> },
]
