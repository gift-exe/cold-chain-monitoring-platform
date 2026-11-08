// Performance view (FR10.3): how quickly incidents are responded to and resolved, measured from incident
// creation as the escalation limits are, for the user's area (Table 3.21). Redesigned in E9 (P15, P16):
// plain names in place of MTTA/MTTR, the headline figures first, a trend over time, a ranking that puts
// the slowest (and those that have not responded at all) first, and a view by responsible officer for
// supervisors. Each headline figure is compared with the period before; the trend shows the time allowed
// before escalation; a ranking row opens that group's incidents. The full figures stay in a table below. An administrator's performance view is the system
// status, on the administration page.
import { useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useApi } from "../api.js";
import { duration, label, ROLES, TIERS } from "../format.js";

const GROUPS_BY_ROLE = {
  facility_oic: ["facility", "tier"],
  lga_officer: ["facility", "officer", "tier"],
  state_officer: ["lga", "facility", "officer", "tier"],
  national_officer: ["state", "lga", "facility", "officer", "tier"],
};
const GROUP_NAMES = { facility: "Facility", lga: "LGA", state: "State", officer: "Responsible officer",
                      tier: "Level that responded" };
const PERIODS = [[7, "7 days"], [30, "30 days"], [90, "90 days"], [365, "12 months"]];
const MODES = [["operational", "Caught while happening"], ["retrospective", "Found after they ended"], ["all", "All incidents"]];
const RESPOND = "#16784a";
const RESOLVE = "#7c5cc4";
const TZ = "Africa/Lagos";
const LIMIT = "#9a3412";

const n = (v) => (v == null ? null : Number(v));
const pct = (part, whole) => (whole ? Math.round((100 * part) / whole) : null);

