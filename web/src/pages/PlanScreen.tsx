import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
    ArrowRight,
    BrainCircuit,
    Check,
    ChevronDown,
    ChevronRight,
    Lock,
    Play,
    RotateCcw,
    ShieldCheck,
    Sliders,
    X,
} from 'lucide-react'
import { api, type CommitState, type Move } from '../api/client'
import { useLive } from '../state/LiveState'
import './PlanScreen.css'

const AGENTS = [
    { id: 'supervisor', name: 'DeepAgent Orchestrator', detail: 'Nemotron on NIM · plans & delegates', lead: true },
    { id: 'demand', name: 'Demand Intelligence', detail: 'forecast · velocity · ABC class' },
    { id: 'inventory', name: 'Inventory Intelligence', detail: 'stock health · replenishment risk' },
    { id: 'warehouse', name: 'Warehouse Intelligence', detail: 'layout · distance matrix · capacity' },
    { id: 'optimize', name: 'Decision Optimization', detail: 'NVIDIA cuOpt · constrained solve' },
]

const STATUS_PILL: Record<string, string> = {
    approved: 'pl-st--ok',
    rejected: 'pl-st--blocked',
    pending: 'pl-st--wait',
}

const STATUS_LABEL: Record<string, string> = {
    approved: 'Approved',
    rejected: 'Rejected',
    pending: 'Awaiting Approval',
}

