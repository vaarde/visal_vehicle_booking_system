import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import StaffImport from '../components/StaffImport';
import { buildCredentialsCsv } from '../utils/staffImport.js';
import { authFetch } from '../utils/api.js';

vi.mock('../utils/api.js', () => ({ authFetch: vi.fn() }));

const json = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

const PREVIEW = {
  dry_run: true,
  total_rows: 3,
  valid: 2,
  created: 0,
  skipped: 1,
  results: [
    { row: 2, staff_id: 'N1', full_name: 'Ama', email: 'ama@example.com', status: 'valid', errors: [] },
    { row: 3, staff_id: 'N2', full_name: '', email: 'bad', status: 'error', errors: ['full_name is required', 'email is not a valid address'] },
    { row: 4, staff_id: 'N3', full_name: 'Kofi', email: 'kofi@example.com', status: 'valid', errors: [] },
  ],
};

const OUTCOME = {
  dry_run: false,
  total_rows: 3,
  valid: 2,
  created: 2,
  skipped: 1,
  results: [
    { row: 2, staff_id: 'N1', full_name: 'Ama', email: 'ama@example.com', status: 'created', errors: [], temporary_password: 'Temp-pass-1' },
    PREVIEW.results[1],
    { row: 4, staff_id: 'N3', full_name: 'Kofi', email: 'kofi@example.com', status: 'created', errors: [] },
  ],
};

const chooseFile = () => {
  const file = new File(['staff_id,full_name,email\n'], 'staff.csv', { type: 'text/csv' });
  fireEvent.change(screen.getByLabelText('CSV file'), { target: { files: [file] } });
};

describe('StaffImport', () => {
  beforeEach(() => {
    authFetch.mockReset();
  });

  it('checks the file first and lists problem rows with their reasons', async () => {
    authFetch.mockImplementation(() => json(PREVIEW));
    render(<StaffImport />);
    chooseFile();

    expect(await screen.findByText(/2 ready to import · 1 with problems · 3 rows checked/)).toBeInTheDocument();
    expect(screen.getByText('full_name is required; email is not a valid address')).toBeInTheDocument();

    const [, options] = authFetch.mock.calls[0];
    expect(options.method).toBe('POST');
    expect(options.body.get('dry_run')).toBe('true'); // nothing is created yet
    expect(screen.getByRole('button', { name: /Import 2 staff/ })).toBeEnabled();
  });

  it('imports on confirmation and offers the temporary passwords once', async () => {
    authFetch.mockImplementationOnce(() => json(PREVIEW)).mockImplementationOnce(() => json(OUTCOME));
    render(<StaffImport />);
    chooseFile();

    fireEvent.click(await screen.findByRole('button', { name: /Import 2 staff/ }));

    expect(await screen.findByText(/2 staff accounts created · 1 skipped/)).toBeInTheDocument();
    expect(screen.getByText(/1 account has a temporary password/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Download credentials CSV/ })).toBeInTheDocument();
    expect(authFetch.mock.calls[1][1].body.get('dry_run')).toBe('false');
  });

  it('disables the import button when no row is valid', async () => {
    authFetch.mockImplementation(() => json({ ...PREVIEW, valid: 0, skipped: 3, total_rows: 3 }));
    render(<StaffImport />);
    chooseFile();

    expect(await screen.findByRole('button', { name: /Import 0 staff/ })).toBeDisabled();
  });

  it('shows the server message when the file is rejected', async () => {
    authFetch.mockImplementation(() => json({ error: 'Missing required column(s): email' }, 400));
    render(<StaffImport />);
    chooseFile();

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Missing required column(s): email'));
  });
});

describe('buildCredentialsCsv', () => {
  it('lists only accounts that got a temporary password', () => {
    const csv = buildCredentialsCsv(OUTCOME.results);
    expect(csv.split('\n')).toHaveLength(2); // header + Ama
    expect(csv).toContain('"Temp-pass-1"');
  });

  it('neutralises spreadsheet formulas in names', () => {
    const csv = buildCredentialsCsv([{ staff_id: 'N9', full_name: '=HYPERLINK("x")', email: 'a@b.co', temporary_password: 'pw' }]);
    expect(csv).toContain(`"'=HYPERLINK(""x"")"`);
  });
});
