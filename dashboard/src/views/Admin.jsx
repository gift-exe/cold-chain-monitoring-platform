// Administration (FR11, Table 3.21). Administrators manage users, jurisdictions, facilities, devices and
// phone numbers, and watch the system; national officers configure products and the escalation policy.
// Every change is recorded in the audit trail (FR11.2).
import { useState } from "react";

import { api, useApi } from "../api.js";
import Dialog from "../components/Dialog.jsx";
import { Pager, usePage } from "../components/PagedList.jsx";
import { ago, duration, label, ROLES, time } from "../format.js";

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
      ["status", "System status", SystemStatus], ["sms", "Text messages", TextMessages], ["users", "Users and phone numbers", Users],
      ["jurisdictions", "Jurisdictions", Jurisdictions], ["facilities", "Facilities", Facilities],
      ["devices", "Devices", Devices]]} />;
  }
  return <div className="page"><p className="muted">Administration is for national officers and administrators.</p></div>;
}

// An "add" form in a dialog (E9, P25). Fields are [key, label, type, options]; options are [value, text]
// pairs for a select. `submit` returns the message to show once it has worked.
function AddDialog({ title, intro, fields, initial = {}, submit, button, onClose, onDone }) {
  const [v, setV] = useState(initial);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setErr(null);
    setBusy(true);
    try {
      onDone(await submit(v));
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Dialog title={title} intro={intro} onClose={onClose} onSubmit={go} submitLabel={button} busy={busy} error={err}>
      {fields.map(([k, t, type = "text", options]) => (
        <label key={k} className={`field ${type === "checkbox" ? "check-field inline" : ""}`}>
          {type === "checkbox" ? null : <span>{t}</span>}
          {options ? (
            <select value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value || null })}>
              <option value="">Choose…</option>
              {options.map(([o, ot]) => <option key={o} value={o}>{ot}</option>)}
            </select>
          ) : type === "checkbox" ? (
            <><input type="checkbox" checked={!!v[k]} onChange={(e) => setV({ ...v, [k]: e.target.checked })} /> {t}</>
          ) : (
            <input type={type} step="any" value={v[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
          )}
        </label>
      ))}
    </Dialog>
  );
}

