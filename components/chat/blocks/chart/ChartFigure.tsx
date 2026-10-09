"use client"

import { useState } from "react"

import type { ChartSpec } from "./schema"
import {
  GEOM,
  buildYAxis,
  categoryCenter,
  formatTick,
  plotBottom,
  plotLeft,
  plotRight,
  plotTop,
  plotWidth,
} from "./scale"

/** Radius of the rounded data-end on a bar. */
const BAR_RADIUS = 4
/** Surface gap between adjacent bars inside one category group. */
const BAR_GAP = 2

/**
 * Series colours are assigned in slot order and never cycled. A single series
 * uses the sequential hue instead, because one hue per bar would encode rank as
 * identity and invite the reader to see groupings that are not in the data.
 */
function seriesColor(index: number, total: number): string {
  if (total === 1) return "var(--viz-seq)"
  return `var(--viz-series-${index + 1})`
}

/**
 * A bar whose data-end is rounded and whose baseline end is square, so the bar
 * still reads as anchored to zero. Handles negative values by mirroring.
 */
function barPath(x: number, width: number, valueY: number, zeroY: number): string {
  const up = valueY <= zeroY
  const height = Math.abs(zeroY - valueY)
  const r = Math.min(BAR_RADIUS, height, width / 2)
  const right = x + width

  if (up) {
    return [
      `M ${x} ${zeroY}`,
      `L ${x} ${valueY + r}`,
      `Q ${x} ${valueY} ${x + r} ${valueY}`,
      `L ${right - r} ${valueY}`,
      `Q ${right} ${valueY} ${right} ${valueY + r}`,
      `L ${right} ${zeroY}`,
      "Z",
    ].join(" ")
  }
  return [
    `M ${x} ${zeroY}`,
    `L ${x} ${valueY - r}`,
    `Q ${x} ${valueY} ${x + r} ${valueY}`,
    `L ${right - r} ${valueY}`,
    `Q ${right} ${valueY} ${right} ${valueY - r}`,
    `L ${right} ${zeroY}`,
    "Z",
  ].join(" ")
}

