type Props = { points: number[]; highlight?: boolean[] }

/** Plots the forecast series exactly as returned; no smoothing, no projection. */
export default function Sparkline({ points, highlight = [] }: Props) {
    if (points.length < 2) return null
    const width = 300
    const height = 54
    const min = Math.min(...points)
    const max = Math.max(...points)
    const span = max - min || 1
    const step = width / (points.length - 1)
    const xy = points.map((value, index) => [index * step, height - ((value - min) / span) * (height - 8) - 4] as const)
    const line = xy.map(([x, y], i) => `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
    const area = `${line} L ${width} ${height} L 0 ${height} Z`

    return (
        <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="forecast series">
            <defs>
                <linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.35" />
                    <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
                </linearGradient>
            </defs>
            <path d={area} fill="url(#sparkFill)" />
            <path d={line} fill="none" stroke="#38bdf8" strokeWidth="1.6" />
            {xy.map(([x, y], index) =>
                highlight[index] ? <circle key={index} cx={x} cy={y} r="2.4" fill="#fbbf24" /> : null,
            )}
        </svg>
    )
}
