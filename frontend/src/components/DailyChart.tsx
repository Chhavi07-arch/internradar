import { useEffect, useRef, useState } from "react";
import type { DayCount, Origin } from "../api";
import { ORIGINS, shortDate } from "../format";
import { Button, Card, CardHeader, cx } from "./ui";

const COLOR: Record<Origin, string> = {
  ats: "var(--color-origin-ats)",
  list: "var(--color-origin-list)",
  board: "var(--color-origin-board)",
  email: "var(--color-origin-email)",
};

const HEIGHT = 180;
const PAD = { top: 12, right: 8, bottom: 24, left: 28 };
const GAP = 2; // surface gap between stacked segments

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

export function DailyChart({ days }: { days: DayCount[] }) {
  const [asTable, setAsTable] = useState(false);
  const total = days.reduce((sum, d) => sum + d.ats + d.list + d.board + d.email, 0);
  const active = ORIGINS.filter((o) => days.some((d) => d[o.key] > 0));

  return (
    <Card>
      <CardHeader
        title="New internships per day"
        hint={`${total} found in the last ${days.length} days, by where they were first seen`}
        right={
          <Button variant="outline" onClick={() => setAsTable((t) => !t)} aria-pressed={asTable}>
            {asTable ? "Chart" : "Table"}
          </Button>
        }
      />
      <div className="px-5 pb-4 pt-3">
        {active.length > 1 && (
          <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2" aria-label="Legend">
            {active.map((o) => (
              <li key={o.key} className="flex items-center gap-1.5">
                <span className="size-2.5 rounded-sm" style={{ background: COLOR[o.key] }} />
                {o.label}
              </li>
            ))}
          </ul>
        )}
        {asTable ? <DayTable days={days} /> : <Bars days={days} origins={active.map((o) => o.key)} />}
      </div>
    </Card>
  );
}

function Bars({ days, origins }: { days: DayCount[]; origins: Origin[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  const totals = days.map((d) => origins.reduce((sum, o) => sum + d[o], 0));
  const max = niceMax(Math.max(...totals, 0));
  const plotW = width - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const slot = plotW / days.length;
  const barW = Math.min(28, slot * 0.6);
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];

  return (
    <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={HEIGHT} role="img" aria-label="Stacked bar chart of new internships per day">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)}
              stroke={t === 0 ? "var(--color-axis)" : "var(--color-grid)"} strokeWidth={1} />
            <text x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end"
              className="fill-muted text-[10px] tabular-nums">{t}</text>
          </g>
        ))}
        {days.map((day, i) => {
          const x = PAD.left + i * slot + (slot - barW) / 2;
          let base = 0;
          const present = origins.filter((o) => day[o] > 0);
          return (
            <g key={day.day}>
              {/* full-height hit target, larger than the mark */}
              <rect x={PAD.left + i * slot} y={PAD.top} width={slot} height={plotH}
                fill={hover === i ? "rgb(255 255 255 / 0.04)" : "transparent"}
                onMouseEnter={() => setHover(i)} />
              {present.map((o, k) => {
                const top = y(base + day[o]);
                const height = Math.max(0, y(base) - top - (k > 0 ? GAP : 0));
                base += day[o];
                const isTop = k === present.length - 1;
                return (
                  <path key={o} pointerEvents="none" fill={COLOR[o]}
                    d={segment(x, top, barW, height, isTop ? 4 : 0)} />
                );
              })}
              {/* every Nth day, counted from today so the latest day is always labelled */}
              {(days.length - 1 - i) % Math.ceil(days.length / 7) === 0 && (
                <text x={x + barW / 2} y={HEIGHT - 6} textAnchor="middle" className="fill-muted text-[10px]">
                  {shortDate(day.day)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <Tooltip day={days[hover]} origins={origins}
          left={Math.min(PAD.left + hover * slot + slot / 2, width - 150)} />
      )}
    </div>
  );
}

/** A bar segment whose top corners are rounded by `r` (only the stack's top segment). */
function segment(x: number, y: number, w: number, h: number, r: number): string {
  const radius = Math.min(r, w / 2, h);
  return [
    `M${x},${y + h}`,
    `V${y + radius}`,
    `Q${x},${y} ${x + radius},${y}`,
    `H${x + w - radius}`,
    `Q${x + w},${y} ${x + w},${y + radius}`,
    `V${y + h}Z`,
  ].join("");
}

function Tooltip({ day, origins, left }: { day: DayCount; origins: Origin[]; left: number }) {
  const total = origins.reduce((sum, o) => sum + day[o], 0);
  return (
    <div className="pointer-events-none absolute top-0 z-10 w-36 rounded-lg border border-line bg-raised px-3 py-2 text-xs shadow-xl"
      style={{ left }}>
      <p className="font-medium text-ink">{shortDate(day.day)} · {total} new</p>
      <ul className="mt-1 space-y-0.5">
        {ORIGINS.filter((o) => origins.includes(o.key)).map((o) => (
          <li key={o.key} className="flex items-center justify-between gap-2 text-ink-2">
            <span className="flex items-center gap-1.5">
              <span className="size-2 rounded-sm" style={{ background: COLOR[o.key] }} />
              {o.label}
            </span>
            <span className="tabular-nums">{day[o.key]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DayTable({ days }: { days: DayCount[] }) {
  return (
    <table className="w-full text-left text-xs">
      <thead className="text-muted">
        <tr>
          <th className="py-1.5 font-medium">Day</th>
          {ORIGINS.map((o) => <th key={o.key} className="py-1.5 text-right font-medium">{o.label}</th>)}
        </tr>
      </thead>
      <tbody className="tabular-nums text-ink-2">
        {[...days].reverse().map((d) => (
          <tr key={d.day} className={cx("border-t border-line")}>
            <td className="py-1.5">{shortDate(d.day)}</td>
            {ORIGINS.map((o) => <td key={o.key} className="py-1.5 text-right">{d[o.key]}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
