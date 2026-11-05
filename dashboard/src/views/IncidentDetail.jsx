// One incident: what happened and when, the readings behind it, its timeline of notifications,
// acknowledgements and escalations, and the actions open to the user (acknowledge, record action,
// resolve; or, for a reviewer, close or reopen).
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis,
         YAxis } from "recharts";

import { api, useApi } from "../api.js";
import { Flag, Severity } from "../components/Status.jsx";
import { ago, clock, duration, EQUIPMENT, label, place, STATES, TIERS, time, TYPES } from "../format.js";

const TEMPERATURE = ["heat_excursion", "freeze"];

export default function IncidentDetail() {
  const { ref } = useParams();
  const { data: inc, error, reload } = useApi(`/incidents/${ref}`, ["incident", "notification"]);
  const { data: readings } = useApi(`/incidents/${ref}/readings`, ["device_status"], 120000);

  if (error) return <div className="page"><p className="error">{error.status === 403 ? "You do not have access to this incident." : `Could not load incident: ${error.message}`}</p></div>;
  if (!inc) return <div className="page"><p className="muted">Loading…</p></div>;

  const minutes = (a, b) => (a && b ? (new Date(b) - new Date(a)) / 60000 : null);
  return (
    <div className="page">
      <p className="crumbs"><Link to="/incidents">Incidents</Link> › #{inc.incident_no}</p>
      <div className="page-head">
        <h1>
          <Severity severity={inc.severity} /> #{inc.incident_no} {label(TYPES, inc.type)}
        </h1>
        <div className="flags">
          <Flag>{label(STATES, inc.state)}</Flag>
          {inc.notification_failed && <Flag tone="bad" title="An SMS about this incident could not be delivered">SMS failed</Flag>}
          {inc.overdue && <Flag tone="bad">Overdue</Flag>}
          {inc.mode === "retrospective" && <Flag title="The event had ended before it was detected">Retrospective</Flag>}
          {inc.reopen_count > 0 && <Flag tone="warn">Reopened {inc.reopen_count}×</Flag>}
        </div>
      </div>

      <div className="grid2">
        <section className="panel">
          <h2>Details</h2>
          <dl className="facts">
            <dt>Where</dt><dd>{place(inc)} <span className="muted">· {inc.jurisdiction_name}</span></dd>
            <dt>Logger</dt><dd>{inc.device_serial || "—"} {inc.equipment_type && <span className="muted">({label(EQUIPMENT, inc.equipment_type)})</span>}</dd>
            {inc.consignment_no && <><dt>Consignment</dt><dd><Link to={`/consignments/${inc.consignment_id}`}>{inc.consignment_no}</Link></dd></>}
            <dt>Event</dt>
            <dd>
              {time(inc.event_start)} – {inc.event_end ? time(inc.event_end) : <b>ongoing</b>}
              <span className="muted"> ({duration(minutes(inc.event_start, inc.event_end || new Date()))})</span>
            </dd>
            {inc.peak_value != null && TEMPERATURE.includes(inc.type) && <><dt>Peak</dt><dd>{Number(inc.peak_value).toFixed(1)} °C</dd></>}
            <dt>Detected</dt>
            <dd>{time(inc.detected_at)} <span className="muted">({duration(minutes(inc.event_start, inc.detected_at))} after it began)</span></dd>
            <dt>Owner</dt><dd>{inc.owner_name}</dd>
            <dt>Now with</dt><dd>{label(TIERS, inc.current_tier)}</dd>
            <dt>Acknowledged</dt>
            <dd>{inc.acknowledged_at ? <>{time(inc.acknowledged_at)} by {inc.acknowledged_by_name} <span className="muted">({duration(minutes(inc.created_at, inc.acknowledged_at))} after creation)</span></> : "not yet"}</dd>
            {inc.resolved_at && <><dt>Resolved</dt><dd>{time(inc.resolved_at)} by {inc.resolved_by_name}</dd></>}
            {inc.closed_at && <><dt>Closed</dt><dd>{time(inc.closed_at)} by {inc.closed_by_name}</dd></>}
            <dt>Condition</dt><dd>{inc.event_end ? (inc.condition_cleared ? "Back to normal" : "Ended") : <b>Ongoing</b>}{inc.episodes > 1 && <span className="muted"> ({inc.episodes} episodes)</span>}</dd>
          </dl>
        </section>
        <Actions inc={inc} reload={reload} />
      </div>

      <section className="panel">
        <h2>Readings</h2>
        <ReadingsChart readings={readings} inc={inc} />
      </section>

      <section className="panel">
        <h2>Timeline</h2>
        <Timeline inc={inc} />
      </section>
    </div>
  );
}

