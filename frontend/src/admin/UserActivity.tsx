import { Link, useOutletContext, useParams } from 'react-router'
import ActivityHistory from '../activity/ActivityHistory'
import type { AdminContext } from './AdminLayout'

export default function UserActivity() {
  const { userId } = useParams()
  const { leave } = useOutletContext<AdminContext>()
  const id = /^[1-9][0-9]{0,9}$/.test(userId ?? '') && Number(userId) <= 2147483647 ? Number(userId) : null
  return <div className="admin-page">
    <Link to="/admin">Back to users</Link>
    <h1>User activity{ id ? ` · account #${id}` : ''}</h1>
    {id ? <ActivityHistory userId={id} onDenied={leave} /> : <p role="alert">User not found.</p>}
  </div>
}
