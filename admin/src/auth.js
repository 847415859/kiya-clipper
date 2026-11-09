import { reactive } from 'vue'
import { api } from './api'

export const auth = reactive({ checked: false, authenticated: false, username: '' })

export async function ensureAuth() {
  if (!auth.checked) {
    try {
      const me = await api.me()
      auth.authenticated = !!me.authenticated
      auth.username = me.username || ''
    } catch (e) {
      auth.authenticated = false
    }
    auth.checked = true
  }
  return auth.authenticated
}