function Actions({ inc, reload }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  const run = async (path, body, done) => {
    setBusy(true);
    setMessage(null);
    try {
      await api(`/incidents/${inc.incident_no}/${path}`, { method: "POST", body });
      setNote("");
      setMessage({ ok: true, text: done });
      reload();
    } catch (e) {
      setMessage({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  if (!inc.can_act && !inc.can_review) {
    return (
      <section className="panel">
        <h2>Actions</h2>
        <p className="muted">
          {inc.state === "closed" ? "This incident is closed."
            : inc.state === "resolved" ? "Resolved; awaiting review by an LGA officer or above who did not resolve it."
            : "Only the owner, or an officer it has been escalated to, can act on this incident."}
        </p>
        {message && <p className={message.ok ? "ok" : "error"} role="status">{message.text}</p>}
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>Actions</h2>
      {inc.can_act && !inc.acknowledged_at && (
        <button className="btn primary" disabled={busy} onClick={() => run("acknowledge", undefined, "Acknowledged.")}>
          Acknowledge
        </button>
      )}
      <label className="field">
        <span>{inc.can_review ? "Review note (needed to reopen)" : "What was done"}</span>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={1000}
                  placeholder={inc.can_review ? "e.g. Checked readings; vaccines moved in time" : "e.g. Moved vaccines to the backup fridge"} />
      </label>
      <div className="btn-row">
        {inc.can_act && (
          <>
            <button className="btn" disabled={busy || !note.trim()} onClick={() => run("actions", { note }, "Action recorded.")}>
              Record action
            </button>
            <button className="btn primary" disabled={busy || !note.trim()} onClick={() => run("resolve", { note }, "Resolved and sent for review.")}>
              Resolve
            </button>
          </>
        )}
        {inc.can_review && (
          <>
            <button className="btn primary" disabled={busy} onClick={() => {
              if (!inc.event_end && !window.confirm("The condition is still ongoing. Close the incident anyway?")) return;
              run("close", { note }, "Closed.");
            }}>Close incident</button>
            <button className="btn" disabled={busy || !note.trim()} onClick={() => run("reopen", { reason: note }, "Reopened; the owner has been told.")}>
              Reopen
            </button>
          </>
        )}
      </div>
      {inc.can_act && <p className="hint">Resolving needs a note of what was done. Closing is done by a reviewer.</p>}
      {message && <p className={message.ok ? "ok" : "error"} role="status">{message.text}</p>}
    </section>
  );
}

function ReadingsChart({ readings, inc }) {
  if (!readings) return <p className="muted">Loading…</p>;
  if (readings.readings.length === 0) return <p className="muted">No readings for this incident.</p>;
  const data = readings.readings.map((r) => ({ t: new Date(r.t).getTime(), temp: Number(r.temp), power: r.power_ok }));
  const powerOff = [];                                  // spans with mains power off
  for (const d of data) {
    const last = powerOff.at(-1);
    if (d.power === false) {
      if (last && last.open) last.x2 = d.t;
      else powerOff.push({ x1: d.t, x2: d.t, open: true });
    } else if (last) last.open = false;
  }
  const lim = readings.limits || {};
  const start = new Date(inc.event_start).getTime();
  const end = inc.event_end ? new Date(inc.event_end).getTime() : data.at(-1).t;
  const temps = data.map((d) => d.temp);
  const lo = Math.floor(Math.min(...temps, Number(lim.freeze_limit ?? 0)) - 1);
  const hi = Math.ceil(Math.max(...temps, Number(lim.upper_limit ?? 8)) + 1);
  return (
    <>
      <div className="chart" role="img" aria-label={`Temperature from ${clock(data[0].t)} to ${clock(data.at(-1).t)}; peak ${Math.max(...temps).toFixed(1)} °C`}>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={data} margin={{ top: 10, right: 24, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={clock}
                   stroke="var(--muted)" fontSize={12} />
            <YAxis domain={[lo, hi]} unit=" °C" stroke="var(--muted)" fontSize={12} width={60} />
            <ReferenceArea x1={start} x2={end} fill="#D55E00" fillOpacity={0.08} />
            {powerOff.map((p) => <ReferenceArea key={p.x1} x1={p.x1} x2={p.x2} y1={lo} y2={lo + (hi - lo) * 0.06} fill="#6B6B6B" fillOpacity={0.6} label={{ value: "power off", position: "insideLeft", fontSize: 10, fill: "#fff" }} />)}
            {lim.upper_limit != null && <ReferenceLine y={Number(lim.upper_limit)} stroke="#D55E00" strokeDasharray="4 3" label={{ value: `upper ${lim.upper_limit} °C`, position: "insideTopRight", fontSize: 11, fill: "#D55E00" }} />}
            {lim.lower_limit != null && <ReferenceLine y={Number(lim.lower_limit)} stroke="#0072B2" strokeDasharray="4 3" label={{ value: `lower ${lim.lower_limit} °C`, position: "insideBottomRight", fontSize: 11, fill: "#0072B2" }} />}
            {lim.freeze_limit != null && <ReferenceLine y={Number(lim.freeze_limit)} stroke="#56B4E9" strokeDasharray="2 3" />}
            <Tooltip labelFormatter={(t) => time(t)} formatter={(v) => [`${v.toFixed(1)} °C`, "Temperature"]} />
            <Line type="monotone" dataKey="temp" stroke="var(--ink)" dot={false} strokeWidth={2} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="hint">Shaded: the event{powerOff.length ? "; grey band: mains power off" : ""}. Dashed lines: the limits that applied. Readings are by the time they were taken{readings.bucket_s > 60 ? `, averaged over ${Math.round(readings.bucket_s / 60)} minutes` : ""}.</p>
    </>
  );
}

const CHANNEL = { sms: "by SMS", dashboard: "on the dashboard", system: "" };

function describe(a, inc) {
  const d = a.details || {};
  const by = a.actor_name ? ` by ${a.actor_name}` : "";
  const via = CHANNEL[a.channel] ? ` ${CHANNEL[a.channel]}` : "";
  switch (a.action) {
    case "created": return `Incident created; owner ${inc.owner_name}`;
    case "notified": return `Owner notified`;
    case "escalated": return `Escalated to ${label(TIERS, d.tier)}${d.reason === "undelivered" ? " (SMS to the previous tier undelivered)" : ""}`;
    case "acknowledged": return `Acknowledged${by}${via}${d.late ? " (after escalation)" : ""}`;
    case "action_recorded": return `Action recorded${by}${via}: ${d.note}`;
    case "resolved": return `Resolved${by}${via}: ${d.note}${d.condition_cleared === false ? " (the condition had not yet cleared)" : ""}`;
    case "reopened": return `Reopened${by}${d.note ? `: ${d.note}` : d.reason === "recurrence" ? " (the condition recurred)" : ""}`;
    case "closed": return `Closed${by}${d.note ? `: ${d.note}` : ""}`;
    case "reminder": return "Reminder sent";
    case "overdue": return "Marked overdue";
    case "notification_failed": return "SMS could not be delivered";
    case "condition_cleared": return "Readings back within limits";
    case "updated": return d.new_episode ? "The condition occurred again; added to this incident"
      : d.joined ? "A further event added to this incident" : "Event updated with new readings";
    case "linked": return "Linked to an earlier incident";
    default: return label({}, a.action);
  }
}

function Timeline({ inc }) {
  const [all, setAll] = useState(false);
  const items = [
    ...inc.actions.filter((a) => all || a.action !== "delivery_report").map((a) => ({
      t: a.occurred_at, key: `a${a.id}`, kind: a.action, text: describe(a, inc),
    })),
    ...inc.notifications.filter((n) => n.channel === "sms" && (all || n.kind !== "reply")).map((n) => ({
      t: n.created_at, key: `n${n.id}`, kind: "sms",
      text: `SMS (${n.kind}) to ${n.recipient_name}: ${n.status}${n.attempts > 1 ? ` after ${n.attempts} attempts` : ""}${n.failure_reason ? ` (${n.failure_reason})` : ""}`,
      body: n.body,
    })),
  ].sort((a, b) => new Date(a.t) - new Date(b.t));
  return (
    <>
      <ol className="timeline">
        {items.map((i) => (
          <li key={i.key} className={`tl-${i.kind}`}>
            <time dateTime={i.t}>{time(i.t)}</time>
            <span>{i.text}{i.body && <span className="tl-body">“{i.body}”</span>}</span>
          </li>
        ))}
      </ol>
      <label className="check">
        <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
        Show every audit record and SMS reply
      </label>
      <p className="hint">Last updated {ago(items.at(-1)?.t)}.</p>
    </>
  );
}
