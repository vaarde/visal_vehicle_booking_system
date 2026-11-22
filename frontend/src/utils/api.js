import { API_BASE_URL } from '../config.js';
import { clearSession } from './session.js';

/**
 * fetch() for endpoints that need a signed-in user. Takes a path such as "/bookings/pending",
 * adds the Bearer token, and - if the server says the session is no longer valid (expired,
 * logged out, or the account was deactivated) - clears the stored session and returns to the login page.
 */
export async function authFetch(path, options = {}) {
  const token = localStorage.getItem('access_token');
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (response.status === 401 && token) {
    clearSession();
    window.location.assign('/');
  }
  return response;
}
