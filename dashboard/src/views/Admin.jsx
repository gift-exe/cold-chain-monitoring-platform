// Administration (FR11, Table 3.21). Administrators manage users, jurisdictions, facilities, devices and
// phone numbers, and watch the system; national officers configure products and the escalation policy.
// Every change is recorded in the audit trail (FR11.2).
import { useState } from "react";

import { api, useApi } from "../api.js";
import { ago, label, ROLES, time } from "../format.js";

function Tabbed({ title, tabs }) {
  const [on, setOn] = useState(tabs[0][0]);
  const Current = tabs.find(([k]) => k === on)[2];
  return (
    <div className="page">
      <h1>{title}</h1>
      <div className="tabs" role="tablist">
        {tabs.map(([k, t]) => (
          <button key={k} role="tab" aria-selected={on === k} className={on === k ? "on" : ""} onClick={() => setOn(k)}>{t}</button>
        ))}
      </div>
      <section className="panel"><Current /></section>
    </div>
  );
}

export default function Admin({ me }) {
  if (me.role === "national_officer") {
    return <Tabbed title="Configuration" tabs={[["products", "Products", Products], ["policy", "Escalation policy", Policy]]} />;
  }
  if (me.role === "admin") {
    return <Tabbed title="Administration" tabs={[
      ["status", "System status", SystemStatus], ["users", "Users and phone numbers", Users],
      ["jurisdictions", "Jurisdictions", Jurisdictions], ["facilities", "Facilities", Facilities],
      ["devices", "Devices", Devices]]} />;
  }
  return <div className="page"><p className="muted">Administration is for national officers and administrators.</p></div>;
}

// A small form whose fields are [key, label, type, options]; options are [value, text] pairs for a select.
function Form({ fields, initial = {}, submit, button, onDone }) {
  const [v, setV] = useState(initial);
  const [msg, setMsg] = useState(null);
  const go = async (e) => {
    e.preventDefault();
    try {
      const text = await submit(v);
      setMsg({ ok: true, text });
      setV(initial);
      onDone?.();
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    }
  };
  return (
    <form className="form" onSubmit={go}>
      {fields.map(([k, t, type = "text", options]) => (
        <label key={k} className="field"><span>{t}</span>
          {options ? (
            <select value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value || null })}>
              <option value="">—</option>
              {options.map(([o, ot]) => <option key={o} value={o}>{ot}</option>)}
            </select>
          ) : type === "checkbox" ? (
            <input type="checkbox" checked={!!v[k]} onChange={(e) => setV({ ...v, [k]: e.target.checked })} />
          ) : (
            <input type={type} step="any" value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
          )}
        </label>
      ))}
      <button className="btn primary" type="submit">{button}</button>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
    </form>
  );
}

