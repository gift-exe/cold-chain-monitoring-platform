// Vaccines in transit (Section 3.4.4): consignments, called shipments on the dashboard (E9, P18), and who
// is responsible for each one. Laid out like the incidents page (P19): shipments grouped by what needs
// doing, details beside the list, still at /consignments/<id>. The details say in plain words where the
// shipment is and how cold it has been, with its journey from hand to hand, a map, the temperature and any
// incidents (P20). New shipments are created in a dialog (P21). Each custody action is offered only to
// the person allowed to take it. Figures for the user's area sit above the list, with a breakdown by
// facility, LGA or state for supervisors (F08).
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api, useApi } from "../api.js";
import { Flag, Status, STATUS } from "../components/Status.jsx";
import { ago, clock, duration, label, ROLES, STATES, temp, time, TYPES } from "../format.js";
import { MiniMap } from "./IncidentDetail.jsx";
import PagedList from "../components/PagedList.jsx";

const KINDS = ["consignment", "custody_handoff", "incident"];
const ROLE_SHORT = { outreach_lead: "outreach team lead", driver: "driver", facility_oic: "facility officer",
                     lga_officer: "LGA officer", state_officer: "state officer" };
const who = (name, role) => (name ? `${name}${ROLE_SHORT[role] ? ` (${ROLE_SHORT[role]})` : ""}` : "nobody");
const doses = (c) => `${c.quantity} doses of ${c.product_name}`;

// Most urgent first. "Needs you": a shipment released to you, or one you released that was rejected.
const SECTIONS = [
  ["needs", "Needs you", (c) => c.can_confirm || (c.state === "rejected" && c.can_cancel)],
  ["moving", "Handed over, not yet confirmed", (c) => c.state === "in_handoff" || c.state === "rejected"],
  ["mine", "With you", (c) => c.state === "held" && c.can_complete],
  ["others", "With others", (c) => c.state === "held"],
];

export default function Consignments({ me }) {
  const { id } = useParams();
  const [params] = useSearchParams();
  const done = params.get("show") === "completed";
  const { data, error } = useApi(`/consignments?completed=${done}`, KINDS);
  const { data: figures } = useApi("/consignments/summary", KINDS);
  const canCreate = me?.role !== "national_officer";
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const qs = params.toString() ? `?${params}` : "";

  const list = data ? data.filter((c) => (done ? c.completed_at : !c.completed_at)) : [];
  const groups = done ? [["done", "Completed", list]] : (() => {
    const left = [...list];
    return SECTIONS.map(([key, title, test]) => {
      const mine = left.filter(test);
      mine.forEach((c) => left.splice(left.indexOf(c), 1));
      return [key, title, mine];
    });
  })();

  return (
    <div className={`incidents ${id ? "has-detail" : ""}`}>
      <div className="inc-pane">
        <div className="page-head">
          <div>
            <h1>Vaccines in transit</h1>
            <p className="page-sub">Vaccines on the move: who has them, where they are going, and how cold they are.</p>
          </div>
          <div className="btn-row">
            <div className="tabs" role="tablist">
              <button role="tab" aria-selected={!done} className={!done ? "on" : ""} onClick={() => navigate(`/consignments${id ? `/${id}` : ""}`)}>In transit</button>
              <button role="tab" aria-selected={done} className={done ? "on" : ""} onClick={() => navigate(`/consignments${id ? `/${id}` : ""}?show=completed`)}>Completed</button>
            </div>
            {canCreate && <button className="btn primary" onClick={() => setCreating(true)}>New shipment</button>}
          </div>
        </div>
        {figures && !id && <Figures f={figures} role={me?.role} />}
        {error && <p className="error">Could not load shipments: {error.message}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && list.length === 0 && (
          <p className="empty">{done ? "No completed shipments yet." : "No vaccines in transit. Use New shipment when vaccines leave your store."}</p>
        )}
        {groups.filter(([, , l]) => l.length > 0).map(([key, title, l]) => (
          <section key={key} className={`inc-group ship-group-${key}`}>
            <h2 className="inc-group-h">{title} <span className="count">{l.length}</span></h2>
            <PagedList items={l} label={title} getKey={(c) => c.id} selectedKey={id}
                       render={(c) => <Row key={c.id} c={c} selected={c.id === id} onOpen={() => navigate(`/consignments/${c.id}${qs}`)} />} />
          </section>
        ))}
      </div>
      {id && (
        <div className="detail-pane">
          <ShipmentDetail key={id} id={id} onClose={() => navigate(`/consignments${qs}`)} />
        </div>
      )}
      {creating && <NewShipment onClose={() => setCreating(false)} onDone={(cid) => { setCreating(false); navigate(`/consignments/${cid}`); }} />}
    </div>
  );
}