export default function Performance({ me }) {
  const groups = GROUPS_BY_ROLE[me.role] || ["facility"];
  const [group, setGroup] = useState(groups[0]);
  const [days, setDays] = useState(7);
  const [mode, setMode] = useState("operational");
  const { data, error } = useApi(`/performance?group=${group}&days=${days}&mode=${mode}`, ["incident"], 300000);
  const o = data?.overall;

  return (
    <div className="page perf-page">
      <div className="page-head">
        <h1>Response performance</h1>
        <div className="tabs" role="group" aria-label="Period">
          {PERIODS.map(([d, t]) => (
            <button key={d} className={days === d ? "on" : ""} aria-pressed={days === d} onClick={() => setDays(d)}>{t}</button>
          ))}
        </div>
        <label className="perf-mode">Incidents{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            {MODES.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
          </select>
        </label>
      </div>
      {error && <p className="error">Could not load the figures: {error.message}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {o && o.incidents === 0 && <p className="empty">No incidents in the last {PERIODS.find(([d]) => d === days)[1]}.</p>}
      {o && o.incidents > 0 && (
        <>
          <Headline o={o} prev={data.previous} period={PERIODS.find(([d]) => d === days)[1]} />
          <div className="perf-charts">
            <section className="panel perf-panel">
              <h2>Response times over the period</h2>
              <Trend trend={data.trend} bucket={data.bucket} limits={data.limits} />
            </section>
            <Ranking rows={data.rows} group={group} groups={groups} setGroup={setGroup} />
          </div>
          <details className="perf-table">
            <summary>All figures by {group === "lga" ? "LGA" : GROUP_NAMES[group].toLowerCase()}</summary>
            <FullTable rows={data.rows} group={group} />
          </details>
        </>
      )}
    </div>
  );
}

// "5 min faster than the previous 7 days": green when better, orange when worse, plain when unchanged.
function Change({ cur, prev, kind, period }) {
  const than = `the previous ${period}`;
  if (prev == null || cur == null) return <span className="change">No figures for {than}</span>;
  const d = cur - prev;
  if (kind === "time") {
    if (Math.abs(d) < 1) return <span className="change">Same as {than}</span>;
    return <span className={`change ${d < 0 ? "better" : "worse"}`}>{duration(Math.abs(d))} {d < 0 ? "faster" : "slower"} than {than}</span>;
  }
  if (kind === "pct") {
    if (d === 0) return <span className="change">Same as {than}</span>;
    return <span className={`change ${d > 0 ? "better" : "worse"}`}>{Math.abs(d)} points {d > 0 ? "higher" : "lower"} than {than}</span>;
  }
  if (prev === 0) return <span className="change">None in {than}</span>;
  if (d === 0) return <span className="change">Same as {than}</span>;
  return <span className="change">{Math.abs(d)} {d > 0 ? "more" : "fewer"} than {than}</span>;
}

function Headline({ o, prev, period }) {
  const before = pct(o.before_escalation, o.acknowledged);
  const p = prev || {};
  const had = p.incidents > 0;
  return (
    <div className="summary perf-summary">
      <Stat value={o.incidents} label="incidents raised" note={`${o.critical} critical`}
            change={<Change cur={o.incidents} prev={p.incidents ?? null} period={period} />} />
      <Stat value={duration(n(o.mtta_min))} label="average time to respond"
            note={<>from alert to acknowledgement <abbr title="Mean time to acknowledge">(MTTA)</abbr></>}
            change={<Change cur={n(o.mtta_min)} prev={had ? n(p.mtta_min) : null} kind="time" period={period} />} />
      <Stat value={duration(n(o.mttr_min))} label="average time to resolve"
            note={<>from alert to resolution <abbr title="Mean time to resolve">(MTTR)</abbr></>}
            change={<Change cur={n(o.mttr_min)} prev={had ? n(p.mttr_min) : null} kind="time" period={period} />} />
      <Stat value={before == null ? "—" : `${before}%`} label="responded to before escalation"
            note={`${o.before_escalation} of ${o.acknowledged} acknowledged by the person responsible`}
            change={<Change cur={before} prev={had ? pct(p.before_escalation, p.acknowledged) : null} kind="pct" period={period} />} />
      <Stat value={o.unacknowledged} label="still waiting for a response" tone={o.unacknowledged > 0 ? "attention" : ""}
            note={o.unacknowledged > 0 ? `the oldest has waited ${duration(n(o.longest_wait_min))}` : "none waiting"} />
    </div>
  );
}

function Stat({ value, label: l, note, change, tone = "" }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <span className="stat-v">{value}</span>
      <span className="stat-l">{l}</span>
      <span className="stat-note">{note}</span>
      {change}
    </div>
  );
}

function when(t, bucket, long = false) {
  const d = new Date(t);
  if (bucket === "month") return d.toLocaleDateString("en-GB", { timeZone: TZ, month: "short", year: long ? "numeric" : undefined });
  const day = d.toLocaleDateString("en-GB", { timeZone: TZ, day: "numeric", month: "short" });
  return bucket === "week" && long ? `Week starting ${day}` : day;
}

// Axis steps in round amounts of time (5 min ... 1 day), so the labels read "30 min", "1 h", "2 h".
const STEPS = [5, 10, 15, 30, 60, 120, 180, 240, 360, 720, 1440, 2880, 10080];
const short = (m) => (m < 60 ? `${m} min` : m < 1440 ? `${m / 60} h` : `${m / 1440} d`);
function ticks(points, limit) {
  const top = Math.max(1, (limit || 0) * 1.1, ...points.flatMap((p) => [p.respond || 0, p.resolve || 0]));
  const step = STEPS.find((s) => s * 4 >= top) || Math.ceil(top / 4);
  return Array.from({ length: Math.ceil(top / step) + 1 }, (_, k) => k * step);
}

// Critical and warning incidents have very different time limits (15 min and 24 h before they move up
// from the owner, Table 3.15), so the trend is shown for one severity at a time, with that limit drawn in.
const SEVERITIES = [["critical", "Critical"], ["warning", "Warning"], ["all", "All"]];
const TIER_UP = { owner: "the LGA officer" };

function Trend({ trend, bucket, limits }) {
  const [sev, setSev] = useState("critical");
  const k = sev === "all" ? "min" : sev;
  const points = trend.map((p) => ({
    start: p.start, respond: n(p[`mtta_${k}`]), resolve: n(p[`mttr_${k}`]),
    incidents: sev === "all" ? p.incidents : sev === "critical" ? p.critical : p.incidents - p.critical,
  }));
  const limit = sev === "all" ? null : n(limits?.[sev]);
  const yTicks = ticks(points, limit);
  return (
    <div className="perf-trend">
      <div className="perf-trend-head">
        <p className="panel-note">Average for {sev === "all" ? "" : `${sev} `}incidents raised each {bucket}. Lower is better.</p>
        <div className="seg" role="group" aria-label="Severity">
          {SEVERITIES.map(([v, t]) => (
            <button key={v} className={sev === v ? "on" : ""} aria-pressed={sev === v} onClick={() => setSev(v)}>{t}</button>
          ))}
        </div>
      </div>
      <div className="chart-key">
        <span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={RESPOND} strokeWidth="2" /></svg>Time to respond</span>
        <span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={RESOLVE} strokeWidth="2" strokeDasharray="5 4" /></svg>Time to resolve</span>
        {limit != null && <span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="22" y2="4" stroke={LIMIT} strokeWidth="1.5" strokeDasharray="1 3" strokeLinecap="round" /></svg>Time allowed before it moves up ({duration(limit)})</span>}
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#edf2ef" vertical={false} />
          <XAxis dataKey="start" tickFormatter={(t) => when(t, bucket)} tick={{ fontSize: 12, fill: "#5d6b65" }}
                 axisLine={{ stroke: "#e2e9e5" }} tickLine={false} minTickGap={16} />
          <YAxis ticks={yTicks} domain={[0, yTicks[yTicks.length - 1]]} tickFormatter={short} tick={{ fontSize: 12, fill: "#5d6b65" }} axisLine={false}
                 tickLine={false} width={52} />
          {limit != null && <ReferenceLine y={limit} stroke={LIMIT} strokeWidth={1.5} strokeDasharray="1 3" strokeLinecap="round" ifOverflow="extendDomain" />}
          <Tooltip content={<TrendTip bucket={bucket} sev={sev} />} cursor={{ stroke: "#9aa8a1", strokeDasharray: "3 3" }} />
          <Line name="Time to resolve" dataKey="resolve" stroke={RESOLVE} strokeWidth={2} strokeDasharray="5 4"
                dot={{ r: 4, strokeWidth: 2, fill: "#fff" }} activeDot={{ r: 5 }} isAnimationActive={false} />
          <Line name="Time to respond" dataKey="respond" stroke={RESPOND} strokeWidth={2} 
                dot={{ r: 4, strokeWidth: 2, fill: "#fff" }} activeDot={{ r: 5 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      <p className="panel-note">{sev === "all"
        ? "Pick critical or warning to see the time allowed: the two have different limits."
        : `A ${sev} incident not acknowledged within ${duration(limit)} moves up to ${TIER_UP.owner}. Points above the dotted line mean responses were too slow on average.`}</p>
    </div>
  );
}

function TrendTip({ active, payload, bucket, sev }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="chart-tip">
      <strong>{when(p.start, bucket, true)}</strong>
      <span>{p.incidents} {sev === "all" ? "" : `${sev} `}incident{p.incidents === 1 ? "" : "s"} raised</span>
      {p.incidents > 0 && <>
        <span><i style={{ background: RESPOND }} />Respond: {p.respond == null ? "none acknowledged" : duration(p.respond)}</span>
        <span><i style={{ background: RESOLVE }} />Resolve: {p.resolve == null ? "none resolved" : duration(p.resolve)}</span>
      </>}
    </div>
  );
}

// Slowest first. A group with incidents waiting and none acknowledged has no average at all, which would
// otherwise rank it best: it goes to the top, marked as not responding.
function rank(rows, metric) {
  const key = metric === "respond" ? "mtta_min" : "mttr_min";
  const silent = (r) => r[key] == null && r.unacknowledged > 0;
  return [...rows].sort((a, b) => (silent(b) - silent(a)) || ((n(b[key]) ?? -1) - (n(a[key]) ?? -1))
                                  || b.unacknowledged - a.unacknowledged);
}

function name(r, group) {
  return group === "tier" ? (r.label === "owner" ? "Person responsible" : `${label(TIERS, r.label)} officer`) : r.label;
}

function covers(list) {
  const places = (list || []).filter((p) => p !== "Shipments in transit");
  if (places.length === 0) return "Shipments in transit";
  return places.length <= 2 ? places.join(" and ") : `${places.slice(0, 2).join(", ")} and ${places.length - 2} more`;
}

// The incidents list for one row of the ranking (E9): its active incidents, with the row's name shown
// as the filter. None for a level or for shipments in transit, which are not one place or person.
function incidentsLink(r, group) {
  const by = { lga: "area", state: "area", facility: "facility", officer: "owner" }[group];
  if (!by || r.key === "transit") return null;
  return `/incidents?${new URLSearchParams({ [by]: r.key, name: name(r, group) })}`;
}

function Ranking({ rows, group, groups, setGroup }) {
  const [metric, setMetric] = useState("respond");
  const key = metric === "respond" ? "mtta_min" : "mttr_min";
  const ranked = rank(rows, metric);
  const max = Math.max(1, ...rows.map((r) => n(r[key]) || 0));
  return (
    <section className="panel perf-panel">
      <div className="perf-rank-head">
        <h2>Slowest {metric === "respond" ? "to respond" : "to resolve"} first</h2>
        <label>By{" "}
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            {groups.map((g) => <option key={g} value={g}>{GROUP_NAMES[g]}</option>)}
          </select>
        </label>
      </div>
      <div className="seg" role="group" aria-label="Measure">
        <button className={metric === "respond" ? "on" : ""} aria-pressed={metric === "respond"} onClick={() => setMetric("respond")}>Time to respond</button>
        <button className={metric === "resolve" ? "on" : ""} aria-pressed={metric === "resolve"} onClick={() => setMetric("resolve")}>Time to resolve</button>
      </div>
      <ol className="rank">
        {ranked.map((r) => {
          const v = n(r[key]);
          const done = metric === "respond" ? r.acknowledged : r.resolved;
          const to = incidentsLink(r, group);
          const Row = to ? Link : "div";
          return (
            <li key={r.key}><Row className={`rank-row ${to ? "rank-link" : ""}`} {...(to ? { to } : {})}>
              <span className="rank-name">
                {name(r, group)}
                {group === "officer" && <span className="muted">{label(ROLES, r.role)}, {covers(r.covers)}</span>}
              </span>
              <span className="rank-bar" title={v == null ? "" : `${duration(v)} on average`}>
                {v != null && <span className="rank-fill" style={{ width: `${Math.max(2, (v / max) * 70)}%`, background: metric === "respond" ? RESPOND : RESOLVE }} />}
                <span className="rank-v">
                  {v != null ? duration(v)
                    : r.unacknowledged > 0 ? <span className="rank-none">No response yet</span>
                    : <span className="muted">None {metric === "respond" ? "acknowledged" : "resolved"}</span>}
                </span>
              </span>
              <span className="rank-detail muted">
                {done} of {r.incidents} {metric === "respond" ? "acknowledged" : "resolved"}
                {r.unacknowledged > 0 && <span className="rank-wait">; {r.unacknowledged} not yet acknowledged, the oldest waiting {duration(n(r.longest_wait_min))}</span>}
              </span>
            </Row></li>
          );
        })}
      </ol>
      {group !== "tier" && <p className="panel-note">Select a row to see its active incidents.</p>}
    </section>
  );
}

function FullTable({ rows, group }) {
  const total = rows.reduce((s, r) => s + r.incidents, 0);
  return (
    <div className="table-wrap"><table className="table perf">
      <thead>
        <tr>
          <th>{GROUP_NAMES[group]}</th><th className="num">Incidents</th><th className="num">Critical</th>
          <th className="num">Average time to respond</th><th className="num">Average time to resolve</th>
          <th className="num">Responded before escalation</th><th className="num">Not yet acknowledged</th><th className="num">Oldest waiting</th><th className="num">SMS not delivered</th>
        </tr>
      </thead>
      <tbody>
        {rank(rows, "respond").map((r) => (
          <tr key={r.key}>
            <th scope="row">{name(r, group)}{group === "officer" && <span className="muted"> ({covers(r.covers)})</span>}</th>
            <td className="num">{r.incidents}</td>
            <td className="num">{r.critical}</td>
            <td className="num" title={`Average over ${r.acknowledged} acknowledged`}>{r.mtta_min == null ? <span className="muted">none acknowledged</span> : duration(n(r.mtta_min))}</td>
            <td className="num" title={`Average over ${r.resolved} resolved`}>{r.mttr_min == null ? <span className="muted">none resolved</span> : duration(n(r.mttr_min))}</td>
            <td className="num">{r.acknowledged ? `${pct(r.before_escalation, r.acknowledged)}%` : "—"}</td>
            <td className="num">{r.unacknowledged}</td>
            <td className="num">{r.unacknowledged > 0 ? duration(n(r.longest_wait_min)) : <span className="muted">—</span>}</td>
            <td className="num">{r.notification_failed}</td>
          </tr>
        ))}
      </tbody>
      <tfoot><tr><th>Total</th><td className="num">{total}</td><td colSpan={7}></td></tr></tfoot>
    </table></div>
  );
}
