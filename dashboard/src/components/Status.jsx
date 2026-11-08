// Status is never shown by colour alone (Section 3.4.7, FR10.2): each has a symbol and a text label too,
// and on the map problem facilities are also larger than normal ones. Red for critical and yellow for
// warning, as officers expect; `ink` is the darker shade used for text so that it stays readable.
export const STATUS = {
  critical: { symbol: "▲", label: "Critical", fill: "#DC2626", ink: "#B91C1C" },
  warning: { symbol: "◆", label: "Warning", fill: "#F5B700", ink: "#8A6100" },
  stale: { symbol: "◌", label: "No recent data", fill: "#8B95A1", ink: "#5B6470" },
  ok: { symbol: "●", label: "Normal", fill: "#16A34A", ink: "#15803D" },
  no_data: { symbol: "○", label: "No data yet", fill: "#B8C0CA", ink: "#6B7280" },
};

export function Status({ status, compact = false }) {
  const s = STATUS[status] || STATUS.no_data;
  return (
    <span className={`status status-${status}`} style={{ color: s.ink }} title={s.label}>
      <span aria-hidden="true">{s.symbol}</span>
      {compact ? <span className="sr-only">{s.label}</span> : <span className="status-label">{s.label}</span>}
    </span>
  );
}

export function Severity({ severity }) {
  return <Status status={severity === "critical" ? "critical" : "warning"} />;
}

export function Flag({ children, tone = "neutral", title }) {
  return (
    <span className={`flag flag-${tone}`} title={title}>
      {children}
    </span>
  );
}