// The line above a table: what it is, and the button that adds to it.
function TableHead({ hint, button, onAdd, children }) {
  return (
    <div className="section-head">
      <div className="section-head-main">{hint && <p className="hint">{hint}</p>}{children}</div>
      {button && <button className="btn primary" onClick={onAdd}>{button}</button>}
    </div>
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
      const body = { ...Object.fromEntries(FIELDS.map(([k]) => [k, Number(edit[k])])), freeze_sensitive: !!edit.freeze_sensitive };
      await api(`/products/${edit.id}`, { method: "PATCH", body });
      setMsg({ ok: true, text: `${edit.name} updated. Detection uses the new values from now on.` });
      setEdit(null);
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  const [adding, setAdding] = useState(false);
  const pg = usePage(data || []);
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="section-head">
        <p className="hint">Tolerances are how long a reading may stay out of limits before an incident is raised.
          For stores, detection applies the strictest values across all products.</p>
        <button className="btn primary" onClick={() => { setAdding(true); setMsg(null); }}>Add product</button>
      </div>
      {adding && <AddProduct onClose={() => setAdding(false)}
                             onDone={(name) => { setAdding(false); setMsg({ ok: true, text: `${name} added. Detection uses its limits from now on.` }); reload(); }} />}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Product</th>{FIELDS.map(([, t]) => <th key={t} className="num">{t}</th>)}<th>Damaged by freezing</th><th></th></tr></thead>
        <tbody>
          {pg.rows.map((p) => edit?.id === p.id ? (
            <tr key={p.id} className="editing">
              <th scope="row">{p.name}</th>
              {FIELDS.map(([k, t, step]) => (
                <td key={k}><input type="number" step={step} aria-label={`${t} for ${p.name}`} value={edit[k]}
                                   onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></td>
              ))}
              <td><input type="checkbox" aria-label={`${p.name} is damaged by freezing`} checked={!!edit.freeze_sensitive}
                         onChange={(e) => setEdit({ ...edit, freeze_sensitive: e.target.checked })} /></td>
              <td><button className="btn primary" onClick={save}>Save</button> <button className="btn" onClick={() => setEdit(null)}>Cancel</button></td>
            </tr>
          ) : (
            <tr key={p.id}>
              <th scope="row">{p.name}</th>
              {FIELDS.map(([k]) => <td key={k} className="num">{Number(p[k])}</td>)}
              <td>{p.freeze_sensitive ? "Yes" : "No"}</td>
              <td><button className="btn" onClick={() => { setEdit({ ...p }); setMsg(null); }}>Edit</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
      <Pager pg={pg} label="Products" />
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
  const [adding, setAdding] = useState(false);
  const [find, setFind] = useState("");
  const roles = Object.entries(ROLES);
  const shown = (data || []).filter((u) => !find || `${u.full_name} ${u.username} ${u.phone_number || ""} ${u.facility_name || ""} ${u.jurisdiction_name || ""}`.toLowerCase().includes(find.toLowerCase()));
  const pg = usePage(shown, { resetKey: find });
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
      <TableHead button="Add user" onAdd={() => { setAdding(true); setMsg(null); }}
                 hint="A user's role and area decide what they see and which incidents reach them. Dashboard sign-in accounts are created in the identity server; people who only use SMS need none.">
        <label className="field find"><span>Find a user</span><input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Name, username, phone number or place" /></label>
      </TableHead>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      {adding && <AddDialog title="Add user" button="Add user" onClose={() => setAdding(false)}
                            onDone={(text) => { setAdding(false); setMsg({ ok: true, text }); reload(); }}
                            fields={[["username", "Username"], ["full_name", "Full name"], ["role", "Role", "text", roles],
                                     ["jurisdiction_id", "Jurisdiction", "text", lists.jurisdictions],
                                     ["facility_id", "Facility (officers-in-charge, outreach teams)", "text", lists.facilities],
                                     ["phone_number", "Phone number (+234…)"]]}
                            submit={async (v) => { await api("/users", { method: "POST", body: v }); return `${v.full_name} added.`; }} />}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Name</th><th>Role</th><th>Area</th><th>Phone</th><th>New number</th><th></th></tr></thead>
        <tbody>
          {pg.rows.map((u) => edit?.id === u.id ? (
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
      <Pager pg={pg} label="Users" />
    </>
  );
}

function Jurisdictions() {
  const { data, error, reload } = useApi("/jurisdictions");
  const [names, setNames] = useState({});
  const [msg, setMsg] = useState(null);
  const [adding, setAdding] = useState(false);
  const pg = usePage(data || []);
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
      <TableHead button="Add state or LGA" onAdd={() => { setAdding(true); setMsg(null); }}
                 hint="States and LGAs decide which officers see which facilities and incidents." />
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      {adding && <AddDialog title="Add a state or LGA" button="Add" onClose={() => setAdding(false)}
                            onDone={(text) => { setAdding(false); setMsg({ ok: true, text }); reload(); }}
                            fields={[["level", "Level", "text", [["state", "State"], ["lga", "LGA"]]], ["parent_id", "Within", "text", parents],
                                     ["id", "Code (the parent's code plus a suffix, e.g. NG-KN-ABC)"], ["name", "Name"]]}
                            submit={async (v) => { await api("/jurisdictions", { method: "POST", body: v }); return `${v.name} added.`; }} />}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Code</th><th>Name</th><th>Level</th><th>Within</th><th>New name</th><th></th></tr></thead>
        <tbody>{pg.rows.map((j) => (
          <tr key={j.id}><th scope="row">{j.id}</th><td>{j.name}</td><td>{j.level === "lga" ? "LGA" : j.level}</td><td>{j.parent_id || "—"}</td>
            <td><input aria-label={`New name for ${j.id}`} value={names[j.id] || ""} onChange={(e) => setNames({ ...names, [j.id]: e.target.value })} /></td>
            <td><button className="btn" disabled={!names[j.id]} onClick={() => rename(j)}>Rename</button></td></tr>
        ))}</tbody>
      </table></div>
      <Pager pg={pg} label="Jurisdictions" />
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
  const [adding, setAdding] = useState(false);
  const rows = lists.raw.facilities.filter((f) => !filter || `${f.name} ${f.ward || ""} ${f.jurisdiction_id}`.toLowerCase().includes(filter.toLowerCase()));
  const pg = usePage(rows, { resetKey: filter });
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
      <TableHead button="Add facility" onAdd={() => { setAdding(true); setMsg(null); }}>
        <label className="field find"><span>Find a facility</span><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Name, ward or LGA code" /></label>
      </TableHead>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      {adding && <AddDialog title="Add facility" button="Add facility" onClose={() => setAdding(false)}
                            onDone={(text) => { setAdding(false); setMsg({ ok: true, text }); lists.reload(); }}
                            fields={[["name", "Name"], ["tier", "Type", "text", TIERS], ["jurisdiction_id", "Jurisdiction", "text", lists.jurisdictions], ["ward", "Ward (optional)"],
                                     ["parent_facility_id", "Supplied by", "text", lists.facilities], ["power_source", "Power source", "text", POWER],
                                     ["lat", "Latitude", "number"], ["lon", "Longitude", "number"]]}
                            submit={async (v) => { await api("/facilities", { method: "POST", body: { ...v, ward: v.ward || null, lat: Number(v.lat), lon: Number(v.lon) } }); return `${v.name} added.`; }} />}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Name</th><th>Type</th><th>Ward</th><th>Jurisdiction</th><th>Power</th><th className="num">Latitude</th><th className="num">Longitude</th><th></th></tr></thead>
        <tbody>{pg.rows.map((f) => edit?.id === f.id ? (
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
      <Pager pg={pg} label="Facilities" />
    </>
  );
}

function Devices() {
  const { data, error, reload } = useApi("/devices");
  const lists = useLists();
  const [msg, setMsg] = useState(null);
  const [filter, setFilter] = useState("");
  const [moves, setMoves] = useState({});
  const [adding, setAdding] = useState(false);
  const rows = (data || []).filter((d) => !filter || d.serial_no.toLowerCase().includes(filter.toLowerCase()));
  const pg = usePage(rows, { resetKey: filter });
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
  return (
    <>
      <TableHead button="Register device" onAdd={() => { setAdding(true); setMsg(null); }}>
        <label className="field find"><span>Find a device</span><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Serial number" /></label>
      </TableHead>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      {adding && <AddDialog title="Register a device" button="Register device" initial={{ type: "fixed", reporting_interval_s: 60 }}
                            intro="A temperature logger. A fixed one belongs to a facility; a mobile one travels with shipments."
                            onClose={() => setAdding(false)} onDone={(text) => { setAdding(false); setMsg({ ok: true, text }); reload(); }}
                            fields={[["serial_no", "Serial number"], ["type", "Type", "text", [["fixed", "Fixed (in a facility)"], ["mobile", "Mobile (travels with shipments)"]]],
                                     ["equipment_type", "Equipment", "text", [["ice_lined_refrigerator", "Ice-lined refrigerator"], ["solar_direct_drive", "Solar direct-drive refrigerator"],
                                                                                ["walk_in_cold_room", "Walk-in cold room"], ["vaccine_carrier", "Vaccine carrier"], ["cold_box", "Cold box"]]],
                                     ["facility_id", "Facility (fixed loggers)", "text", lists.facilities], ["has_door_sensor", "Has a door sensor", "checkbox"],
                                     ["has_power_signal", "Reports mains power", "checkbox"], ["reporting_interval_s", "Reporting interval (seconds)", "number"]]}
                            submit={async (v) => { await api("/devices", { method: "POST", body: { ...v, reporting_interval_s: Number(v.reporting_interval_s) } }); return `${v.serial_no} registered.`; }} />}
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Serial</th><th>Type</th><th>Facility</th><th>Sensors</th><th className="num">Interval (s)</th><th>Status</th><th>Move to</th><th></th></tr></thead>
        <tbody>{pg.rows.map((d) => (
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
      <Pager pg={pg} label="Devices" />
    </>
  );
}

// Add a product (E9, P23), with the usual 2-8 °C range filled in.
function AddProduct({ onClose, onDone }) {
  const [f, setF] = useState({ name: "", lower_limit: 2, upper_limit: 8, freeze_limit: 0, heat_tolerance_min: 30,
                               freeze_tolerance_min: 5, freeze_sensitive: true });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const submit = async () => {
    setErr(null);
    setBusy(true);
    try {
      const body = { ...f, name: f.name.trim(), ...Object.fromEntries(FIELDS.map(([k]) => [k, Number(f[k])])) };
      await api("/products", { method: "POST", body });
      onDone(body.name);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Dialog title="Add product" intro="A vaccine or diluent the system monitors. Its limits apply to shipments of it, and to stores as part of the strictest values."
            onClose={onClose} onSubmit={submit} submitLabel="Add product" busy={busy} error={err}>
      <label className="field"><span>Name</span><input required minLength={2} maxLength={100} value={f.name} onChange={set("name")} placeholder="e.g. Rotavirus" /></label>
      <fieldset className="field">
        <legend>Safe temperature range</legend>
        <div className="field-pair">
          <label className="field"><span>Too cold below (°C)</span><input type="number" step="0.1" required value={f.lower_limit} onChange={set("lower_limit")} /></label>
          <label className="field"><span>Too warm above (°C)</span><input type="number" step="0.1" required value={f.upper_limit} onChange={set("upper_limit")} /></label>
        </div>
      </fieldset>
      <div className="field-pair">
        <label className="field"><span>Freezing at or below (°C)</span><input type="number" step="0.1" required value={f.freeze_limit} onChange={set("freeze_limit")} /></label>
        <label className="field check-field"><input type="checkbox" checked={f.freeze_sensitive} onChange={set("freeze_sensitive")} /> Damaged by freezing</label>
      </div>
      <fieldset className="field">
        <legend>How long it may be out of range before an incident is raised</legend>
        <div className="field-pair">
          <label className="field"><span>Too warm for (minutes)</span><input type="number" min="0" required value={f.heat_tolerance_min} onChange={set("heat_tolerance_min")} /></label>
          <label className="field"><span>Freezing for (minutes)</span><input type="number" min="0" required value={f.freeze_tolerance_min} onChange={set("freeze_tolerance_min")} /></label>
        </div>
      </fieldset>
    </Dialog>
  );
}

// Escalation policy (E9, P24): each kind of incident as a sequence in plain words, with each time limit
// editable in place. The stored rows are Table 3.15; the limits of one clock are cumulative.
const LADDERS = [
  ["critical", "operational", "Critical incidents caught while happening",
   "Something can still be saved, so a response is needed quickly."],
  ["warning", "operational", "Warnings caught while happening", "Less urgent: only the LGA officer is brought in."],
  ["critical", "retrospective", "Critical incidents found after they ended",
   "The event is over (for example, a logger's readings arrived late), so the job is to check the vaccines; the limits are longer."],
  ["warning", "retrospective", "Warnings found after they ended", ""],
];
const WHO = { owner: "the person responsible", lga: "the LGA officer", state: "the state officer",
              national: "the national officer", next: "the next level up" };

function Policy() {
  const { data, error, reload } = useApi("/escalation-policy");
  const [msg, setMsg] = useState(null);
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const save = async (p, minutes) => {
    setMsg(null);
    try {
      await api(`/escalation-policy/${p.id}`, { method: "PATCH", body: { delay_min: minutes } });
      setMsg({ ok: true, text: "Time limit changed. Open incidents follow it from their next step." });
      reload();
      return null;
    } catch (e) {
      return e.message.replace(/^time limits are cumulative: /, "Not saved: ");   // shown beside the step
    }
  };
  return (
    <>
      <p className="hint">Who is told about an incident, and when, if nobody responds. Times count from when the
        incident was raised; once someone responds, from their response. Each step must come later than the one
        before it.</p>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      {LADDERS.map(([sev, mode, title, note]) => {
        const rows = data.filter((p) => p.severity === sev && p.mode === mode);
        if (rows.length === 0) return null;
        const ack = rows.filter((p) => p.kind === "acknowledge").sort((a, b) => a.stage - b.stage);
        const res = rows.filter((p) => p.kind === "resolve").sort((a, b) => a.stage - b.stage);
        const rem = rows.filter((p) => p.kind === "reminder");
        return (
          <section key={title} className="ladder">
            <h3>{title}</h3>
            {note && <p className="muted ladder-note">{note}</p>}
            <ol className="ladder-steps">
              {ack.map((p, k) => (
                <Step key={p.id} p={p} save={save} fixed={p.stage === 0 && p.delay_min === 0}
                      text={p.stage === 0 ? `${cap(WHO[p.tier])} is sent an SMS.`
                        : `${k === 1 ? "If nobody has responded" : "Still no response"}: ${WHO[p.tier]} is told.`} />
              ))}
              {rem.map((p) => (
                <Step key={p.id} p={p} save={save} every
                      text={`If it reaches the top and is still not dealt with, ${WHO[p.tier]} is reminded.`} />
              ))}
            </ol>
            {res.length > 0 && (
              <>
                <p className="ladder-sub">Once someone responds, it must be fixed in time:</p>
                <ol className="ladder-steps">
                  {res.map((p, k) => (
                    <Step key={p.id} p={p} save={save} after
                          text={`${k === 0 ? "If not fixed" : "Still not fixed"}: it goes to ${WHO[p.tier]}.`} />
                  ))}
                </ol>
              </>
            )}
          </section>
        );
      })}
    </>
  );
}

const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);

function Step({ p, save, text, fixed = false, every = false, after = false }) {
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState(null);
  const when = fixed ? "At once" : `${every ? "Every " : after ? "" : "After "}${duration(p.delay_min)}${after ? " after the response" : ""}`;
  const start = () => setEdit({ h: Math.floor(p.delay_min / 60), m: p.delay_min % 60 });
  const go = async () => {
    const problem = await save(p, Number(edit.h) * 60 + Number(edit.m));
    setErr(problem);
    if (!problem) setEdit(null);
  };
  return (
    <li className="ladder-step">
      {edit ? (
        <span className="ladder-edit">
          <label><input type="number" min="0" value={edit.h} onChange={(e) => setEdit({ ...edit, h: e.target.value })} aria-label="Hours" /> h</label>
          <label><input type="number" min="0" max="59" value={edit.m} onChange={(e) => setEdit({ ...edit, m: e.target.value })} aria-label="Minutes" /> min</label>
        </span>
      ) : <span className="ladder-when">{when}</span>}
      <span>{text}</span>
      <span className="btn-row ladder-act">
        {fixed ? null : edit ? (
          <>
            <button className="btn btn-small primary" onClick={go}>Save</button>
            <button className="btn btn-small" onClick={() => { setEdit(null); setErr(null); }}>Cancel</button>
          </>
        ) : <button className="btn btn-small" onClick={start} aria-label={`Change: ${when}, ${text}`}>Change</button>}
      </span>
      {err && <p className="error ladder-err" role="alert">{err}</p>}
    </li>
  );
}

// Text messages (E9): every SMS the system sent and received, newest first, so people testing the system
// can see them (in development they go to the local gateway emulator, not to phones). With the emulator,
// the administrator can also send a message as if from a user's phone, to try the SMS commands.
const SMS_STATUS = { queued: "Waiting to send", sent: "Sent", delivered: "Delivered", retrying: "Retrying",
                     undelivered: "Not delivered", rejected: "Refused by the gateway", held: "Held for the daily summary",
                     digested: "Sent in the daily summary" };
const SMS_OUTCOME = { accepted: "Accepted", no_effect: "No effect", rejected: "Refused", rate_limited: "Too many messages" };

function TextMessages() {
  const { data, error, reload } = useApi("/sms", ["notification", "incident", "custody_handoff"], 10000);
  const { data: users } = useApi("/users");
  const [find, setFind] = useState("");
  const [reply, setReply] = useState(null);
  const [msg, setMsg] = useState(null);
  const rows = (data?.messages || []).filter((m) => !find || `${m.full_name || ""} ${m.phone || ""} ${m.text}`.toLowerCase().includes(find.toLowerCase()));
  const pg = usePage(rows, { resetKey: find });
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const phones = (users || []).filter((u) => u.phone_number && !u.phone_deactivated_at && u.active);
  return (
    <>
      <TableHead button={data.can_reply ? "Send as a phone" : null} onAdd={() => { setReply({ from_number: "", text: "" }); setMsg(null); }}
                 hint={`Every SMS the system sent and received, newest first. In this test set-up they go to a local stand-in for the SMS gateway, not to real phones.${data.can_reply ? " To try a command, send a message as if from someone's phone." : ""}`}>
        <label className="field find"><span>Find a message</span><input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Name, phone number or text" /></label>
      </TableHead>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      {reply && <SendAsPhone initial={reply} phones={phones} onClose={() => setReply(null)}
                             onDone={(text) => { setReply(null); setMsg({ ok: true, text }); setTimeout(reload, 1500); }} />}
      {rows.length === 0 ? <p className="empty">No text messages yet.</p> : (
        <ol className="sms-list">
          {pg.rows.map((m) => (
            <li key={`${m.direction}-${m.id}`} className={`sms sms-${m.direction}`}>
              <div className="sms-meta">
                <span className="sms-who">{m.direction === "sent" ? "To " : "From "}<strong>{m.full_name || "unknown number"}</strong>
                  {m.role && <span className="muted"> ({label(ROLES, m.role)})</span>} <span className="muted">{m.phone}</span></span>
                <time className="muted">{time(m.at)}</time>
              </div>
              <p className="sms-text">{m.text}</p>
              <div className="sms-foot">
                <span className={`sms-status ${["undelivered", "rejected"].includes(m.status) ? "bad" : ""}`}>
                  {m.direction === "sent" ? SMS_STATUS[m.status] || m.status
                    : `${SMS_OUTCOME[m.status] || m.status}${m.reason ? `: ${m.reason}` : ""}`}
                </span>
                {data.can_reply && m.direction === "sent" && m.phone && (
                  <button className="btn btn-small" onClick={() => { setReply({ from_number: m.phone, text: "" }); setMsg(null); }}>Reply as this phone</button>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      <Pager pg={pg} label="Text messages" />
    </>
  );
}

function SendAsPhone({ initial, phones, onClose, onDone }) {
  const [v, setV] = useState(initial);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setErr(null);
    setBusy(true);
    try {
      await api("/sms/reply", { method: "POST", body: v });
      const who = phones.find((p) => p.phone_number === v.from_number);
      onDone(`Sent "${v.text}" from ${who ? who.full_name : v.from_number}. The system's answer appears in the list.`);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Dialog title="Send as a phone" submitLabel="Send" busy={busy} error={err} onClose={onClose} onSubmit={submit}
            intro="The system receives this as if the person had texted it from their phone.">
      <label className="field"><span>From</span>
        <select required value={v.from_number} onChange={(e) => setV({ ...v, from_number: e.target.value })}>
          <option value="">Choose a person…</option>
          {phones.map((p) => <option key={p.id} value={p.phone_number}>{p.full_name} ({label(ROLES, p.role)}), {p.phone_number}</option>)}
        </select></label>
      <label className="field"><span>Message</span>
        <input required maxLength={160} value={v.text} onChange={(e) => setV({ ...v, text: e.target.value })} placeholder="e.g. ACK 1234" />
        <span className="hint">Commands: ACK &lt;incident&gt; to acknowledge; RES &lt;incident&gt; &lt;code&gt; &lt;what was done&gt; to resolve;
          REC &lt;shipment&gt; &lt;code&gt; to confirm receipt; REJ &lt;shipment&gt; &lt;code&gt; &lt;reason&gt; to refuse; HELP. Codes are in the messages above.</span>
      </label>
    </Dialog>
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
