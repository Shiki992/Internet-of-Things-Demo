export default function StatTile({ label, value, sub, color }) {
  return (
    <div className="card tile">
      <div className="label">
        {color && <span className="swatch" style={{ background: color }} />}
        {label}
      </div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}
