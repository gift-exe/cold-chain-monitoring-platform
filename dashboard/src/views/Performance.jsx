// Performance view (FR10.3): mean time to acknowledge (MTTA) and to resolve (MTTR), measured from
// incident creation as the escalation limits are, grouped as each role needs (Table 3.21). An
// administrator's performance view is the system status, on the administration page.
import { useState } from "react";

import { useApi } from "../api.js";
import { duration, label, TIERS } from "../format.js";

const GROUPS_BY_ROLE = {
  facility_oic: ["facility", "tier"],
  lga_officer: ["facility", "tier"],
  state_officer: ["lga", "facility", "tier"],
  national_officer: ["state", "lga", "facility", "tier"],
};
const GROUP_NAMES = { facility: "Facility", lga: "LGA", state: "State", tier: "Tier at acknowledgement" };

export default function Performance({ me }) {
  const groups = GROUPS_BY_ROLE[me.role] || ["facility"];
  return <Metrics groups={groups} />;
}

function Metrics({ groups }) {
  const [group, setGroup] = useState(groups[0]);
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState("operational");
  const { data, error } = useApi(`/performance?group=${group}&days=${days}&mode=${mode}`, ["incident"], 300000);
  const rows = data?.rows || [];
  const max = Math.max(1, ...rows.map((r) => Number(r.mtta_min) || 0), ...rows.map((r) => Number(r.mttr_min) || 0));
  const total = rows.reduce((n, r) => n + r.incidents, 0);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Response performance</h1>
      </div>
      <div className="controls">
        <label>By <select value={group} onChange={(e) => setGroup(e.target.value)}>
          {groups.map((g) => <option key={g} value={g}>{GROUP_NAMES[g]}</option>)}
        </select></label>
        <label>Period <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option><option value={365}>Last 12 months</option>
        </select></label>
        <label>Incidents <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="operational">Operational</option><option value="retrospective">Retrospective</option>
          <option value="all">All</option>
        </select></label>
      </div>
      <p className="hint">
        MTTA: mean time from an incident's creation to its acknowledgement; MTTR: to its resolution. Both
        cover only incidents acknowledged (resolved), so open incidents not yet acknowledged are shown
        separately with the longest wait. Sorted by MTTA, longest first. Operational incidents are those
        detected while the condition was still ongoing.
      </p>
      {error && <p className="error">Could not load the figures: {error.message}</p>}
      {data && rows.length === 0 && <p className="empty">No incidents in this period.</p>}
      {rows.length > 0 && (
        <div className="table-wrap"><table className="table perf">
          <thead>
            <tr>
              <th>{GROUP_NAMES[group]}</th><th className="num">Incidents</th><th className="num">Critical</th>
              <th>Awaiting acknowledgement</th><th>MTTA</th><th>MTTR</th><th className="num">SMS failed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, k) => (
              <tr key={r.key} className={k === 0 && r.mtta_min != null ? "highlight" : ""}>
                <th scope="row">{group === "tier" ? label(TIERS, r.label) : r.label}{k === 0 && r.mtta_min != null && rows.length > 1 && <span className="flag flag-warn">Longest MTTA</span>}</th>
                <td className="num">{r.incidents}</td>
                <td className="num">{r.critical}</td>
                <td>{r.unacknowledged > 0
                  ? <span className="flag flag-bad">{r.unacknowledged}, longest {duration(Number(r.longest_wait_min))}</span>
                  : <span className="muted">none</span>}</td>
                <td><Bar value={r.mtta_min} max={max} n={r.acknowledged} of={r.incidents} what="acknowledged" /></td>
                <td><Bar value={r.mttr_min} max={max} n={r.resolved} of={r.incidents} what="resolved" /></td>
                <td className="num">{r.notification_failed}</td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr><th>Total</th><td className="num">{total}</td><td colSpan={5}></td></tr></tfoot>
        </table></div>
      )}
    </div>
  );
}

function Bar({ value, max, n, of, what }) {
  if (value == null) return <span className="muted">none {what}</span>;
  return (
    <div className="bar-cell">
      <div className="bar" style={{ width: `${Math.max(2, (Number(value) / max) * 100)}%` }} />
      <span>{duration(Number(value))}</span>
      <span className="muted"> ({n}/{of} {what})</span>
    </div>
  );
}
