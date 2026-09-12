import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, AlertTriangle, ArrowRight, CheckCircle2, Wrench } from 'lucide-react'
import { useLive } from '../state/LiveState'
import type { Metric } from '../api/client'
import WarehouseMap from './WarehouseMap'
import Sparkline from '../components/Sparkline'
import './CockpitScreen.css'

const KPI_CARDS = [
    { key: 'daily_travel_km', label: 'Picker travel', unit: 'km/day', better: 'lower' },
    { key: 'avg_distance_per_pick_m', label: 'Distance per pick', unit: 'm round trip', better: 'lower' },
    { key: 'forward_pick_coverage_pct', label: 'Class A in forward pick', unit: '% of A demand', better: 'higher' },
    { key: 'daily_picks', label: 'Picks per day', unit: 'forecast driven', better: 'higher' },
] as const

/** The chip is narrow, so figures are shortened rather than wrapped or clipped. */
function formatMetric(metric: Metric | null): string {
    if (!metric || !Number.isFinite(metric.value)) return ''
    const { value, unit } = metric
    if (unit === '%') return `${value.toFixed(1)}%`
    // Distances read as one token and are never abbreviated: 3029km, not 3.0k km.
    if (unit === 'm' || unit === 'km') {
        return `${Math.abs(value) >= 100 ? Math.round(value) : value.toFixed(1)}${unit}`
    }
    const abs = Math.abs(value)
    const short =
        abs >= 1_000_000
            ? `${(value / 1_000_000).toFixed(1)}M`
            : abs >= 10_000
                ? `${Math.round(value / 1000)}k`
                : Number.isInteger(value)
                    ? String(value)
                    : value.toFixed(1)
    // Counted things need the noun to mean anything.
    return unit ? `${short} ${unit}` : short
}

