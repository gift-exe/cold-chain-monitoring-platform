// Administration (FR11, Table 3.21): national officers configure products; administrators manage phone
// numbers and watch the system. Every change is recorded in the audit trail.
import { useState } from "react";

import { api, useApi } from "../api.js";
import { ago, label, ROLES, time } from "../format.js";

export default function Admin({ me }) {
  if (me.role === "national_officer") return <div className="page"><h1>Products</h1><Products /></div>;
  if (me.role === "admin") {
    return (
      <div className="page">
        <h1>Administration</h1>
        <section className="panel"><h2>System status</h2><SystemStatus /></section>
        <section className="panel"><h2>Users and phone numbers</h2><Users /></section>
      </div>
    );
  }
  return <div className="page"><p className="muted">Administration is for national officers and administrators.</p></div>;
}

const FIELDS = [
  ["lower_limit", "Lower limit (°C)", 0.1],
  ["upper_limit", "Upper limit (°C)", 0.1],
  ["freeze_limit", "Freezing limit (°C)", 0.1],
  ["heat_tolerance_min", "Heat tolerance (min)", 1],
  ["freeze_tolerance_min", "Freeze tolerance (min)", 1],
];

function Products() {
  const { data, error, reload } = useApi("/products");
  const [edit, setEdit] = useState(null);
  const [msg, setMsg] = useState(null);
  const save = async () => {
    try {
      const body = Object.fromEntries(FIELDS.map(([k]) => [k, Number(edit[k])]));
      await api(`/products/${edit.id}`, { method: "PATCH", body });
      setMsg({ ok: true, text: `${edit.name} updated. Detection uses the new values from now on.` });
      setEdit(null);
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <p className="hint">Tolerances are how long a reading may stay out of limits before an incident is raised.
        For stores, detection applies the strictest values across all products.</p>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Product</th>{FIELDS.map(([, t]) => <th key={t} className="num">{t}</th>)}<th></th></tr></thead>
        <tbody>
          {data.map((p) => edit?.id === p.id ? (
            <tr key={p.id} className="editing">
              <th scope="row">{p.name}</th>
              {FIELDS.map(([k, t, step]) => (
                <td key={k}><input type="number" step={step} aria-label={`${t} for ${p.name}`} value={edit[k]}
                                   onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></td>
              ))}
              <td><button className="btn primary" onClick={save}>Save</button> <button className="btn" onClick={() => setEdit(null)}>Cancel</button></td>
            </tr>
          ) : (
            <tr key={p.id}>
              <th scope="row">{p.name}</th>
              {FIELDS.map(([k]) => <td key={k} className="num">{Number(p[k])}</td>)}
              <td><button className="btn" onClick={() => { setEdit({ ...p }); setMsg(null); }}>Edit</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
    </>
  );
}

function Users() {
  const { data, error, reload } = useApi("/users");
  const [msg, setMsg] = useState(null);
  const [numbers, setNumbers] = useState({});
  const run = async (u, path, method, body, text) => {
    if (path === "deactivate" && !window.confirm(`Deactivate ${u.full_name}'s number ${u.phone_number}? Every SMS command from it will be refused.`)) return;
    try {
      await api(`/users/${u.id}/phone${path === "deactivate" ? "/deactivate" : ""}`, { method, body });
      setNumbers({ ...numbers, [u.id]: "" });
      setMsg({ ok: true, text });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Name</th><th>Role</th><th>Area</th><th>Phone</th><th>New number</th><th></th></tr></thead>
        <tbody>
          {data.map((u) => (
            <tr key={u.id}>
              <th scope="row">{u.full_name}<div className="muted">{u.username}</div></th>
              <td>{label(ROLES, u.role)}</td>
              <td>{u.facility_name || u.jurisdiction_name}</td>
              <td>{u.phone_number || "—"}{u.phone_deactivated_at && <div className="flag flag-bad">Deactivated {time(u.phone_deactivated_at)}</div>}</td>
              <td><input aria-label={`New number for ${u.full_name}`} placeholder="+234…" value={numbers[u.id] || ""}
                         onChange={(e) => setNumbers({ ...numbers, [u.id]: e.target.value })} /></td>
              <td className="btn-row">
                {u.phone_number && !u.phone_deactivated_at && (
                  <button className="btn" onClick={() => run(u, "deactivate", "POST", undefined, `${u.full_name}'s number deactivated.`)}>Deactivate number</button>
                )}
                <button className="btn" disabled={!numbers[u.id]} onClick={() => run(u, "set", "PUT", { phone_number: numbers[u.id] }, `New number registered for ${u.full_name}.`)}>Set number</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
    </>
  );
}

export function SystemStatus() {
  const { data: s, error } = useApi("/system/status", ["device_status", "notification", "incident"], 30000);
  if (error) return <p className="error">{error.message}</p>;
  if (!s) return <p className="muted">Loading…</p>;
  const tiles = [
    ["Loggers reporting", `${s.devices_reporting} of ${s.devices_active}`, s.devices_reporting < s.devices_active],
    ["Last reading received", ago(s.last_reading_received_at), false],
    ["Open incidents", s.open_incidents, false],
    ["SMS waiting to send", s.sms_waiting, s.sms_waiting > 10],
    ["SMS undelivered (24 h)", s.sms_undelivered_24h, s.sms_undelivered_24h > 0],
    ["Event-log messages unsent", s.outbox_unsent, s.outbox_unsent > 0],
    ["Malformed messages (24 h)", s.rejected_messages_24h, false],
  ];
  return (
    <div className="tiles">
      {tiles.map(([t, v, warn]) => (
        <div key={t} className={`tile ${warn ? "tile-warn" : ""}`}><div className="tile-v">{v}</div><div className="tile-t">{t}</div></div>
      ))}
    </div>
  );
}
