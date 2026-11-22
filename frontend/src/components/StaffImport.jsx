import React, { useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Download, FileUp, KeyRound, Upload } from "lucide-react";
import { authFetch } from "../utils/api.js";
import { buildCredentialsCsv } from "../utils/staffImport.js";

async function readError(res) {
  try {
    const body = await res.json();
    if (body && body.error) return body.error;
  } catch {
    // not JSON - use the generic message below
  }
  return `Server responded ${res.status}`;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export default function StaffImport() {
  const fileInput = useRef(null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null); // dry-run result
  const [outcome, setOutcome] = useState(null); // real import result

  const send = async (selectedFile, dryRun) => {
    const form = new FormData();
    form.append("file", selectedFile);
    form.append("dry_run", dryRun ? "true" : "false");
    const res = await authFetch("/auth/users/import", { method: "POST", body: form });
    if (!res.ok) throw new Error(await readError(res));
    return res.json();
  };

  const handleFile = async (event) => {
    const selected = event.target.files?.[0] || null;
    setFile(selected);
    setPreview(null);
    setOutcome(null);
    setError(null);
    if (!selected) return;

    setBusy(true);
    try {
      setPreview(await send(selected, true));
    } catch (err) {
      setError(err.message || "Could not check the file");
    } finally {
      setBusy(false);
    }
  };

  const runImport = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      setOutcome(await send(file, false));
      setPreview(null);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    } catch (err) {
      setError(err.message || "Import failed");
    } finally {
      setBusy(false);
    }
  };

  const downloadTemplate = async () => {
    setError(null);
    try {
      const res = await authFetch("/auth/users/import/template");
      if (!res.ok) throw new Error(await readError(res));
      downloadBlob(await res.blob(), "staff_import_template.csv");
    } catch (err) {
      setError(err.message || "Could not download the template");
    }
  };

  const downloadCredentials = () => {
    downloadBlob(new Blob([buildCredentialsCsv(outcome.results)], { type: "text/csv;charset=utf-8;" }), "new_staff_credentials.csv");
  };

  const problemRows = (preview || outcome)?.results.filter((row) => row.status === "error") || [];
  const withPasswords = outcome ? outcome.results.filter((row) => row.temporary_password).length : 0;

  return (
    <section className="px-7 pb-10 max-[1200px]:px-6">
      <div className="mx-auto max-w-[1000px] rounded-[28px] border border-slate-200 bg-white p-8 max-[860px]:p-6 max-[640px]:p-5">
        <div className="mb-6 flex flex-col justify-between gap-4 md:flex-row md:items-start">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.14em] text-[#6b7f9e]">Bulk import</p>
            <h2 className="mb-0 mt-1.5 text-[1.8rem] text-[#11233f]">Import staff from a CSV file</h2>
            <p className="m-0 mt-2 max-w-[560px] text-sm text-slate-500">
              Required columns: staff_id, full_name, email. Optional: phone_number, department, role (staff or admin) and password. Rows with a blank
              password get a temporary one. Up to 500 rows per file.
            </p>
          </div>
          <button
            type="button"
            onClick={downloadTemplate}
            className="inline-flex w-fit items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50"
          >
            <Download size={16} />
            Download template
          </button>
        </div>

        <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500 hover:border-blue-300">
          <FileUp size={26} className="text-blue-600" />
          <span className="font-semibold text-[#11233f]">{file ? file.name : "Choose a CSV file"}</span>
          <span>The file is checked first - nothing is created until you confirm.</span>
          <input ref={fileInput} type="file" accept=".csv,text/csv" onChange={handleFile} className="sr-only" aria-label="CSV file" />
        </label>

        {busy && <p className="mt-4 text-sm text-slate-500">Working…</p>}

        {error && (
          <div role="alert" className="mt-4 flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
            <AlertCircle size={18} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {preview && (
          <div className="mt-5">
            <p className="m-0 text-sm font-semibold text-[#11233f]">
              {preview.valid} ready to import · {preview.skipped} with problems · {preview.total_rows} rows checked
            </p>
            <button
              type="button"
              onClick={runImport}
              disabled={busy || preview.valid === 0}
              className="mt-3 inline-flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Upload size={16} />
              Import {preview.valid} staff
            </button>
            {preview.skipped > 0 && <p className="m-0 mt-2 text-xs text-slate-500">Rows with problems will be skipped.</p>}
          </div>
        )}

        {outcome && (
          <div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-900">
            <p className="m-0 flex items-center gap-2 font-semibold">
              <CheckCircle2 size={18} />
              {outcome.created} staff account{outcome.created === 1 ? "" : "s"} created
              {outcome.skipped > 0 ? ` · ${outcome.skipped} skipped` : ""}
            </p>
            {withPasswords > 0 && (
              <div className="mt-3">
                <p className="m-0 flex items-start gap-2 text-emerald-900">
                  <KeyRound size={16} className="mt-0.5 shrink-0" />
                  {withPasswords} account{withPasswords === 1 ? " has" : "s have"} a temporary password. Download it now - it will not be shown again.
                </p>
                <button
                  type="button"
                  onClick={downloadCredentials}
                  className="mt-3 inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-emerald-800"
                >
                  <Download size={16} />
                  Download credentials CSV
                </button>
              </div>
            )}
          </div>
        )}

        {problemRows.length > 0 && (
          <div className="mt-5 overflow-hidden rounded-2xl border border-slate-200">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-[0.08em] text-slate-500">
                <tr>
                  <th className="px-4 py-3">Row</th>
                  <th className="px-4 py-3">Staff ID</th>
                  <th className="px-4 py-3">Problem</th>
                </tr>
              </thead>
              <tbody>
                {problemRows.map((row) => (
                  <tr key={row.row} className="border-t border-slate-100">
                    <td className="px-4 py-3 text-slate-600">{row.row}</td>
                    <td className="px-4 py-3 font-semibold text-[#11233f]">{row.staff_id || "—"}</td>
                    <td className="px-4 py-3 text-rose-700">{row.errors.join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