export default function CockpitScreen() {
    const { dashboard, layout, demand, run } = useLive()
    const [selectedMove, setSelectedMove] = useState<string | null>(null)

    if (!dashboard || !layout || !demand) {
        return <div className="app-body layout-full ck-loading">Reading the warehouse…</div>
    }

    const addressable = dashboard.problems.filter((p) => p.addressable)
    const notes = dashboard.problems.filter((p) => !p.addressable)
    // A re-assessment keeps the previous findings on screen, so "nothing yet" and
    // "nothing to report" have to stay distinguishable.
    const findings = dashboard.problems.length > 0
    const headline = demand.headline
    const planned = run?.status === 'COMPLETE' ? run.moves : []
    // Once a plan is written the plan itself is gone, so the map falls back to
    // the relocations that were actually executed.
    const committed = dashboard.last_commit?.applied ?? []
    const moves = planned.length > 0 ? planned : committed
    const showingCommitted = planned.length === 0 && committed.length > 0
    const lastCommit = dashboard.last_commit
    // cuOpt has actually solved this layout under the constraints, so its result
    // outranks the analysis model's guess at what is still worth moving. A run
    // whose rounds all failed proved nothing, so it is not an optimum.
    const planPending = planned.length > 0
    const constrainedOptimum =
        run?.status === 'COMPLETE' && run.chosen_round === null && run.rounds.some((round) => round.status === 'done')
    const actionable = planPending || (!constrainedOptimum && addressable.length > 0)

    return (
        <div className="app-body layout-cockpit">
            <aside className="info-region">
                <section className="ck-panel">
                    <div className="ck-panel__head">
                        <Activity size={15} />
                        <span>Demand signal</span>
                    </div>
                    {headline ? (
                        <>
                            <div className="ck-signal">
                                <strong>{headline.product_name}</strong>
                                <span className="ck-signal__sku">
                                    {headline.sku_id} · class {headline.abc_class} · slot {headline.slot}
                                </span>
                            </div>
                            <div className="ck-signal__stats">
                                <div>
                                    <span>promotion uplift</span>
                                    <b>+{headline.promotion_uplift_pct}%</b>
                                </div>
                                <div>
                                    <span>forecast picks</span>
                                    <b>{headline.picks_per_day}/day</b>
                                </div>
                                <div>
                                    <span>walk to pick face</span>
                                    <b>{headline.distance_m}m</b>
                                </div>
                            </div>
                            <Sparkline points={headline.series.map((p) => p.qty)} highlight={headline.series.map((p) => p.promotion)} />
                            <p className="ck-signal__foot">{demand.horizon_days}-day forecast · {demand.promoted_count} lines on promotion</p>
                        </>
                    ) : (
                        <p className="ck-empty">No promoted line in the current forecast.</p>
                    )}
                </section>

                <section className="ck-panel">
                    <div className="ck-panel__head">
                        <span>Top movers</span>
                        <span className="ck-panel__dim">forecast picks per day</span>
                    </div>
                    <div className="ck-movers">
                        {demand.top_movers.map((mover) => (
                            <div className="ck-mover" key={mover.sku_id}>
                                <span className={`ck-mover__abc ck-mover__abc--${mover.abc_class.toLowerCase()}`}>{mover.abc_class}</span>
                                <div className="ck-mover__text">
                                    <strong>{mover.product_name}</strong>
                                    <span>
                                        {mover.slot || 'unslotted'} · {mover.distance_m}m
                                        {mover.temperature_controlled ? ' · cold chain' : ''}
                                    </span>
                                </div>
                                <span className="ck-mover__picks">{mover.picks_per_day}</span>
                            </div>
                        ))}
                    </div>
                </section>
            </aside>

            <section className="viewport-region">
                <WarehouseMap
                    slots={layout.slots}
                    moves={moves}
                    selectedMoveId={selectedMove}
                    onSelectMove={setSelectedMove}
                    hasPlan={moves.length > 0}
                    executed={showingCommitted}
                />
                {selectedMove && (
                    <div className="ck-movecard">
                        {(() => {
                            const move = moves.find((m) => m.id === selectedMove)
                            if (!move) return null
                            return (
                                <>
                                    <div className="ck-movecard__head">
                                        <strong>{move.id}</strong>
                                        <span>
                                            {move.from_slot} <ArrowRight size={12} /> {move.to_slot}
                                        </span>
                                        <button onClick={() => setSelectedMove(null)}>close</button>
                                    </div>
                                    <p>{move.reason}</p>
                                    <div className="ck-movecard__stats">
                                        <span>
                                            <b>{move.benefit_hours_per_day}</b> hr/day saved
                                        </span>
                                        <span>
                                            <b>{move.labor_minutes}</b> min to relocate
                                        </span>
                                    </div>
                                </>
                            )
                        })()}
                    </div>
                )}
            </section>

            <aside className="insight-region">
                <section className="ck-panel">
                    <div className="ck-panel__head">
                        <span>Warehouse today</span>
                    </div>
                    <div className="ck-kpis">
                        {KPI_CARDS.map((card) => {
                            const value = dashboard.kpis[card.key]
                            const base = dashboard.baseline?.[card.key]
                            const delta = base === undefined || base === null ? null : Number((value - base).toFixed(1))
                            const good = delta === null ? false : card.better === 'lower' ? delta < 0 : delta > 0
                            return (
                                <div className="ck-kpi" key={card.key}>
                                    <span className="ck-kpi__label">{card.label}</span>
                                    <strong>{value}</strong>
                                    <span className="ck-kpi__unit">{card.unit}</span>
                                    {delta !== null && delta !== 0 && (
                                        <span className={`ck-kpi__delta ${good ? 'is-good' : 'is-bad'}`}>
                                            {delta > 0 ? '+' : ''}
                                            {delta} vs before
                                        </span>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                </section>

                <section className="ck-panel">
                    <div className="ck-panel__head">
                        <AlertTriangle size={15} />
                        <span>Slotting vs demand</span>
                        <span className="ck-panel__dim">
                            {dashboard.analysis.status === 'failed'
                                ? 'analysis unavailable'
                                : dashboard.analysis.status === 'pending'
                                    ? findings
                                        ? 're-assessing…'
                                        : 'reading the warehouse…'
                                    : constrainedOptimum && findings
                                        ? 'none reachable'
                                        : addressable.length
                                            ? `${addressable.length} actionable`
                                            : 'none outstanding'}
                        </span>
                    </div>
                    {/* Only claim we have nothing to show when we genuinely have nothing. */}
                    {dashboard.analysis.status === 'pending' && !findings && (
                        <div className="ck-note">
                            <Wrench size={13} />
                            <div>
                                <strong>Assessing the layout against demand…</strong>
                                <p>{dashboard.analysis.model} is judging which gaps a move plan could close.</p>
                            </div>
                        </div>
                    )}
                    {dashboard.analysis.status === 'failed' && (
                        <div className="ck-note">
                            <AlertTriangle size={13} />
                            <div>
                                <strong>The analysis could not be produced.</strong>
                                <p>{dashboard.analysis.error}</p>
                            </div>
                        </div>
                    )}
                    {addressable.length ? (
                        addressable.map((problem) => (
                            <div
                                className={`ck-gap ck-gap--${constrainedOptimum ? 'unreachable' : problem.severity}`}
                                key={problem.id}
                            >
                                {formatMetric(problem.metric) && (
                                    <span className="ck-gap__metric">{formatMetric(problem.metric)}</span>
                                )}
                                <div>
                                    {problem.title && <strong>{problem.title}</strong>}
                                    <p>{problem.detail}</p>
                                </div>
                            </div>
                        ))
                    ) : (
                        dashboard.analysis.status === 'ready' && (
                            <div className="ck-resolved">
                                <CheckCircle2 size={16} />
                                <div>
                                    <strong>No slotting problems outstanding.</strong>
                                    <p>
                                        {lastCommit
                                            ? `Class A demand is served from the forward pick face at ${dashboard.kpis.forward_pick_coverage_pct}% and the average pick trip is ${dashboard.kpis.avg_distance_per_pick_m}m, after ${dashboard.relocated_total} relocations were committed.`
                                            : `Class A demand is served from the forward pick face at ${dashboard.kpis.forward_pick_coverage_pct}% and the average pick trip is ${dashboard.kpis.avg_distance_per_pick_m}m.`}
                                    </p>
                                </div>
                            </div>
                        )
                    )}
                    {notes.map((note) => (
                        <div className="ck-note" key={note.id}>
                            <Wrench size={13} />
                            <div>
                                <strong>{note.title}</strong>
                                <p>{note.detail}</p>
                            </div>
                        </div>
                    ))}
                </section>

                <section className="ck-panel ck-next">
                    <div className="ck-panel__head">
                        <span>
                            {planPending
                                ? 'What happens next'
                                : constrainedOptimum
                                    ? 'Constrained optimum'
                                    : lastCommit
                                        ? 'Plan executed'
                                        : actionable
                                            ? 'What happens next'
                                            : 'Nothing outstanding'}
                        </span>
                    </div>
                    {planPending ? (
                        <>
                            <p className="ck-next__text">
                                cuOpt has solved a constrained move plan of <b>{planned.length}</b> relocations. Nothing reaches
                                the WMS without your approval.
                            </p>
                            <Link className="ck-btn ck-btn--primary" to="/plan">
                                Review the move plan <ArrowRight size={15} />
                            </Link>
                        </>
                    ) : constrainedOptimum ? (
                        <>
                            <p className="ck-next__text">
                                cuOpt solved this layout under your constraints and found no relocation that shortens picker
                                travel. {lastCommit ? 'The plan has been written to the WMS and the' : 'The'} layout is at its
                                constrained optimum.
                            </p>
                            <p className="ck-next__text">
                                {addressable.length > 0
                                    ? 'The gaps above remain measurable, but none can be closed within the current move cap. Raise the cap or generate a different warehouse to run the flow again.'
                                    : 'Raise the move cap or generate a different warehouse to run the flow again.'}
                            </p>
                        </>
                    ) : lastCommit ? (
                        <>
                            <p className="ck-next__text">
                                <b>{dashboard.relocated_total}</b> relocations have been written to the WMS, the last at{' '}
                                {new Date(lastCommit.at).toLocaleTimeString()}. Against the warehouse as first measured, picker
                                travel is down from <b>{dashboard.baseline?.daily_travel_km} km/day</b> to{' '}
                                <b>{dashboard.kpis.daily_travel_km} km/day</b> and the average pick trip from{' '}
                                <b>{dashboard.baseline?.avg_distance_per_pick_m}m</b> to{' '}
                                <b>{dashboard.kpis.avg_distance_per_pick_m}m</b>.
                            </p>
                            {addressable.length ? (
                                <>
                                    <p className="ck-next__text">
                                        The layout still shows {addressable.length} measurable{' '}
                                        {addressable.length === 1 ? 'gap' : 'gaps'}. Whether another plan can close{' '}
                                        {addressable.length === 1 ? 'it' : 'them'} is for cuOpt to decide, not this assessment.
                                    </p>
                                    <Link className="ck-btn ck-btn--primary" to="/plan">
                                        Run the DeepAgent again <ArrowRight size={15} />
                                    </Link>
                                </>
                            ) : (
                                <>
                                    <p className="ck-next__text">
                                        This assessment finds nothing further worth moving, but the warehouse changed when the
                                        plan was written and cuOpt has not solved it since. Only a run can confirm the layout is
                                        finished.
                                    </p>
                                    <Link className="ck-btn ck-btn--primary" to="/plan">
                                        Run the DeepAgent again <ArrowRight size={15} />
                                    </Link>
                                </>
                            )}
                        </>
                    ) : actionable ? (
                        <>
                            <p className="ck-next__text">
                                The DeepAgent reads this state, the specialists assess it, and cuOpt solves a constrained move
                                plan. Nothing reaches the WMS without your approval.
                            </p>
                            <Link className="ck-btn ck-btn--primary" to="/plan">
                                Build the move plan <ArrowRight size={15} />
                            </Link>
                        </>
                    ) : (
                        <p className="ck-next__text">
                            There is nothing for the planner to act on. Generate a different warehouse to run the flow again.
                        </p>
                    )}
                </section>
            </aside>
        </div>
    )
}