export default function PlanScreen() {
    const { run, dashboard, demand, startRun, setConstraints, decide, refreshWarehouse, refreshRun } = useLive()
    const navigate = useNavigate()
    const [openMove, setOpenMove] = useState<string | null>(null)
    const [commitState, setCommitState] = useState<CommitState | null>(null)
    const [committing, setCommitting] = useState(false)

    const constraints = run?.constraints
    const moves = run?.moves ?? []
    const solved = run?.status === 'COMPLETE' && moves.length > 0
    const decisions = run?.decisions ?? {}
    const busy = run?.status === 'RUNNING' || run?.status === 'HALTED'

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

    const verdict = (move: Move) => decisions[move.id] ?? 'pending'
    const approved = moves.filter((m) => verdict(m) === 'approved').length
    const rejected = moves.filter((m) => verdict(m) === 'rejected').length
    const pending = moves.length - approved - rejected

    const windows = [...new Set(moves.map((m) => m.day))].sort((a, b) => a - b)
    const totalBenefit = moves.reduce((sum, m) => sum + m.benefit_hours_per_day, 0)
    const totalLabour = moves.reduce((sum, m) => sum + m.labor_minutes, 0)
    const lockedHeadline = demand?.headline ? constraints?.locked_skus.includes(demand.headline.sku_id) : false

    return (
        <div className="app-body layout-plan">
            <aside className="info-region">
                <section className="panel pl-console">
                    <div className="block__head">
                        <span className="block__title">
                            <Sliders size={12} /> Planner constraints
                        </span>
                        <span className="block__link">{solved ? `solve #${run?.id?.slice(-4)}` : 'not solved yet'}</span>
                    </div>
                    <p className="pl-console__hint">
                        The hard limits cuOpt must respect. Change one, then re-optimise — the solver re-solves the whole
                        warehouse and Nemotron explains what the change cost you.
                    </p>

                    <div className="pl-constraints">
                        <div className="pl-range">
                            <div className="pl-range__row">
                                <span className="pl-range__label">Max moves per plan</span>
                                <span className="pl-range__value">{constraints?.max_moves ?? '—'}</span>
                            </div>
                            <input
                                type="range"
                                min={1}
                                max={40}
                                value={constraints?.max_moves ?? 10}
                                onChange={(event) => void setConstraints({ max_moves: Number(event.target.value) })}
                            />
                            <span className="pl-range__detail">Hard cap enforced by cuOpt</span>
                        </div>

                        <div className="pl-range">
                            <div className="pl-range__row">
                                <span className="pl-range__label">Labour budget per window</span>
                                <span className="pl-range__value">
                                    {Math.round((constraints?.labour_minutes_per_window ?? 240) / 60)} h
                                </span>
                            </div>
                            <input
                                type="range"
                                min={30}
                                max={960}
                                step={30}
                                value={constraints?.labour_minutes_per_window ?? 240}
                                onChange={(event) =>
                                    void setConstraints({ labour_minutes_per_window: Number(event.target.value) })
                                }
                            />
                            <span className="pl-range__detail">Operator-minutes available in each move window</span>
                        </div>

                        <button
                            className={`pl-toggle${constraints?.cold_chain_locked ? ' is-on' : ''}`}
                            onClick={() => void setConstraints({ cold_chain_locked: !constraints?.cold_chain_locked })}
                        >
                            <span className="pl-toggle__switch" />
                            <span className="pl-toggle__body">
                                <span className="pl-toggle__label">Lock cold-chain inventory</span>
                                <span className="pl-toggle__detail">No chilled or frozen SKU may be relocated</span>
                            </span>
                            {constraints?.cold_chain_locked && <Lock size={12} className="pl-toggle__ico" />}
                        </button>

                        {demand?.headline && (
                            <button
                                className={`pl-toggle${lockedHeadline ? ' is-on' : ''}`}
                                onClick={() => {
                                    const id = demand.headline!.sku_id
                                    const locked = constraints?.locked_skus ?? []
                                    void setConstraints({
                                        locked_skus: locked.includes(id)
                                            ? locked.filter((sku) => sku !== id)
                                            : [...locked, id],
                                    })
                                }}
                            >
                                <span className="pl-toggle__switch" />
                                <span className="pl-toggle__body">
                                    <span className="pl-toggle__label">
                                        Lock {demand.headline.sku_id} ({demand.headline.product_name})
                                    </span>
                                    <span className="pl-toggle__detail">Pin the SKU to its current slot</span>
                                </span>
                                {lockedHeadline && <Lock size={12} className="pl-toggle__ico" />}
                            </button>
                        )}
                    </div>

                    <button className={`pl-solve${solved ? ' is-dirty' : ''}`} onClick={() => void launch()} disabled={busy}>
                        {busy ? <span className="pl-solve__spin" /> : <Play size={13} />}
                        {busy ? 'DeepAgent running…' : solved ? 'Re-optimise with cuOpt' : 'Run DeepAgent with these constraints'}
                    </button>
                </section>

                <section className="panel">
                    <div className="block__head">
                        <span className="block__title">Human-in-the-loop</span>
                    </div>
                    <ol className="pl-loop">
                        <li className={run ? 'is-done' : ''}>Agents assess the warehouse</li>
                        <li className={solved ? 'is-done' : ''}>cuOpt optimises under the constraints</li>
                        <li className={solved ? 'is-active' : ''}>Planner reviews every move</li>
                        <li className={approved > 0 ? 'is-done' : ''}>Moves approved or rejected</li>
                        <li className={committing ? 'is-active' : ''}>OpenShell gates the WMS write</li>
                        <li className={commitState?.status === 'COMMITTED' ? 'is-done' : ''}>Approved plan → WMS</li>
                    </ol>
                </section>
            </aside>

            <main className="pl-main">
                {!solved ? (
                    <div className="pl-launch">
                        <span className="pl-launch__badge">Step 1 of 3 · generate</span>
                        <h1 className="pl-launch__title">
                            {run?.status === 'FAILED' ? 'The last run failed' : 'No move plan for this warehouse yet'}
                        </h1>
                        {run?.status === 'FAILED' ? (
                            <p className="pl-launch__lead">{run.error}</p>
                        ) : (
                            <p className="pl-launch__lead">
                                {demand?.headline ? (
                                    <>
                                        <b>
                                            {demand.headline.product_name} demand +{demand.headline.promotion_uplift_pct}%
                                        </b>{' '}
                                        is the strongest signal in the next {demand.horizon_days} days at{' '}
                                        {Math.round(demand.headline.picks_per_day)} picks/day. Launch the DeepAgent run to turn
                                        that signal into a feasible, labour-bounded move plan under the constraints on the left.
                                    </>
                                ) : (
                                    'Launch the DeepAgent run to turn the measured slotting gaps into a feasible move plan.'
                                )}
                            </p>
                        )}

                        <div className="pl-launch__agents">
                            {AGENTS.map((agent) => (
                                <div
                                    className={`pl-launch__agent${agent.lead ? ' pl-launch__agent--sup' : ''}`}
                                    key={agent.id}
                                >
                                    <span className="pl-launch__dot" />
                                    <span>
                                        <b>{agent.name}</b>
                                        <span>{agent.detail}</span>
                                    </span>
                                </div>
                            ))}
                        </div>

                        <div className="pl-launch__actions">
                            <button className="pl-btn pl-btn--primary" onClick={() => void launch()} disabled={busy}>
                                <Play size={14} /> Run DeepAgent
                            </button>
                            <span className="pl-launch__note">
                                <ShieldCheck size={13} /> Every privileged call is held by the OpenShell governor until an admin
                                approves it. Nothing is written to the WMS without your approval.
                            </span>
                        </div>

                        <ol className="pl-launch__steps">
                            <li className="is-active">
                                <b>Generate</b>
                                <span>Agents + cuOpt produce a labour-bounded plan</span>
                            </li>
                            <li>
                                <b>Review &amp; steer</b>
                                <span>Change constraints, re-optimise, approve moves</span>
                            </li>
                            <li>
                                <b>Execute</b>
                                <span>Approved manifest becomes WMS move tasks</span>
                            </li>
                        </ol>
                    </div>
                ) : (
                    <>
                        <div className="pl-head">
                            <div>
                                <h1 className="pl-head__title">{run?.cuopt?.headline}</h1>
                                <p className="pl-head__sub">
                                    {moves.length} moves · {windows.length} window{windows.length === 1 ? '' : 's'} ·{' '}
                                    {Math.round(totalLabour)} min of labour ·{' '}
                                    <b>{run?.cuopt?.metrics.travel_reduction_pct}%</b> picker travel reduction
                                </p>
                            </div>
                            <div className="pl-head__right">
                                <span className="pl-value">
                                    <small>Travel saved</small>
                                    <b>{totalBenefit.toFixed(1)} hr/day</b>
                                </span>
                                <button className="pl-btn pl-btn--ghost" onClick={() => navigate('/workflow')}>
                                    <BrainCircuit size={14} /> Agent run
                                </button>
                                <button className="pl-btn pl-btn--ghost" onClick={() => void decide('*', 'approved')}>
                                    <Check size={14} /> Approve all
                                </button>
                                <button
                                    className="pl-btn pl-btn--primary"
                                    onClick={() => void send()}
                                    disabled={committing || approved === 0}
                                >
                                    <ArrowRight size={14} /> Send {approved} to WMS
                                </button>
                            </div>
                        </div>

                        {commitState?.status === 'AWAITING_APPROVAL' && (
                            <div className="pl-banner pl-banner--think">
                                <ShieldCheck size={15} />
                                <div>
                                    <b>Held by OpenShell</b>
                                    <p>
                                        <code>{commitState.service}</code> is a per-call service, so this specific write needs its
                                        own approval. Approve it in the governor — the write completes on its own.
                                    </p>
                                </div>
                                <Link className="pl-banner__link" to="/openshell">
                                    Open governor
                                </Link>
                            </div>
                        )}
                        {(commitState?.status === 'DENIED' || commitState?.status === 'FAILED') && (
                            <div className="pl-banner">
                                <X size={15} />
                                <div>The WMS write did not happen: {commitState.error}</div>
                            </div>
                        )}

                        <section className="pl-section">
                            <div className="block__head">
                                <span className="block__title">Execution windows</span>
                                <span className="block__link">
                                    Moves are packed under a {Math.round((constraints?.labour_minutes_per_window ?? 240) / 60)} h
                                    labour budget
                                </span>
                            </div>
                            <div className="pl-timeline">
                                {windows.map((day, index) => {
                                    const inWindow = moves.filter((m) => m.day === day)
                                    const minutes = inWindow.reduce((sum, m) => sum + m.labor_minutes, 0)
                                    const benefit = inWindow.reduce((sum, m) => sum + m.benefit_hours_per_day, 0)
                                    return (
                                        <div className={`pl-period${index === 0 ? ' is-now' : ''}`} key={day}>
                                            <div className="pl-period__head">
                                                <span className="pl-period__label">Window {day + 1}</span>
                                                <span className="pl-period__date">{inWindow[0]?.window ?? ''}</span>
                                            </div>
                                            <div className="pl-period__moves">{inWindow.length} moves</div>
                                            <div className="pl-period__benefit">+{benefit.toFixed(1)} hr/day</div>
                                            <div className="pl-period__note">{minutes} min of the labour budget</div>
                                        </div>
                                    )
                                })}
                            </div>
                        </section>

                        <section className="pl-section">
                            <div className="block__head">
                                <span className="block__title">Move manifest</span>
                                <span className="muted pl-tiny">
                                    {approved} approved · {rejected} rejected · {pending} awaiting approval
                                </span>
                            </div>
                            <div className="pl-manifest">
                                <div className="pl-row pl-row--head">
                                    <span>#</span>
                                    <span>Window</span>
                                    <span>SKU</span>
                                    <span>Move</span>
                                    <span>Benefit</span>
                                    <span>Labour</span>
                                    <span>Status</span>
                                    <span>Decision</span>
                                    <span />
                                </div>
                                {moves.map((move) => {
                                    const state = verdict(move)
                                    const open = openMove === move.id
                                    return (
                                        <div className={`pl-move${open ? ' is-open' : ''}`} key={move.id}>
                                            <div className="pl-row" onClick={() => setOpenMove(open ? null : move.id)}>
                                                <span className="pl-prio">{move.priority}</span>
                                                <span className="pl-when">
                                                    Window {move.day + 1}
                                                    <small>{move.window}</small>
                                                </span>
                                                <span className="pl-sku">
                                                    {move.code}
                                                    <small>{move.sku}</small>
                                                </span>
                                                <span className="pl-route">
                                                    <code>{move.from_slot}</code>
                                                    <ArrowRight size={11} />
                                                    <code>{move.to_slot}</code>
                                                    <em>class {move.abc_class}</em>
                                                </span>
                                                <span className="pl-benefit">
                                                    {move.benefit_hours_per_day.toFixed(1)} hr/day
                                                    <small>picker travel</small>
                                                </span>
                                                <span className="pl-conf">{move.labor_minutes}m</span>
                                                <span className={`pl-st ${STATUS_PILL[state]}`}>{STATUS_LABEL[state]}</span>
                                                <span
                                                    className="pl-decide"
                                                    onClick={(event) => event.stopPropagation()}
                                                >
                                                    <button
                                                        className={`pl-mini pl-mini--ok${state === 'approved' ? ' is-on' : ''}`}
                                                        onClick={() => void decide(move.id, 'approved')}
                                                        title="Approve this move"
                                                    >
                                                        <Check size={12} />
                                                    </button>
                                                    <button
                                                        className={`pl-mini pl-mini--no${state === 'rejected' ? ' is-on' : ''}`}
                                                        onClick={() => void decide(move.id, 'rejected')}
                                                        title="Reject this move"
                                                    >
                                                        <X size={12} />
                                                    </button>
                                                </span>
                                                <span className="pl-caret">
                                                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                                </span>
                                            </div>

                                            {open && (
                                                <div className="pl-detail fade-in">
                                                    <div className="pl-detail__main">
                                                        <div className="pl-detail__block">
                                                            <span className="pl-detail__label">
                                                                <BrainCircuit size={11} /> Why cuOpt chose this slot
                                                            </span>
                                                            <p>{move.reason}</p>
                                                        </div>
                                                        <div className="pl-detail__block">
                                                            <span className="pl-detail__label">Execution</span>
                                                            <p>
                                                                Scheduled into window {move.day + 1} ({move.window}) because the
                                                                earlier windows were already at their labour budget. The move
                                                                itself costs {move.labor_minutes} operator-minutes.
                                                            </p>
                                                        </div>
                                                    </div>
                                                    <div className="pl-detail__side">
                                                        <div className="pl-detail__stats">
                                                            <span>
                                                                <small>Travel saved</small>
                                                                {move.benefit_hours_per_day.toFixed(1)} hr/day
                                                            </span>
                                                            <span>
                                                                <small>Labour</small>
                                                                {move.labor_minutes} min
                                                            </span>
                                                            <span>
                                                                <small>Class</small>
                                                                {move.abc_class}
                                                            </span>
                                                            <span>
                                                                <small>Priority</small>
                                                                {move.priority}
                                                            </span>
                                                        </div>
                                                        <span className="pl-detail__label">Alternatives compared</span>
                                                        {move.alternatives.length === 0 ? (
                                                            <p>No other slot improved on the chosen one.</p>
                                                        ) : (
                                                            move.alternatives.map((alt) => (
                                                                <div className="pl-altrow" key={alt.slot_id}>
                                                                    <b>{alt.slot_id}</b>
                                                                    <span>{alt.distance_m}m from the pick face</span>
                                                                    <i className="is-rej">
                                                                        Rejected — {Math.round(alt.gain_metre_picks).toLocaleString()}{' '}
                                                                        metre-picks
                                                                    </i>
                                                                </div>
                                                            ))
                                                        )}
                                                        <div className="pl-detail__actions">
                                                            <button
                                                                className="pl-mini pl-mini--ok"
                                                                onClick={() => void decide(move.id, 'approved')}
                                                                disabled={state === 'approved'}
                                                            >
                                                                <Check size={12} /> Approve
                                                            </button>
                                                            <button
                                                                className="pl-mini"
                                                                onClick={() => void decide(move.id, 'rejected')}
                                                                disabled={state === 'rejected'}
                                                            >
                                                                <X size={12} /> Reject
                                                            </button>
                                                            <button
                                                                className="pl-mini"
                                                                onClick={() => void decide(move.id, 'pending')}
                                                                disabled={state === 'pending'}
                                                            >
                                                                <RotateCcw size={12} /> Reset
                                                            </button>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )
                                })}
                            </div>
                        </section>

                        <div className="pl-bottom">
                            <section className="pl-section pl-obj">
                                <div className="block__head">
                                    <span className="block__title">Optimisation objective</span>
                                </div>
                                <p className="pl-obj__formula">
                                    Maximise Σ (picks/day × metres saved) subject to the move cap, the per-window labour budget,
                                    slot capacity, temperature class, and the locked SKU list.
                                </p>
                                <div className="pl-obj__terms">
                                    {Object.entries(run?.cuopt?.metrics ?? {}).map(([key, value]) => (
                                        <div className="pl-objterm" key={key}>
                                            <span className="pl-objterm__label">{key.replace(/_/g, ' ')}</span>
                                            <span className="pl-objterm__track">
                                                <span className="is-pos" style={{ width: '100%' }} />
                                            </span>
                                            <span className="pl-objterm__val">
                                                {typeof value === 'number' ? value.toLocaleString() : String(value)}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            </section>

                            <section className="pl-section pl-reason">
                                <div className="block__head">
                                    <span className="block__title">What the optimiser reported</span>
                                </div>
                                <div className="pl-reason__item">
                                    <span className="pl-reason__label">cuOpt</span>
                                    <p>{run?.cuopt?.explanation}</p>
                                </div>
                                {dashboard?.problems
                                    .filter((problem) => !problem.addressable)
                                    .map((problem) => (
                                        <div className="pl-reason__item" key={problem.id}>
                                            <span className="pl-reason__label">Not solvable by slotting</span>
                                            <p>{problem.detail}</p>
                                        </div>
                                    ))}
                            </section>
                        </div>
                    </>
                )}
            </main>
        </div>
    )
}
