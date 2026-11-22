// A cell starting with = + - or @ would run as a formula when the file is opened in a spreadsheet.
function csvCell(value) {
  const text = String(value ?? "");
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
}

export function buildCredentialsCsv(results) {
  const rows = results.filter((row) => row.temporary_password);
  const lines = [
    ["Staff ID", "Full Name", "Email", "Temporary Password"],
    ...rows.map((r) => [r.staff_id, r.full_name, r.email, r.temporary_password]),
  ];
  return lines.map((line) => line.map(csvCell).join(",")).join("\n");
}