function useLists() {
  const j = useApi("/jurisdictions");
  const f = useApi("/facilities");
  return {
    jurisdictions: (j.data || []).map((x) => [x.id, `${x.name} (${x.id})`]),
    facilities: (f.data || []).map((x) => [x.id, `${x.name} · ${x.jurisdiction_id}`]),
    reload: () => { j.reload(); f.reload(); },
    raw: { jurisdictions: j.data || [], facilities: f.data || [] },
  };
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
  const lists = useLists();
  const [msg, setMsg] = useState(null);
  const [numbers, setNumbers] = useState({});
  const [edit, setEdit] = useState(null);
  const roles = Object.entries(ROLES);
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
  const change = async (u, body, text) => {
    if (body.active === false && !window.confirm(`Deactivate ${u.full_name}? They will no longer receive alerts or be able to act.`)) return;
    try {
      await api(`/users/${u.id}`, { method: "PATCH", body });
      setMsg({ ok: true, text });
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
      <p className="hint">A user's role and area decide what they see and which incidents reach them. Dashboard
        sign-in accounts are created in the identity server; people who only use SMS need none.</p>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Name</th><th>Role</th><th>Area</th><th>Phone</th><th>New number</th><th></th></tr></thead>
        <tbody>
          {data.map((u) => edit?.id === u.id ? (
            <tr key={u.id} className="editing">
              <th scope="row">{u.full_name}<div className="muted">{u.username}</div></th>
              <td><select aria-label="Role" value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })}>
                {roles.map(([r, t]) => <option key={r} value={r}>{t}</option>)}</select></td>
              <td colSpan={3}>
                <select aria-label="Jurisdiction" value={edit.jurisdiction_id} onChange={(e) => setEdit({ ...edit, jurisdiction_id: e.target.value })}>
                  {lists.jurisdictions.map(([j, t]) => <option key={j} value={j}>{t}</option>)}</select>{" "}
                <select aria-label="Facility" value={edit.facility_id || ""} onChange={(e) => setEdit({ ...edit, facility_id: e.target.value || null })}>
                  <option value="">No facility</option>
                  {lists.facilities.filter(([, t]) => t.endsWith(edit.jurisdiction_id)).map(([f, t]) => <option key={f} value={f}>{t}</option>)}</select>
              </td>
              <td className="btn-row">
                <button className="btn primary" onClick={() => change(u, { role: edit.role, jurisdiction_id: edit.jurisdiction_id, facility_id: edit.facility_id }, `${u.full_name} updated.`)}>Save</button>
                <button className="btn" onClick={() => setEdit(null)}>Cancel</button>
              </td>
            </tr>
          ) : (
            <tr key={u.id} className={u.active ? "" : "muted"}>
              <th scope="row">{u.full_name}<div className="muted">{u.username}{u.active ? "" : " · deactivated"}</div></th>
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
                <button className="btn" onClick={() => { setEdit({ id: u.id, role: u.role, jurisdiction_id: u.jurisdiction_id, facility_id: u.facility_id }); setMsg(null); }}>Edit</button>
                <button className="btn" onClick={() => change(u, { active: !u.active }, `${u.full_name} ${u.active ? "deactivated" : "reactivated"}.`)}>{u.active ? "Deactivate user" : "Reactivate user"}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      <h2>Add a user</h2>
      <Form button="Add user" onDone={reload}
            fields={[["username", "Username"], ["full_name", "Full name"], ["role", "Role", "text", roles],
                     ["jurisdiction_id", "Jurisdiction", "text", lists.jurisdictions], ["ward", "Ward (optional)"],
                     ["facility_id", "Facility (officers-in-charge, outreach teams)", "text", lists.facilities],
                     ["phone_number", "Phone number (+234…)"]]}
            submit={async (v) => { await api("/users", { method: "POST", body: v }); return `${v.full_name} added.`; }} />
    </>
  );
}

function Jurisdictions() {
  const { data, error, reload } = useApi("/jurisdictions");
  const [names, setNames] = useState({});
  const [msg, setMsg] = useState(null);
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const rename = async (j) => {
    try {
      await api(`/jurisdictions/${j.id}`, { method: "PATCH", body: { name: names[j.id] } });
      setMsg({ ok: true, text: `${j.id} renamed.` });
      setNames({ ...names, [j.id]: "" });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  const parents = data.filter((j) => j.level !== "lga").map((j) => [j.id, `${j.name} (${j.id})`]);
  return (
    <>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Code</th><th>Name</th><th>Level</th><th>Within</th><th>New name</th><th></th></tr></thead>
        <tbody>{data.map((j) => (
          <tr key={j.id}><th scope="row">{j.id}</th><td>{j.name}</td><td>{j.level === "lga" ? "LGA" : j.level}</td><td>{j.parent_id || "—"}</td>
            <td><input aria-label={`New name for ${j.id}`} value={names[j.id] || ""} onChange={(e) => setNames({ ...names, [j.id]: e.target.value })} /></td>
            <td><button className="btn" disabled={!names[j.id]} onClick={() => rename(j)}>Rename</button></td></tr>
        ))}</tbody>
      </table></div>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      <h2>Add a state or LGA</h2>
      <Form button="Add jurisdiction" onDone={reload}
            fields={[["level", "Level", "text", [["state", "State"], ["lga", "LGA"]]], ["parent_id", "Within", "text", parents],
                     ["id", "Code (the parent's code plus a suffix, e.g. NG-KN-ABC)"], ["name", "Name"]]}
            submit={async (v) => { await api("/jurisdictions", { method: "POST", body: v }); return `${v.name} added.`; }} />
    </>
  );
}

const TIERS = [["state_store", "State store"], ["lga_store", "LGA store"], ["health_facility", "Health facility"],
               ["outreach_base", "Outreach base"], ["zonal_store", "Zonal store"], ["national_store", "National store"]];
const POWER = [["grid", "Grid"], ["solar", "Solar"], ["generator", "Generator"], ["grid_generator", "Grid and generator"], ["none", "None"]];

function Facilities() {
  const lists = useLists();
  const [edit, setEdit] = useState(null);
  const [msg, setMsg] = useState(null);
  const [filter, setFilter] = useState("");
  const rows = lists.raw.facilities.filter((f) => !filter || `${f.name} ${f.ward || ""} ${f.jurisdiction_id}`.toLowerCase().includes(filter.toLowerCase()));
  const save = async () => {
    try {
      await api(`/facilities/${edit.id}`, { method: "PATCH", body: { name: edit.name, power_source: edit.power_source, ward: edit.ward || undefined, lat: Number(edit.lat), lon: Number(edit.lon) } });
      setMsg({ ok: true, text: `${edit.name} updated.` });
      setEdit(null);
      lists.reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  return (
    <>
      <label className="field"><span>Find a facility</span><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Name, ward or LGA code" /></label>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Name</th><th>Type</th><th>Ward</th><th>Jurisdiction</th><th>Power</th><th className="num">Latitude</th><th className="num">Longitude</th><th></th></tr></thead>
        <tbody>{rows.slice(0, 100).map((f) => edit?.id === f.id ? (
          <tr key={f.id} className="editing">
            <td><input aria-label="Name" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></td>
            <td>{label(Object.fromEntries(TIERS), f.tier)}</td>
            <td><input aria-label="Ward" value={edit.ward || ""} onChange={(e) => setEdit({ ...edit, ward: e.target.value })} /></td>
            <td>{f.jurisdiction_id}</td>
            <td><select aria-label="Power source" value={edit.power_source} onChange={(e) => setEdit({ ...edit, power_source: e.target.value })}>
              {POWER.map(([p, t]) => <option key={p} value={p}>{t}</option>)}</select></td>
            <td><input aria-label="Latitude" type="number" step="any" value={edit.lat} onChange={(e) => setEdit({ ...edit, lat: e.target.value })} /></td>
            <td><input aria-label="Longitude" type="number" step="any" value={edit.lon} onChange={(e) => setEdit({ ...edit, lon: e.target.value })} /></td>
            <td className="btn-row"><button className="btn primary" onClick={save}>Save</button><button className="btn" onClick={() => setEdit(null)}>Cancel</button></td>
          </tr>
        ) : (
          <tr key={f.id}><th scope="row">{f.name}</th><td>{label(Object.fromEntries(TIERS), f.tier)}</td><td>{f.ward || "—"}</td><td>{f.jurisdiction_id}</td>
            <td>{label(Object.fromEntries(POWER), f.power_source)}</td><td className="num">{f.lat.toFixed(4)}</td><td className="num">{f.lon.toFixed(4)}</td>
            <td><button className="btn" onClick={() => { setEdit({ ...f }); setMsg(null); }}>Edit</button></td></tr>
        ))}</tbody>
      </table></div>
      {rows.length > 100 && <p className="muted">Showing 100 of {rows.length}; narrow the search to see others.</p>}
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      <h2>Add a facility</h2>
      <Form button="Add facility" onDone={lists.reload}
            fields={[["name", "Name"], ["tier", "Type", "text", TIERS], ["jurisdiction_id", "Jurisdiction", "text", lists.jurisdictions], ["ward", "Ward (optional)"],
                     ["parent_facility_id", "Supplied by", "text", lists.facilities], ["power_source", "Power source", "text", POWER],
                     ["lat", "Latitude", "number"], ["lon", "Longitude", "number"]]}
            submit={async (v) => { await api("/facilities", { method: "POST", body: { ...v, ward: v.ward || null, lat: Number(v.lat), lon: Number(v.lon) } }); return `${v.name} added.`; }} />
    </>
  );
}

function Devices() {
  const { data, error, reload } = useApi("/devices");
  const lists = useLists();
  const [msg, setMsg] = useState(null);
  const [filter, setFilter] = useState("");
  const [moves, setMoves] = useState({});
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const facilityName = Object.fromEntries(lists.facilities);
  const change = async (d, body, text) => {
    if (body.status === "retired" && !window.confirm(`Retire ${d.serial_no}? Its readings will no longer be accepted.`)) return;
    try {
      await api(`/devices/${d.id}`, { method: "PATCH", body });
      setMsg({ ok: true, text });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  const rows = data.filter((d) => !filter || d.serial_no.toLowerCase().includes(filter.toLowerCase()));
  return (
    <>
      <label className="field"><span>Find a device</span><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Serial number" /></label>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Serial</th><th>Type</th><th>Facility</th><th>Sensors</th><th className="num">Interval (s)</th><th>Status</th><th>Move to</th><th></th></tr></thead>
        <tbody>{rows.slice(0, 100).map((d) => (
          <tr key={d.id}>
            <th scope="row">{d.serial_no}</th><td>{d.type} · {d.equipment_type}</td><td>{facilityName[d.facility_id] || "—"}</td>
            <td>{[d.has_door_sensor && "door", d.has_power_signal && "power"].filter(Boolean).join(", ") || "temperature only"}</td>
            <td className="num">{d.reporting_interval_s}</td><td>{d.status}</td>
            <td><select aria-label={`Move ${d.serial_no} to`} value={moves[d.id] || ""} onChange={(e) => setMoves({ ...moves, [d.id]: e.target.value })}>
              <option value="">—</option>{lists.facilities.map(([f, t]) => <option key={f} value={f}>{t}</option>)}</select></td>
            <td className="btn-row">
              <button className="btn" disabled={!moves[d.id]} onClick={() => change(d, { facility_id: moves[d.id] }, `${d.serial_no} moved.`)}>Move</button>
              {d.status === "active"
                ? <button className="btn" onClick={() => change(d, { status: "retired" }, `${d.serial_no} retired.`)}>Retire</button>
                : <button className="btn" onClick={() => change(d, { status: "active" }, `${d.serial_no} reactivated.`)}>Reactivate</button>}
            </td>
          </tr>
        ))}</tbody>
      </table></div>
      {rows.length > 100 && <p className="muted">Showing 100 of {rows.length}; narrow the search to see others.</p>}
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      <h2>Register a device</h2>
      <Form button="Register device" onDone={reload} initial={{ type: "fixed", reporting_interval_s: 60 }}
            fields={[["serial_no", "Serial number"], ["type", "Type", "text", [["fixed", "Fixed (in a facility)"], ["mobile", "Mobile (travels with shipments)"]]],
                     ["equipment_type", "Equipment", "text", [["ice_lined_refrigerator", "Ice-lined refrigerator"], ["solar_direct_drive", "Solar direct-drive refrigerator"],
                                                                ["walk_in_cold_room", "Walk-in cold room"], ["vaccine_carrier", "Vaccine carrier"], ["cold_box", "Cold box"]]],
                     ["facility_id", "Facility", "text", lists.facilities], ["has_door_sensor", "Door sensor", "checkbox"],
                     ["has_power_signal", "Power signal", "checkbox"], ["reporting_interval_s", "Reporting interval (seconds)", "number"]]}
            submit={async (v) => { await api("/devices", { method: "POST", body: { ...v, reporting_interval_s: Number(v.reporting_interval_s) } }); return `${v.serial_no} registered.`; }} />
    </>
  );
}

const CLOCKS = { acknowledge: "Acknowledge by", resolve: "Resolve by", reminder: "Reminder every" };
const TIER_NAMES = { owner: "Owner", lga: "LGA officer", state: "State officer", national: "National officer", next: "Next tier up" };

function Policy() {
  const { data, error, reload } = useApi("/escalation-policy");
  const [edit, setEdit] = useState({});
  const [msg, setMsg] = useState(null);
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const save = async (p) => {
    try {
      await api(`/escalation-policy/${p.id}`, { method: "PATCH", body: { delay_min: Number(edit[p.id]) } });
      setMsg({ ok: true, text: "Time limit changed. Open incidents follow it from their next step." });
      setEdit({ ...edit, [p.id]: "" });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  return (
    <>
      <p className="hint">Acknowledgement limits count from when the incident is created; resolution limits from
        acknowledgement. Each limit must be later than the one before it.</p>
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Incident</th><th>Step</th><th>Goes to</th><th className="num">Minutes</th><th>New value</th><th></th></tr></thead>
        <tbody>{data.map((p) => (
          <tr key={p.id}>
            <th scope="row">{p.severity}, {p.mode}</th><td>{CLOCKS[p.kind]} (step {p.stage})</td><td>{TIER_NAMES[p.tier] || p.tier}</td>
            <td className="num">{p.delay_min}</td>
            <td><input type="number" min="0" aria-label={`New time limit for ${p.severity} ${p.mode} ${p.kind} step ${p.stage}`}
                       value={edit[p.id] ?? ""} onChange={(e) => setEdit({ ...edit, [p.id]: e.target.value })} /></td>
            <td><button className="btn" disabled={edit[p.id] === undefined || edit[p.id] === ""} onClick={() => save(p)}>Save</button></td>
          </tr>
        ))}</tbody>
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
