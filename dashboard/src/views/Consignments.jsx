// Consignments and custody (Section 3.4.4): create, release to a receiver, confirm or reject receipt,
// withdraw a handoff, complete. Each action is offered only to the person allowed to take it.
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, useApi } from "../api.js";
import { Flag } from "../components/Status.jsx";
import { CUSTODY, label, time } from "../format.js";

export default function Consignments() {
  const [completed, setCompleted] = useState(false);
  const { data, error } = useApi(`/consignments?completed=${completed}`, ["consignment", "custody_handoff"]);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  return (
    <div className="page">
      <div className="page-head">
        <h1>Consignments</h1>
        <button className="btn primary" onClick={() => setCreating(!creating)}>{creating ? "Cancel" : "New consignment"}</button>
      </div>
      {creating && <NewConsignment onDone={(id) => navigate(`/consignments/${id}`)} />}
      <label className="check">
        <input type="checkbox" checked={completed} onChange={(e) => setCompleted(e.target.checked)} /> Include completed
      </label>
      {error && <p className="error">Could not load consignments: {error.message}</p>}
      {data && data.length === 0 && <p className="empty">No consignments.</p>}
      {data && data.length > 0 && (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>No.</th><th>Product</th><th>From → to</th><th>Logger</th><th>Custody</th><th>Held by</th><th></th></tr></thead>
          <tbody>
            {data.map((c) => (
              <tr key={c.id} className="clickable" onClick={() => navigate(`/consignments/${c.id}`)}>
                <td><Link to={`/consignments/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.consignment_no}</Link></td>
                <td>{c.product_name}<div className="muted">{c.quantity} doses</div></td>
                <td>{c.origin_name} → {c.destination}</td>
                <td>{c.logger || "—"}</td>
                <td>{label(CUSTODY, c.state)}{c.handoff && c.state === "in_handoff" && <div className="muted">to {c.handoff.to_name}</div>}</td>
                <td>{c.holder_name || "—"}</td>
                <td>{(c.can_confirm || c.can_reject) && <Flag tone="warn">Awaiting you</Flag>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

function NewConsignment({ onDone }) {
  const { data: lk } = useApi("/lookups");
  const [f, setF] = useState({ origin_facility_id: "", product_id: "", quantity: 100, destination: "",
                               expected_transit_min: 120, device_serial: "" });
  const [err, setErr] = useState(null);
  if (!lk) return <p className="muted">Loading…</p>;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/consignments", { method: "POST", body: {
        ...f, quantity: Number(f.quantity), expected_transit_min: Number(f.expected_transit_min),
        device_serial: f.device_serial || null } });
      onDone(r.consignment_id);
    } catch (x) {
      setErr(x.message);
    }
  };
  return (
    <form className="panel form" onSubmit={submit}>
      <h2>New consignment</h2>
      <label className="field"><span>From</span>
        <select required value={f.origin_facility_id} onChange={set("origin_facility_id")}>
          <option value="">Choose…</option>{lk.origins.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select></label>
      <label className="field"><span>Product</span>
        <select required value={f.product_id} onChange={set("product_id")}>
          <option value="">Choose…</option>{lk.products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select></label>
      <label className="field"><span>Doses</span><input type="number" min={1} required value={f.quantity} onChange={set("quantity")} /></label>
      <label className="field"><span>Destination</span><input required maxLength={200} value={f.destination} onChange={set("destination")} placeholder="e.g. Outreach: Kofar Waika" /></label>
      <label className="field"><span>Expected transit (minutes)</span><input type="number" min={1} required value={f.expected_transit_min} onChange={set("expected_transit_min")} /></label>
      <label className="field"><span>Logger</span>
        <select value={f.device_serial} onChange={set("device_serial")}>
          <option value="">None</option>{lk.loggers.map((d) => <option key={d.serial_no} value={d.serial_no}>{d.serial_no}</option>)}
        </select></label>
      {err && <p className="error">{err}</p>}
      <button className="btn primary" type="submit">Create</button>
    </form>
  );
}

export function ConsignmentDetail() {
  const { id } = useParams();
  const { data: c, error, reload } = useApi(`/consignments/${id}`, ["consignment", "custody_handoff"]);
  if (error) return <div className="page"><p className="error">{error.status === 403 ? "You do not have access to this consignment." : error.message}</p></div>;
  if (!c) return <div className="page"><p className="muted">Loading…</p></div>;
  return (
    <div className="page">
      <p className="crumbs"><Link to="/consignments">Consignments</Link> › {c.consignment_no}</p>
      <div className="page-head"><h1>Consignment {c.consignment_no}</h1><Flag>{label(CUSTODY, c.state)}</Flag></div>
      <div className="grid2">
        <section className="panel">
          <h2>Details</h2>
          <dl className="facts">
            <dt>Product</dt><dd>{c.product_name}, {c.quantity} doses</dd>
            <dt>From</dt><dd>{c.origin_name}</dd>
            <dt>To</dt><dd>{c.destination}</dd>
            <dt>Logger</dt><dd>{c.logger ? <>{c.logger} <Link to="/map">(on the map)</Link></> : "none"}</dd>
            <dt>Held by</dt><dd>{c.holder_name || "—"}</dd>
            {c.handoff && <><dt>Handoff</dt><dd>{c.handoff.from_name} → {c.handoff.to_name}, released {time(c.handoff.released_at)}; {c.handoff.state === "pending" ? `to be confirmed by ${time(c.handoff.window_ends_at)}` : "rejected"}</dd></>}
          </dl>
        </section>
        <CustodyActions c={c} reload={reload} />
      </div>
      <section className="panel">
        <h2>Custody record</h2>
        <ol className="timeline">
          {c.events.map((e) => (
            <li key={e.seq}><time>{time(e.occurred_at)}</time>
              <span>{label({ create: "Created", release: "Released", confirm: "Receipt confirmed", reject: "Rejected",
                             cancel: "Handoff withdrawn", complete: "Completed", expire: "Confirmation window ended" }, e.type)}
                {e.actor_name && ` by ${e.actor_name}`}{e.to_name && e.type === "release" ? ` to ${e.to_name}` : ""}{e.reason ? `: ${e.reason}` : ""}</span></li>
          ))}
        </ol>
      </section>
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
  return (
    <section className="panel">
      <h2>Actions</h2>
      {!any && <p className="muted">Nothing for you to do on this consignment.</p>}
      {c.can_release && (
        <div className="field-row">
          <label className="field"><span>Release to</span>
            <select value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">Choose a team lead or driver…</option>
              {lk?.receivers.map((r) => <option key={r.id} value={r.id}>{r.full_name}</option>)}
            </select></label>
          <button className="btn primary" disabled={!to} onClick={() => run("release", { to_user: to }, "Released; the receiver has been sent a confirmation code.")}>Release</button>
        </div>
      )}
      {c.can_confirm && <button className="btn primary" onClick={() => run("confirm", undefined, "Receipt confirmed; you now hold the consignment.")}>Confirm receipt</button>}
      {(c.can_reject || c.can_cancel) && (
        <label className="field"><span>Reason</span><input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} /></label>
      )}
      <div className="btn-row">
        {c.can_reject && <button className="btn" disabled={!reason.trim()} onClick={() => run("reject", { reason }, "Rejected; custody stays with the releaser.")}>Reject</button>}
        {c.can_cancel && <button className="btn" disabled={!reason.trim()} onClick={() => run("cancel", { reason }, "Handoff withdrawn.")}>Withdraw handoff</button>}
        {c.can_complete && <button className="btn" onClick={() => run("complete", undefined, "Completed.")}>Mark completed</button>}
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
    </section>
  );
}
