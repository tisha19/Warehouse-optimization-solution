import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Check, ChevronDown, Play, ShieldCheck, Sliders, X } from 'lucide-react'
import { api, type CommitState } from '../api/client'
import { useLive } from '../state/LiveState'
import './PlanScreen.css'

const AGENTS = [
    { id: 'supervisor', name: 'DeepAgent Orchestrator', detail: 'plans the objective and delegates' },
    { id: 'demand', name: 'Demand Intelligence', detail: 'forecast · velocity · ABC class' },
    { id: 'inventory', name: 'Inventory Intelligence', detail: 'cover · replenishment risk' },
    { id: 'warehouse', name: 'Warehouse Intelligence', detail: 'travel · congestion · capacity' },
    { id: 'optimize', name: 'Decision Optimization', detail: 'NVIDIA cuOpt · constrained solve' },
]

export default function PlanScreen() {
    const { run, dashboard, demand, startRun, setConstraints, decide, refreshWarehouse, refreshRun } = useLive()
    const navigate = useNavigate()
    const [expanded, setExpanded] = useState<string | null>(null)
    const [commitState, setCommitState] = useState<CommitState | null>(null)
    const [committing, setCommitting] = useState(false)

    const constraints = run?.constraints
    const solved = run?.status === 'COMPLETE' && run.moves.length > 0
    const moves = run?.moves ?? []
    const decisions = run?.decisions ?? {}

    useEffect(() => {
        if (!committing) return
        const timer = window.setInterval(async () => {
            const state = await api.commitStatus()
            setCommitState(state)
            if (state.status === 'COMMITTED') {
                setCommitting(false)
                await refreshWarehouse()
                await refreshRun()
                navigate('/cockpit')
            }
            if (state.status === 'FAILED' || state.status === 'DENIED') setCommitting(false)
        }, 2000)
        return () => window.clearInterval(timer)
    }, [committing, navigate, refreshWarehouse, refreshRun])

    const launch = async () => {
        await startRun()
        navigate('/workflow')
    }

    const send = async () => {
        setCommitting(true)
        setCommitState(await api.commit())
    }

    const counts = Object.values(decisions).reduce<Record<string, number>>(
        (acc, value) => ({ ...acc, [value]: (acc[value] ?? 0) + 1 }),
        {},
    )
    const windows = [...new Set(moves.map((m) => m.day))].sort((a, b) => a - b)

    return (
        <div className="app-body layout-plan">
            <aside className="info-region">
                <section className="pl-panel">
                    <div className="pl-panel__head">
                        <Sliders size={15} />
                        <span>Planner constraints</span>
                        <span className="pl-panel__dim">{solved ? 'solved' : 'not solved yet'}</span>
                    </div>
                    <p className="pl-panel__intro">The hard limits cuOpt must respect. Changing one applies to the next run.</p>

                    <label className="pl-slider">
                        <span>
                            Max moves per plan <b>{constraints?.max_moves ?? 10}</b>
                        </span>
                        <input
                            type="range"
                            min={1}
                            max={20}
                            value={constraints?.max_moves ?? 10}
                            onChange={(event) => void setConstraints({ max_moves: Number(event.target.value) })}
                        />
                        <small>Hard cap enforced by the solver</small>
                    </label>

                    <label className="pl-slider">
                        <span>
                            Labour budget per window <b>{Math.round((constraints?.labour_minutes_per_window ?? 240) / 60)} h</b>
                        </span>
                        <input
                            type="range"
                            min={60}
                            max={480}
                            step={30}
                            value={constraints?.labour_minutes_per_window ?? 240}
                            onChange={(event) => void setConstraints({ labour_minutes_per_window: Number(event.target.value) })}
                        />
                        <small>Moves are packed into windows under this budget</small>
                    </label>

                    <button
                        className={`pl-toggle${constraints?.cold_chain_locked ? ' is-on' : ''}`}
                        onClick={() => void setConstraints({ cold_chain_locked: !constraints?.cold_chain_locked })}
                    >
                        <span>
                            <strong>Lock cold-chain inventory</strong>
                            <small>No temperature-controlled SKU may be relocated</small>
                        </span>
                    </button>

                    {demand?.headline && (
                        <button
                            className={`pl-toggle${constraints?.locked_skus.includes(demand.headline.sku_id) ? ' is-on' : ''}`}
                            onClick={() => {
                                const id = demand.headline!.sku_id
                                const locked = constraints?.locked_skus ?? []
                                void setConstraints({
                                    locked_skus: locked.includes(id) ? locked.filter((s) => s !== id) : [...locked, id],
                                })
                            }}
                        >
                            <span>
                                <strong>Lock {demand.headline.sku_id}</strong>
                                <small>Pin {demand.headline.product_name} to its current slot</small>
                            </span>
                        </button>
                    )}

                    <button className="pl-run" onClick={() => void launch()} disabled={run?.status === 'RUNNING' || run?.status === 'HALTED'}>
                        <Play size={15} /> {solved ? 'Re-optimise with these constraints' : 'Run DeepAgent with these constraints'}
                    </button>
                </section>

                <section className="pl-panel">
                    <div className="pl-panel__head">
                        <span>Human-in-the-loop</span>
                    </div>
                    <ol className="pl-loop">
                        <li>Agents assess the warehouse</li>
                        <li>cuOpt solves under the constraints</li>
                        <li>OpenShell gates every privileged call</li>
                        <li>Planner approves or rejects each move</li>
                        <li>Approved manifest becomes WMS tasks</li>
                    </ol>
                </section>
            </aside>

            <main className="viewport-region pl-main">
                {!solved ? (
                    <div className="pl-empty">
                        <span className="pl-step">Step 1 of 3 · generate</span>
                        <h1>{run?.status === 'FAILED' ? 'The last run failed' : 'No move plan for this warehouse yet'}</h1>
                        {run?.status === 'FAILED' ? (
                            <p className="pl-failure">{run.error}</p>
                        ) : (
                            <p>
                                {dashboard?.problems.filter((p) => p.addressable).length
                                    ? 'Launch the DeepAgent run to turn the measured slotting gaps into a feasible move plan under the constraints on the left.'
                                    : 'Nothing is outstanding on the current warehouse. Generate a different dataset to run the flow again.'}
                            </p>
                        )}
                        <div className="pl-agents">
                            {AGENTS.map((agent) => (
                                <div className="pl-agent" key={agent.id}>
                                    <strong>{agent.name}</strong>
                                    <span>{agent.detail}</span>
                                </div>
                            ))}
                        </div>
                        <button className="pl-run pl-run--lg" onClick={() => void launch()}>
                            <Play size={16} /> Run DeepAgent
                        </button>
                        <p className="pl-gov">
                            <ShieldCheck size={14} /> Every privileged call is held by the OpenShell governor until an admin approves it. Nothing is
                            written to the WMS without your approval.
                        </p>
                    </div>
                ) : (
                    <div className="pl-solved">
                        <header className="pl-solved__head">
                            <div>
                                <span className="pl-step">Step 2 of 3 · review &amp; steer</span>
                                <h1>{run?.cuopt?.headline}</h1>
                                <p>{run?.cuopt?.explanation}</p>
                            </div>
                            <div className="pl-solved__metrics">
                                <div>
                                    <span>travel reduction</span>
                                    <b>{run?.cuopt?.metrics.travel_reduction_pct}%</b>
                                </div>
                                <div>
                                    <span>metre-picks removed</span>
                                    <b>{Math.round(run?.cuopt?.metrics.plan_value ?? 0).toLocaleString()}</b>
                                </div>
                                <div>
                                    <span>constraint violations</span>
                                    <b>{run?.cuopt?.metrics.constraint_violations}</b>
                                </div>
                            </div>
                        </header>

                        <div className="pl-windows">
                            {windows.map((day) => {
                                const inWindow = moves.filter((m) => m.day === day)
                                const minutes = inWindow.reduce((sum, m) => sum + m.labor_minutes, 0)
                                return (
                                    <div className="pl-window" key={day}>
                                        <span className="pl-window__label">Window {day + 1}</span>
                                        <strong>{inWindow.length} moves</strong>
                                        <span className="pl-window__dim">{minutes} min of labour</span>
                                    </div>
                                )
                            })}
                        </div>

                        <div className="pl-actions">
                            <span className="pl-count">
                                {counts.approved ?? 0} approved · {counts.rejected ?? 0} rejected · {counts.pending ?? 0} pending
                            </span>
                            <button className="decision" onClick={() => void decide('*', 'rejected')}>
                                Reject all
                            </button>
                            <button className="decision" onClick={() => void decide('*', 'approved')}>
                                Approve all
                            </button>
                            <button className="pl-send" onClick={() => void send()} disabled={committing || !(counts.approved ?? 0)}>
                                Send to WMS <ArrowRight size={15} />
                            </button>
                        </div>

                        {commitState?.status === 'AWAITING_APPROVAL' && (
                            <div className="pl-hold">
                                <ShieldCheck size={15} /> Write held by OpenShell — <code>write_wms</code> needs approval for this specific call.
                                Approve it in the governor; the write completes on its own.
                            </div>
                        )}
                        {(commitState?.status === 'DENIED' || commitState?.status === 'FAILED') && (
                            <div className="pl-hold pl-hold--bad">{commitState.error}</div>
                        )}

                        <div className="pl-manifest">
                            {moves.map((move) => {
                                const decision = decisions[move.id] ?? 'pending'
                                const open = expanded === move.id
                                return (
                                    <article className={`pl-move is-${decision}`} key={move.id}>
                                        <button className="pl-move__row" onClick={() => setExpanded(open ? null : move.id)}>
                                            <span className="pl-move__rank">{String(move.priority).padStart(2, '0')}</span>
                                            <span className="pl-move__sku">
                                                <strong>{move.sku}</strong>
                                                <small>
                                                    {move.code} · class {move.abc_class}
                                                </small>
                                            </span>
                                            <span className="pl-move__route">
                                                <code>{move.from_slot}</code> <ArrowRight size={12} /> <code>{move.to_slot}</code>
                                                <small>
                                                    window {move.day + 1} · {move.window}
                                                </small>
                                            </span>
                                            <span className="pl-move__benefit">
                                                <b>{move.benefit_hours_per_day}</b> hr/day
                                            </span>
                                            <span className="pl-move__labour">{move.labor_minutes} min</span>
                                            <ChevronDown size={15} className={`pl-move__caret${open ? ' is-open' : ''}`} />
                                        </button>
                                        {open && (
                                            <div className="pl-move__body">
                                                <p>{move.reason}</p>
                                                {move.alternatives.length > 0 && (
                                                    <div className="pl-alts">
                                                        <span className="pl-alts__title">Alternatives cuOpt passed over</span>
                                                        {move.alternatives.map((alt) => (
                                                            <div className="pl-alt" key={alt.slot_id}>
                                                                <code>{alt.slot_id}</code>
                                                                <span>{alt.distance_m}m from the pick face</span>
                                                                <b>{alt.gain_metre_picks.toLocaleString()} metre-picks</b>
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                        <div className="pl-move__decide">
                                            <button
                                                className={`decision${decision === 'approved' ? ' is-on' : ''}`}
                                                onClick={() => void decide(move.id, 'approved')}
                                            >
                                                <Check size={13} /> Approve
                                            </button>
                                            <button
                                                className={`decision${decision === 'rejected' ? ' is-off' : ''}`}
                                                onClick={() => void decide(move.id, 'rejected')}
                                            >
                                                <X size={13} /> Reject
                                            </button>
                                        </div>
                                    </article>
                                )
                            })}
                        </div>
                    </div>
                )}
            </main>
        </div>
    )
}
