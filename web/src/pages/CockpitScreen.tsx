import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, AlertTriangle, ArrowRight, CheckCircle2, Wrench } from 'lucide-react'
import { useLive } from '../state/LiveState'
import WarehouseMap from './WarehouseMap'
import Sparkline from '../components/Sparkline'
import './CockpitScreen.css'

const KPI_CARDS = [
    { key: 'daily_travel_km', label: 'Picker travel', unit: 'km/day', better: 'lower' },
    { key: 'avg_distance_per_pick_m', label: 'Distance per pick', unit: 'm round trip', better: 'lower' },
    { key: 'forward_pick_coverage_pct', label: 'Class A in forward pick', unit: '% of A demand', better: 'higher' },
    { key: 'daily_picks', label: 'Picks per day', unit: 'forecast driven', better: 'higher' },
] as const

export default function CockpitScreen() {
    const { dashboard, layout, demand, run } = useLive()
    const [selectedMove, setSelectedMove] = useState<string | null>(null)

    if (!dashboard || !layout || !demand) {
        return <div className="app-body layout-full ck-loading">Reading the warehouse…</div>
    }

    const addressable = dashboard.problems.filter((p) => p.addressable)
    const notes = dashboard.problems.filter((p) => !p.addressable)
    const headline = demand.headline
    const moves = run?.status === 'COMPLETE' ? run.moves : []

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
                        <span className="ck-panel__dim">{addressable.length ? `${addressable.length} actionable` : 'none outstanding'}</span>
                    </div>
                    {addressable.length ? (
                        addressable.map((problem) => (
                            <div className={`ck-gap ck-gap--${problem.severity}`} key={problem.id}>
                                <span className="ck-gap__metric">{problem.metric}</span>
                                <div>
                                    <strong>{problem.title}</strong>
                                    <p>{problem.detail}</p>
                                </div>
                            </div>
                        ))
                    ) : (
                        <div className="ck-resolved">
                            <CheckCircle2 size={16} />
                            <div>
                                <strong>No slotting problems outstanding.</strong>
                                <p>Class A demand is served from the forward pick face and the average pick trip is within target.</p>
                            </div>
                        </div>
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
                        <span>What happens next</span>
                    </div>
                    <p className="ck-next__text">
                        {addressable.length
                            ? 'The DeepAgent reads this state, the specialists assess it, and cuOpt solves a constrained move plan. Nothing reaches the WMS without your approval.'
                            : 'There is nothing for the planner to act on. Generate a different warehouse to run the flow again.'}
                    </p>
                    <Link className={`ck-btn ck-btn--primary${addressable.length ? '' : ' is-disabled'}`} to="/plan">
                        Build the move plan <ArrowRight size={15} />
                    </Link>
                </section>
            </aside>
        </div>
    )
}
