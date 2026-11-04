// Dashboard notifications (FR9.1): every SMS is also shown here, so a failure of the SMS channel never
// leaves an incident unseen.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, useApi } from "../api.js";
import { ago } from "../format.js";

export default function Notifications({ unread }) {
  const [open, setOpen] = useState(false);
  const { data, reload } = useApi(open ? "/notifications?limit=30" : null, ["notification"]);
  const navigate = useNavigate();
  const box = useRef(null);
  useEffect(() => {
    const close = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const go = async (n) => {
    if (!n.read_at) await api(`/notifications/${n.id}/read`, { method: "POST" });
    reload();
    setOpen(false);
    if (n.incident_no) navigate(`/incidents/${n.incident_no}`);
    else if (n.consignment_id) navigate(`/consignments/${n.consignment_id}`);
  };

  return (
    <div className="notif" ref={box}>
      <button className="btn-quiet notif-button" onClick={() => setOpen(!open)} aria-expanded={open}
              aria-label={`Notifications, ${unread} unread`}>
        <span aria-hidden="true">🔔</span>
        {unread > 0 && <span className="badge">{unread}</span>}
      </button>
      {open && (
        <div className="notif-panel" role="dialog" aria-label="Notifications">
          <div className="notif-head">Notifications</div>
          {!data && <p className="muted pad">Loading…</p>}
          {data && data.length === 0 && <p className="muted pad">No notifications.</p>}
          <ul>
            {data?.map((n) => (
              <li key={n.id}>
                <button className={`notif-item ${n.read_at ? "" : "unread"}`} onClick={() => go(n)}>
                  <span className="notif-body">{n.body}</span>
                  <span className="notif-time">
                    {ago(n.created_at)}
                    {n.notification_failed && <span className="flag flag-bad">SMS failed</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