const GROUP_NAME = { facility: "Leaving from", lga: "LGA", state: "State" };
const BREAKDOWN = { facility: "By where they left from", lga: "By LGA", state: "By state" };
const HOLDS = ["facility_oic", "outreach_lead", "driver", "lga_officer", "state_officer"];

function Figures({ f, role }) {
  const o = f.overall;
  return (
    <>
      <div className="summary ship-summary">
        <Fig value={o.in_transit} label="in transit" note={`${o.completed} completed in the last ${f.days} days`} />
        {HOLDS.includes(role) && <Fig value={o.for_you} label="waiting for you to confirm" tone={o.for_you > 0 ? "attention" : ""} />}
        <Fig value={o.awaiting} label="handed over, awaiting confirmation"
             note={o.refused > 0 ? `and ${o.refused} refused, back with the sender` : "receiver has not confirmed yet"} />
        <Fig value={o.overdue} label="not confirmed in time" tone={o.overdue > 0 ? "critical" : ""}
             note="a custody gap incident is raised" />
        <Fig value={o.with_incidents} label="with open incidents" tone={o.with_incidents > 0 ? "warning" : ""} />
        <Fig value={o.silent} label="logger not reporting" note="temperature not being watched" />
      </div>
      {f.group && f.rows.length > 0 && (
        <details className="ship-breakdown" open={f.group !== "facility"}>
          <summary>{BREAKDOWN[f.group]}</summary>
          <div className="table-wrap"><table className="table">
            <thead><tr>
              <th>{GROUP_NAME[f.group]}</th><th className="num">In transit</th><th className="num">Awaiting confirmation</th>
              <th className="num">Not confirmed in time</th><th className="num">Refused</th>
              <th className="num">With open incidents</th><th className="num">Logger not reporting</th>
              <th className="num">Completed ({f.days} days)</th>
            </tr></thead>
            <tbody>
              {f.rows.map((r) => (
                <tr key={r.key}>
                  <th scope="row">{r.label}</th>
                  <td className="num">{r.in_transit}</td>
                  <td className="num">{r.awaiting}</td>
                  <td className={`num ${r.overdue ? "cell-bad" : ""}`}>{r.overdue}</td>
                  <td className={`num ${r.refused ? "cell-warn" : ""}`}>{r.refused}</td>
                  <td className={`num ${r.with_incidents ? "cell-warn" : ""}`}>{r.with_incidents}</td>
                  <td className="num">{r.silent}</td>
                  <td className="num">{r.completed}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </details>
      )}
    </>
  );
}

function Fig({ value, label: l, note, tone = "" }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <span className="stat-v">{value}</span>
      <span className="stat-l">{l}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}

// Who has it, in a few words: "With Musa Ali (driver)", "Handed to Musa Ali, confirm by 14:30".
function custodyLine(c) {
  if (c.completed_at) return `Completed ${time(c.completed_at)}`;
  if (c.state === "rejected" && c.handoff) return `Rejected by ${c.handoff.to_name}; still with ${c.handoff.from_name}`;
  if (c.state === "in_handoff" && c.handoff) {
    const late = new Date(c.handoff.window_ends_at) < Date.now();
    return `Handed to ${c.handoff.to_name}, ${late ? "not confirmed in time" : `to confirm by ${clock(c.handoff.window_ends_at)}`}`;
  }
  return c.can_complete ? "With you" : `With ${who(c.holder_name, c.holder_role)}`;
}

function TempLine({ c }) {
  if (!c.logger) return <span className="muted">No logger</span>;
  if (c.temperature == null) return <span className="muted">No reading yet</span>;
  return <span>{temp(c.temperature)} <span className="muted">{ago(c.reading_time)}</span></span>;
}

function Row({ c, selected, onOpen }) {
  const el = useRef(null);
  useEffect(() => {
    if (selected) el.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const late = c.state === "in_handoff" && c.handoff && new Date(c.handoff.window_ends_at) < Date.now();
  return (
    <li ref={el}>
      <button className={`inc-row ship-row ${selected ? "selected" : ""}`} onClick={onOpen} aria-current={selected || undefined}>
        <span className="ir-sev">{c.status ? <Status status={c.status} compact /> : <span className="muted" title="No logger">–</span>}</span>
        <span className="ir-main">
          <span className="ir-title">{doses(c)} <span className="ir-no">#{c.consignment_no}</span></span>
          <span className="ir-where">{c.origin_name} to {c.destination}</span>
        </span>
        <span className="ir-col">{custodyLine(c)}</span>
        <span className="ir-col"><TempLine c={c} /></span>
        <span className="ir-flags">
          {c.can_confirm && <Flag tone="warn">Confirm receipt</Flag>}
          {late && <Flag tone="bad">Not confirmed</Flag>}
          {c.open_incidents > 0 && <Flag tone={c.severity === "critical" ? "bad" : "warn"}>{c.open_incidents} incident{c.open_incidents > 1 ? "s" : ""}</Flag>}
        </span>
      </button>
    </li>
  );
}

// The plain-words summary at the top of the details: where it is, who is responsible, and its temperature.
function summary(c) {
  const h = c.handoff;
  let head;
  let detail;
  if (c.completed_at) {
    head = `Delivered: completed ${time(c.completed_at)}.`;
    detail = "Custody and the logger's assignment have ended.";
  } else if (c.state === "rejected" && h) {
    const sender = c.can_cancel ? "you" : h.from_name;
    head = `${h.to_name} refused it. It is still with ${sender}.`;
    detail = `${c.can_cancel ? "You need" : `${h.from_name} needs`} to withdraw the handover, then hand it to someone else or keep it.`;
  } else if (c.state === "in_handoff" && h) {
    const late = new Date(h.window_ends_at) < Date.now();
    const sender = c.can_cancel ? "You remain" : `${h.from_name} remains`;
    head = c.can_confirm ? `${h.from_name} has handed it to you. Confirm once you have it.`
      : `Handed to ${who(h.to_name, h.to_role)}, waiting for them to confirm receipt.`;
    detail = late
      ? `Receipt was not confirmed by ${time(h.window_ends_at)}, so a custody gap incident has been raised. ${sender} responsible until it is confirmed.`
      : `Released ${time(h.released_at)}; to be confirmed by ${clock(h.window_ends_at)}. ${sender} responsible until then.`;
  } else {
    head = `With ${c.can_complete ? "you" : who(c.holder_name, c.holder_role)}${c.held_since ? ` since ${time(c.held_since)}` : ""}.`;
    detail = `Going to ${c.destination}.`;
  }
  const lim = `safe range ${c.lower_limit ?? 2}–${c.upper_limit ?? 8} °C`;
  let reading;
  let tone = "ok";
  if (!c.logger) {
    reading = "No temperature logger travels with it, so its temperature is not watched.";
    tone = "past";
  } else if (c.completed_at && c.readings?.length) {
    const t = c.readings.map((r) => Number(r.temp));
    const [mn, mx] = [Math.min(...t), Math.max(...t)];
    const out = mx > Number(c.upper_limit) || mn < Number(c.lower_limit);
    reading = `On the way its temperature ranged from ${temp(mn)} to ${temp(mx)} (${lim})${out ? ", going outside the safe range" : ", within the safe range"}.`;
  } else if (c.temperature == null) {
    reading = c.completed_at ? `Its logger (${c.logger}) sent no readings on this trip.` : `Its logger (${c.logger}) has not sent a reading yet.`;
    tone = "past";
  } else {
    const t = Number(c.temperature);
    const state = t > Number(c.upper_limit) ? "too warm" : t < Number(c.lower_limit) ? "too cold" : null;
    reading = `${c.status === "stale" ? "Its logger has stopped reporting. The last" : "Last"} reading was ${temp(t)}, ${ago(c.reading_time)} (${lim})${state ? `: ${state}` : ""}.`;
    if (state) tone = "critical";
    else if (c.status === "stale") tone = "past";
  }
  if (c.severity) tone = c.severity;
  if (c.completed_at) tone = "past";
  const issues = c.open_incidents > 0
    ? `It has ${c.open_incidents} open incident${c.open_incidents > 1 ? "s" : ""}: see Incidents below.` : null;
  return { head, detail, reading, issues, tone };
}

function ShipmentDetail({ id, onClose }) {
  const { data: c, error, reload } = useApi(`/consignments/${id}`, KINDS);
  const title = useRef(null);
  useEffect(() => { if (c) title.current?.focus(); }, [c?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error) return <div className="detail"><p className="error">{error.status === 403 ? "You do not have access to this shipment." : error.message}</p></div>;
  if (!c) return <div className="detail"><p className="muted">Loading…</p></div>;
  const s = summary(c);
  const icon = s.tone === "ok" ? "✓" : s.tone === "past" ? "○" : STATUS[s.tone]?.symbol;
  return (
    <div className="detail">
      <div className="detail-head">
        <div>
          <h2 ref={title} tabIndex={-1}>Shipment {c.consignment_no}</h2>
          <p className="detail-where">{doses(c)}, from {c.origin_name} to {c.destination}</p>
        </div>
        <button className="icon-btn detail-close" onClick={onClose} aria-label="Close details">✕</button>
      </div>
      <div className={`wrong wrong-${s.tone}`}>
        <span className="wrong-icon" aria-hidden="true">{icon}</span>
        <p className="wrong-head">{s.head}</p>
        <p className="wrong-detail">{s.detail}</p>
        <p className="wrong-detail">{s.reading}</p>
        {s.issues && <p className="wrong-detail"><strong>{s.issues}</strong></p>}
      </div>
      <CustodyActions c={c} reload={reload} />
      <section className="detail-sec">
        <h3>Where it is</h3>
        <div className="detail-split">
          <dl className="facts">
            <dt>From</dt><dd>{c.origin_name}</dd>
            <dt>Going to</dt><dd>{c.destination}</dd>
            <dt>Expected travel time</dt><dd>{duration(c.expected_transit_min)}</dd>
            <dt>Temperature logger</dt><dd>{c.logger || "none"}</dd>
            <dt>Set out</dt><dd>{time(c.created_at)}</dd>
          </dl>
          <MiniMap inc={{ location: c.location, consignment_id: c.id, pin_status: c.status || "no_data" }} />
        </div>
      </section>
      <section className="detail-sec">
        <h3>Temperature since it set out</h3>
        <TempChart readings={c.readings} c={c} />
      </section>
      <section className="detail-sec">
        <h3>Journey</h3>
        <Journey c={c} />
      </section>
      <section className="detail-sec">
        <h3>Incidents</h3>
        {c.incidents.length === 0 ? <p className="muted">None. Nothing has gone wrong with this shipment.</p> : (
          <ul className="inc-list">
            {c.incidents.map((i) => (
              <li key={i.incident_no}>
                <Status status={i.severity === "critical" ? "critical" : "warning"} compact />
                <Link to={`/incidents/${i.incident_no}`}>{label(TYPES, i.type)} #{i.incident_no}</Link>
                <span className="muted">{label(STATES, i.state)}, raised {time(i.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

const STEP = {
  create: (e, c) => `Set out from ${c.origin_name}, held by ${who(e.actor_name, e.actor_role)}`,
  release: (e) => `${e.actor_name} handed it to ${who(e.to_name, e.to_role)}`,
  confirm: (e) => `${who(e.actor_name, e.actor_role)} confirmed receipt and now holds it`,
  reject: (e) => `${e.actor_name} refused it${e.reason ? `: ${e.reason}` : ""}`,
  cancel: (e) => `${e.actor_name} withdrew the handover${e.reason ? `: ${e.reason}` : ""}`,
  expire: () => "Receipt was not confirmed in time; a custody gap incident was raised",
  complete: (e) => `${e.actor_name} marked it completed (delivered or used)`,
};

function Journey({ c }) {
  return (
    <ol className="timeline">
      {c.events.map((e) => (
        <li key={e.seq} className={`tl-${e.type === "confirm" || e.type === "complete" ? "acknowledged" : e.type === "expire" || e.type === "reject" ? "escalated" : e.type}`}>
          <time>{time(e.occurred_at)}</time>
          <span>{(STEP[e.type] || (() => e.type))(e, c)}</span>
        </li>
      ))}
    </ol>
  );
}

function TempChart({ readings, c }) {
  if (!c.logger) return <p className="muted">No temperature logger travels with this shipment.</p>;
  if (!readings.length) return <p className="muted">No readings from its logger yet.</p>;
  const data = readings.map((r) => ({ t: new Date(r.t).getTime(), temp: Number(r.temp) }));
  const temps = data.map((d) => d.temp);
  const up = Number(c.upper_limit ?? 8);
  const low = Number(c.lower_limit ?? 2);
  const lo = Math.floor(Math.min(...temps, low) - 1);
  const hi = Math.ceil(Math.max(...temps, up) + 1);
  const days = (data.at(-1).t - data[0].t) > 36 * 3600 * 1000;      // a trip over several days: show dates
  const tick = (t) => (days ? new Date(t).toLocaleDateString("en-GB", { timeZone: "Africa/Lagos", day: "numeric", month: "short" }) : clock(t));
  return (
    <div className="chart" role="img" aria-label={`Temperature from ${time(data[0].t)} to ${time(data.at(-1).t)}, between ${Math.min(...temps).toFixed(1)} and ${Math.max(...temps).toFixed(1)} °C`}>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 10, right: 24, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tick} minTickGap={24} stroke="var(--muted)" fontSize={12} />
          <YAxis domain={[lo, hi]} unit=" °C" stroke="var(--muted)" fontSize={12} width={60} />
          <ReferenceLine y={up} stroke="#dc2626" strokeDasharray="4 3" label={{ value: `too warm above ${up} °C`, position: "insideTopRight", fontSize: 11, fill: "#b91c1c" }} />
          <ReferenceLine y={low} stroke="#0072B2" strokeDasharray="4 3" label={{ value: `too cold below ${low} °C`, position: "insideBottomRight", fontSize: 11, fill: "#0072B2" }} />
          <Tooltip labelFormatter={(t) => time(t)} formatter={(v) => [`${v.toFixed(1)} °C`, "Temperature"]} />
          <Line type="monotone" dataKey="temp" stroke="var(--ink)" dot={false} strokeWidth={2} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function CustodyActions({ c, reload }) {
  const { data: lk } = useApi(c.can_release ? "/lookups" : null);
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState(null);
  const run = async (path, body, done) => {
    setMsg(null);
    try {
      await api(`/consignments/${c.id}/${path}`, { method: "POST", body });
      setMsg({ ok: true, text: done });
      setReason("");
      reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  };
  const any = c.can_release || c.can_confirm || c.can_reject || c.can_cancel || c.can_complete;
  if (!any && !msg) return null;
  return (
    <section className="detail-actions">
      <h3>What you can do</h3>
      {c.can_confirm && (
        <p className="muted">Confirm only once you have the vaccines in hand. Refuse them if they are unfit, for example if the vial monitors have changed colour.</p>
      )}
      {c.can_release && (
        <>
          <div className="field-row">
            <label className="field"><span>Hand over to</span>
              <select value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">Choose who receives it…</option>
                <optgroup label="Health facilities">
                  {lk?.receivers.filter((r) => r.role === "facility_oic").map((r) => <option key={r.id} value={r.id}>{r.facility_name || "Facility"}: {r.full_name}</option>)}
                </optgroup>
                <optgroup label="Outreach teams and drivers">
                  {lk?.receivers.filter((r) => r.role !== "facility_oic").map((r) => <option key={r.id} value={r.id}>{who(r.full_name, r.role)}</option>)}
                </optgroup>
              </select></label>
            <button className="btn primary" disabled={!to} onClick={() => run("release", { to_user: to }, "Handed over. They have been sent an SMS with a code to confirm receipt.")}>Hand over</button>
          </div>
          <p className="hint">They get an SMS with a code to confirm receipt. You stay responsible until they confirm. To move it on again (for example from a facility to an outreach team), the new holder hands it over in turn.</p>
        </>
      )}
      {c.can_confirm && <button className="btn primary" onClick={() => run("confirm", undefined, "Receipt confirmed. You now hold this shipment.")}>Confirm receipt</button>}
      {(c.can_reject || c.can_cancel) && (
        <label className="field"><span>{c.can_reject ? "Reason for refusing" : "Reason for withdrawing"}</span>
          <input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} /></label>
      )}
      <div className="btn-row">
        {c.can_reject && <button className="btn" disabled={!reason.trim()} onClick={() => run("reject", { reason }, "Refused. It stays with the sender, who has been told.")}>Refuse</button>}
        {c.can_cancel && <button className="btn" disabled={!reason.trim()} onClick={() => run("cancel", { reason }, "Handover withdrawn. You still hold this shipment.")}>Withdraw handover</button>}
        {c.can_complete && <button className="btn" onClick={() => run("complete", undefined, "Marked completed.")}>Mark completed (delivered or used)</button>}
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
    </section>
  );
}

// A dialog over the page (P21). The native <dialog> keeps keyboard focus inside and closes on Escape.
function NewShipment({ onClose, onDone }) {
  const { data: lk } = useApi("/lookups");
  const box = useRef(null);
  const [f, setF] = useState({ origin_facility_id: "", product_id: "", quantity: 100, destination: "",
                               hours: 2, minutes: 0, device_serial: "" });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const d = box.current;
    d.showModal();
    const close = () => onClose();
    d.addEventListener("close", close);
    return () => d.removeEventListener("close", close);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const transit = Number(f.hours) * 60 + Number(f.minutes);
  const submit = async (e) => {
    e.preventDefault();
    setErr(null);
    if (transit < 1) return setErr("Enter how long the journey should take.");
    setBusy(true);
    try {
      const r = await api("/consignments", { method: "POST", body: {
        origin_facility_id: f.origin_facility_id, product_id: f.product_id, quantity: Number(f.quantity),
        destination: f.destination, expected_transit_min: transit, device_serial: f.device_serial || null } });
      onDone(r.consignment_id);
    } catch (x) {
      setErr(x.message);
      setBusy(false);
    }
    return undefined;
  };
  return (
    <dialog ref={box} className="modal" aria-labelledby="new-shipment-title"
            onClick={(e) => { if (e.target === box.current) box.current.close(); }}>
      <form className="modal-body" onSubmit={submit}>
        <div className="modal-head">
          <h2 id="new-shipment-title">New shipment</h2>
          <button type="button" className="btn-quiet" onClick={() => box.current.close()} aria-label="Close">✕</button>
        </div>
        <p className="muted modal-intro">Record vaccines leaving your store. You will hold them until you hand them over.</p>
        {!lk ? <p className="muted">Loading…</p> : (
          <>
            <label className="field"><span>Leaving from</span>
              <select required value={f.origin_facility_id} onChange={set("origin_facility_id")}>
                <option value="">Choose a store…</option>{lk.origins.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select></label>
            <div className="field-pair">
              <label className="field"><span>Vaccine</span>
                <select required value={f.product_id} onChange={set("product_id")}>
                  <option value="">Choose…</option>{lk.products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select></label>
              <label className="field"><span>Number of doses</span>
                <input type="number" min={1} required value={f.quantity} onChange={set("quantity")} /></label>
            </div>
            <label className="field"><span>Going to</span>
              <input required maxLength={200} value={f.destination} onChange={set("destination")} placeholder="e.g. Outreach session, Kofar Waika" /></label>
            <fieldset className="field">
              <legend>Expected travel time</legend>
              <div className="field-pair time-pair">
                <label><input type="number" min={0} max={72} value={f.hours} onChange={set("hours")} aria-label="Hours" /> hours</label>
                <label><input type="number" min={0} max={59} step={5} value={f.minutes} onChange={set("minutes")} aria-label="Minutes" /> minutes</label>
              </div>
              <span className="hint">The receiver must confirm within this time plus 30 minutes, or a custody gap incident is raised.</span>
            </fieldset>
            <label className="field"><span>Temperature logger</span>
              <select value={f.device_serial} onChange={set("device_serial")}>
                <option value="">None</option>{lk.loggers.map((d) => <option key={d.serial_no} value={d.serial_no}>{d.serial_no}</option>)}
              </select>
              <span className="hint">The device packed with the vaccines that reports their temperature and position.</span></label>
          </>
        )}
        {err && <p className="error" role="alert">{err}</p>}
        <div className="modal-foot">
          <button type="button" className="btn" onClick={() => box.current.close()}>Cancel</button>
          <button className="btn primary" type="submit" disabled={!lk || busy}>{busy ? "Creating…" : "Create shipment"}</button>
        </div>
      </form>
    </dialog>
  );
}