export function ChartFigure({ spec }: { spec: ChartSpec }) {
  const [hover, setHover] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)

  const { type, title, yLabel, categories, series } = spec
  const axis = buildYAxis(series.flatMap((s) => s.values))
  const slot = plotWidth / categories.length

  return (
    <figure className="viz-root my-3 rounded-lg border border-border bg-[var(--viz-surface)] p-4">
      {title && (
        <figcaption className="mb-1 font-display text-sm font-bold text-[var(--viz-ink)]">
          {title}
        </figcaption>
      )}
      {yLabel && (
        <p className="mb-2 text-xs text-[var(--viz-ink-muted)]">{yLabel}</p>
      )}

      {/* A legend only earns its space with more than one series. With one, the
          title already names what the marks are. */}
      {series.length > 1 && (
        <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
          {series.map((s, i) => (
            <li
              key={s.name}
              className="flex items-center gap-1.5 text-xs text-[var(--viz-ink)]"
            >
              <span
                aria-hidden
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ background: seriesColor(i, series.length) }}
              />
              {s.name}
            </li>
          ))}
        </ul>
      )}

      {showTable ? (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm text-[var(--viz-ink)]">
            <thead>
              <tr>
                <th className="px-2 py-1 text-left font-semibold">Category</th>
                {series.map((s) => (
                  <th key={s.name} className="px-2 py-1 text-right font-semibold">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {categories.map((c, ci) => (
                <tr key={c}>
                  <td className="px-2 py-1">{c}</td>
                  {series.map((s) => (
                    <td
                      key={s.name}
                      className="px-2 py-1 text-right"
                      style={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {formatTick(s.values[ci])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg
            viewBox={`0 0 ${GEOM.width} ${GEOM.height}`}
            className="w-full h-auto"
            role="img"
            aria-label={title ?? `${type} chart`}
          >
            {/* Gridlines stay recessive: they orient, they do not compete. */}
            {axis.ticks.map((t) => (
              <line
                key={t}
                x1={plotLeft}
                x2={plotRight}
                y1={axis.y(t)}
                y2={axis.y(t)}
                stroke={t === 0 ? "var(--viz-axis)" : "var(--viz-grid)"}
                strokeWidth={1}
              />
            ))}

            {axis.ticks.map((t) => (
              <text
                key={t}
                x={plotLeft - 8}
                y={axis.y(t)}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={11}
                fill="var(--viz-ink-muted)"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {formatTick(t)}
              </text>
            ))}

            {categories.map((c, ci) => (
              <text
                key={c}
                x={categoryCenter(ci, categories.length)}
                y={plotBottom + 16}
                textAnchor="middle"
                fontSize={11}
                fill="var(--viz-ink-muted)"
              >
                {c}
              </text>
            ))}

            {type === "bar" &&
              series.map((s, si) => {
                const groupWidth = slot * 0.7
                const barWidth =
                  (groupWidth - BAR_GAP * (series.length - 1)) / series.length
                return (
                  <g key={s.name} fill={seriesColor(si, series.length)}>
                    {s.values.map((v, ci) => {
                      const groupLeft =
                        categoryCenter(ci, categories.length) - groupWidth / 2
                      const x = groupLeft + si * (barWidth + BAR_GAP)
                      return (
                        <path
                          key={categories[ci]}
                          d={barPath(x, barWidth, axis.y(v), axis.zeroY)}
                          opacity={hover === null || hover === ci ? 1 : 0.35}
                        />
                      )
                    })}
                  </g>
                )
              })}

            {type === "line" &&
              series.map((s, si) => {
                const color = seriesColor(si, series.length)
                const points = s.values.map(
                  (v, ci) =>
                    [categoryCenter(ci, categories.length), axis.y(v)] as const
                )
                return (
                  <g key={s.name}>
                    <polyline
                      points={points.map(([x, y]) => `${x},${y}`).join(" ")}
                      fill="none"
                      stroke={color}
                      strokeWidth={2}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                    {points.map(([x, y], ci) => (
                      <circle
                        key={categories[ci]}
                        cx={x}
                        cy={y}
                        r={4}
                        fill={color}
                        // A 2px surface ring keeps overlapping markers readable.
                        stroke="var(--viz-surface)"
                        strokeWidth={2}
                      />
                    ))}
                  </g>
                )
              })}

            {/* Hit targets are the full category slot, which is far bigger than
                the marks and makes hovering a thin line practical. */}
            {categories.map((c, ci) => (
              <rect
                key={c}
                x={plotLeft + slot * ci}
                y={plotTop}
                width={slot}
                height={plotBottom - plotTop}
                fill="transparent"
                onMouseEnter={() => setHover(ci)}
                onMouseLeave={() => setHover(null)}
              />
            ))}
          </svg>

          {hover !== null && (
            <div
              className="pointer-events-none absolute top-0 rounded-md border border-border bg-[var(--viz-surface)] px-2 py-1 text-xs shadow-sm"
              style={{
                left: `${((plotLeft + slot * hover + slot / 2) / GEOM.width) * 100}%`,
                transform: "translateX(-50%)",
              }}
            >
              <div className="font-semibold text-[var(--viz-ink)]">
                {categories[hover]}
              </div>
              {series.map((s, i) => (
                <div
                  key={s.name}
                  className="flex items-center gap-1.5 text-[var(--viz-ink)]"
                >
                  <span
                    aria-hidden
                    className="inline-block h-2 w-2 rounded-sm"
                    style={{ background: seriesColor(i, series.length) }}
                  />
                  {series.length > 1 && <span>{s.name}</span>}
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>
                    {formatTick(s.values[hover])}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={() => setShowTable((v) => !v)}
        className="mt-2 text-xs text-[var(--viz-ink-muted)] underline hover:text-[var(--viz-ink)] cursor-pointer"
      >
        {showTable ? "Show chart" : "Show data"}
      </button>
    </figure>
  )
}
