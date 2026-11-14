import { formatMetric, normalise, NOT_REPORTED, type Point } from "@/lib/analytics";

/*
 * A small daily bar chart in token colours, drawn as SVG (no chart library on the
 * critical path). A day with no reported value is a gap labelled "Not reported", never a
 * zero bar. Screen readers get the same numbers as a table.
 */
export function BarChart({ points, metric, label }: { points: readonly Point[]; metric: string; label: string }) {
  const h = normalise(points);
  const w = 100 / Math.max(1, points.length);
  return (
    <figure className="grid gap-2">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-40 w-full" aria-hidden="true">
        {h.map((v, i) =>
          v === null ? (
            <rect key={points[i]?.date ?? i} x={i * w + w * 0.15} y={38} width={w * 0.7} height={2} className="fill-border-strong" />
          ) : (
            <rect
              key={points[i]?.date ?? i}
              x={i * w + w * 0.15}
              y={40 - Math.max(0.6, v * 38)}
              width={w * 0.7}
              height={Math.max(0.6, v * 38)}
              className="fill-accent"
            />
          ),
        )}
      </svg>
      <figcaption className="flex justify-between text-caption text-fg-subtle">
        <span>{points[0]?.date ?? ""}</span>
        <span>{label}</span>
        <span>{points[points.length - 1]?.date ?? ""}</span>
      </figcaption>
      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">{metric}</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.date}>
              <td>{p.date}</td>
              <td>{p.value === null ? NOT_REPORTED : formatMetric(p.value, metric)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
