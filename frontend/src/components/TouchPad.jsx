import { useRef, useState } from "react";

const RANGE = 10; // m/s² at the edge of the pad

// Square pad: finger position maps to x (right +) / y (up +). Used as the
// data source when the browser won't expose the motion sensor (plain HTTP).
export default function TouchPad({ onMove }) {
  const ref = useRef(null);
  const [pt, setPt] = useState(null);

  function handle(e) {
    const r = ref.current.getBoundingClientRect();
    const nx = Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width) * 2 - 1));
    const ny = Math.max(-1, Math.min(1, ((e.clientY - r.top) / r.height) * 2 - 1));
    setPt({ nx, ny });
    onMove(nx * RANGE, -ny * RANGE);
  }

  function release() {
    setPt(null);
    onMove(0, 0, true); // bypass the rate limit so the rest position always lands
  }

  return (
    <div
      ref={ref}
      className="pad"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        handle(e);
      }}
      onPointerMove={(e) => e.buttons && handle(e)}
      onPointerUp={release}
      onPointerCancel={release}
    >
      {pt && (
        <div
          className="pad-dot"
          style={{ left: `${((pt.nx + 1) / 2) * 100}%`, top: `${((pt.ny + 1) / 2) * 100}%` }}
        />
      )}
    </div>
  );
}
