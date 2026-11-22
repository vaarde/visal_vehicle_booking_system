import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Reports from '../pages/admin/Reports';
import { authFetch } from '../utils/api.js';

vi.mock('../utils/api.js', () => ({ authFetch: vi.fn() }));

// recharts measures its container with ResizeObserver, which jsdom does not provide
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const SUMMARY = {
  totals: {
    total: 4,
    approved: 1,
    pending: 1,
    declined: 1,
    cancelled: 1,
    total_hours: 5.5,
    average_duration_hours: 1.38,
    approval_rate: 25,
    decline_rate: 25,
    unique_staff: 2,
    bookings_per_staff: 2,
    backlog_hours: 1,
  },
  top_location: { name: 'Tema', count: 3 },
  peak_hour: { hour: 9, count: 3 },
  by_status: [
    { status: 'Approved', value: 1 },
    { status: 'Pending', value: 1 },
    { status: 'Declined', value: 1 },
    { status: 'Cancelled', value: 1 },
  ],
  daily_hours: [{ date: '2030-01-07', hours: 2 }],
  by_purpose: [{ name: 'Client visit', value: 2 }],
  by_department: [{ name: 'IT', value: 2 }],
  hourly_demand: [{ hour: 9, count: 3 }],
  departments: ['Finance', 'IT'],
};

const ROWS = {
  total: 1,
  page: 1,
  page_size: 10,
  total_pages: 1,
  bookings: [
    {
      booking_id: 7,
      staff_name: 'Ama Mensah',
      department: 'Finance',
      booking_date: '2030-01-07',
      start_time: '09:00',
      end_time: '11:00',
      duration_hours: 2,
      location: 'Tema',
      purpose: 'Client visit',
      status: 'Approved',
    },
  ],
};

const json = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

function mockServer() {
  authFetch.mockImplementation((path) => {
    if (path.startsWith('/reports/summary')) return json(SUMMARY);
    if (path.startsWith('/reports/bookings')) return json(ROWS);
    return json({}, 404);
  });
}

const calledPaths = () => authFetch.mock.calls.map(([path]) => path);

describe('Reports page', () => {
  beforeEach(() => {
    authFetch.mockReset();
  });

  it('shows the figures and table rows computed by the server', async () => {
    mockServer();
    render(<Reports />);

    expect(await screen.findByText('Ama Mensah')).toBeInTheDocument();
    expect(screen.getByText('Total bookings')).toBeInTheDocument();
    expect(screen.getAllByText('25%').length).toBeGreaterThan(0); // approval and decline rate
    expect(screen.getAllByText('Tema').length).toBeGreaterThan(0); // top location and the row
    expect(screen.getByText('9:00 AM')).toBeInTheDocument(); // peak hour label built from hour 9
    expect(screen.getByText('Cancelled', { selector: 'option' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Finance' })).toBeInTheDocument(); // department filter from the server
  });

  it('asks the server to filter when a filter changes, and goes back to page 1', async () => {
    mockServer();
    render(<Reports />);
    await screen.findByText('Ama Mensah');

    fireEvent.change(screen.getByDisplayValue('All statuses'), { target: { value: 'declined' } });

    await waitFor(() => expect(calledPaths().some((p) => p.startsWith('/reports/summary') && p.includes('status=Declined'))).toBe(true));
    expect(calledPaths().some((p) => p.startsWith('/reports/bookings') && p.includes('status=Declined') && p.includes('page=1'))).toBe(true);
  });

  it('shows an error instead of made-up sample data when the server fails', async () => {
    authFetch.mockImplementation(() => json({ error: 'Only admins can view reports' }, 403));
    render(<Reports />);

    expect(await screen.findByText(/Live reports could not load: Only admins can view reports/)).toBeInTheDocument();
    expect(screen.queryByText('John Doe')).not.toBeInTheDocument();
    expect(screen.queryByText('Jane Smith')).not.toBeInTheDocument();
  });
});
