import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Brain, ShieldCheck, Timer } from 'lucide-react'
import { useLive } from '../state/LiveState'
import type { Delegation, SolveRound } from '../api/client'
import './DeepAgentRun.css'

const SPECIALIST_LABEL: Record<string, string> = {
    demand_specialist: 'Demand',
    inventory_specialist: 'Inventory',
    warehouse_specialist: 'Warehouse',
}

function label(name: string): string {
    return SPECIALIST_LABEL[name] ?? name.replace(/_/g, ' ')
}

/** Later delegations to the same specialist are re-runs, which the operator should see as such. */
function withAttempts(delegations: Delegation[]): (Delegation & { attempt: number; attempts: number })[] {
    const totals = new Map<string, number>()
    delegations.forEach((d) => totals.set(d.name, (totals.get(d.name) ?? 0) + 1))
    const seen = new Map<string, number>()
    return delegations.map((d) => {
        const attempt = (seen.get(d.name) ?? 0) + 1
        seen.set(d.name, attempt)
        return { ...d, attempt, attempts: totals.get(d.name) ?? 1 }
    })
}

function roundState(round: SolveRound, chosen: number | null): string {
    if (round.status === 'running') return 'running'
    if (round.status === 'failed') return 'failed'
    return round.round === chosen ? 'chosen' : 'done'
}

/** Metre-picks a round removes, which is what makes rounds comparable. */
function benefit(round: SolveRound): number {
    if (!round.headroom_before || !round.headroom_after) return 0
    return Math.max(0, round.headroom_before.headroom_metre_picks - round.headroom_after.headroom_metre_picks)
}

export default function WorkflowScreen() {
    const { run, refreshOpenShell } = useLive()

    if (!run || run.status === 'IDLE') {
        return (
            <div className="app-body layout-full da-idle">
                <p>No run in flight.</p>
                <Link className="da-link" to="/plan">
                    Set the constraints and launch one <ArrowRight size={14} />
                </Link>
            </div>
        )
    }

    const { orchestrator, delegations, rounds } = run
    const elapsed = run.started_at
        ? ((new Date(run.finished_at ?? Date.now()).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(1)
        : '0.0'
    const specialists = withAttempts(delegations)
    const chosen = rounds.find((r) => r.round === run.chosen_round)
    const latestThought = orchestrator.thinking[orchestrator.thinking.length - 1] ?? ''
    const bestBenefit = Math.max(1, ...rounds.map(benefit))

    return (
        <div className="app-body layout-full da">
            <header className="da__head">
                <div className="da__id">
                    <span className="da__kicker">run</span>
                    <span className="da__runid">{run.id}</span>
                    <span className="da__goal">{run.goal}</span>
                </div>
                <div className="da__status">
                    <span className={`da__state da__state--${run.status.toLowerCase()}`}>{run.status}</span>
                    <span className="da__timer">
                        <Timer size={12} /> {elapsed}s
                    </span>
                    <Link className="da__gov" to="/openshell">
                        <ShieldCheck size={13} /> governed
                    </Link>
                </div>
            </header>

            {run.status === 'HALTED' && run.halted_on && (
                <div className="da__hold">
                    <AlertTriangle size={15} />
                    <div>
                        <strong>Waiting for approval.</strong> OpenShell has not granted{' '}
                        <code>{run.halted_on.service}</code>. {run.halted_on.reason}
                    </div>
                    <Link className="da__holdbtn" to="/openshell" onClick={() => void refreshOpenShell()}>
                        Approve in OpenShell <ArrowRight size={13} />
                    </Link>
                </div>
            )}

            {run.status === 'FAILED' && (
                <div className="da__hold da__hold--bad">
                    <AlertTriangle size={15} />
                    <div>
                        <strong>Run failed.</strong> {run.error}
                        <p>No plan is shown because none was produced.</p>
                    </div>
                </div>
            )}

            <section className={`da__orch is-${orchestrator.status}`}>
                <div className="da__orchhead">
                    <div className="da__orchid">
                        <span className="da__orchmark">
                            <Brain size={17} />
                        </span>
                        <div>
                            <strong>Orchestrator</strong>
                            <span>{orchestrator.model}</span>
                        </div>
                    </div>
                    <div className="da__orchmeta">
                        {orchestrator.harness.attached && (
                            <span className="da__harness">
                                Nemotron harness · <b>{orchestrator.harness.middleware.length}</b> middleware ·{' '}
                                <b>{orchestrator.harness.prompt_suffix_chars}</b> char profile
                            </span>
                        )}
                        <span className={`da__badge da__badge--${orchestrator.status}`}>{orchestrator.status}</span>
                    </div>
                </div>

                {orchestrator.harness.attached && (
                    <ul className="da__mw">
                        {orchestrator.harness.middleware.map((name) => (
                            <li key={name}>{name.replace(/Middleware$/, '')}</li>
                        ))}
                    </ul>
                )}

                {orchestrator.status === 'running' && latestThought && (
                    <p className="da__thought">{latestThought}</p>
                )}

                {orchestrator.narrative && <div className="da__narrative">{orchestrator.narrative}</div>}
            </section>

            <div className="da__grid">
                <section className="da__col">
                    <h2 className="da__colhead">
                        Delegated to specialists <span className="da__count">{specialists.length}</span>
                    </h2>
                    <div className="da__cards">
                        {specialists.length === 0 && <p className="da__empty">Nothing delegated yet.</p>}
                        {specialists.map((item) => (
                            <article className={`da__card is-${item.status}`} key={item.id || `${item.name}-${item.attempt}`}>
                                <header>
                                    <span className="da__avatar">{label(item.name).charAt(0)}</span>
                                    <strong>{label(item.name)}</strong>
                                    {item.attempts > 1 && (
                                        <span className="da__attempt">
                                            re-run {item.attempt}/{item.attempts}
                                        </span>
                                    )}
                                    <span className={`da__badge da__badge--${item.status}`}>{item.status}</span>
                                </header>
                                <p className="da__question">{item.question}</p>
                                {item.answer && <p className="da__answer">{item.answer}</p>}
                            </article>
                        ))}
                    </div>
                </section>

                <section className="da__col">
                    <h2 className="da__colhead">
                        cuOpt solve rounds <span className="da__count">{rounds.length}</span>
                    </h2>
                    <div className="da__cards">
                        {rounds.length === 0 && <p className="da__empty">The solver has not run yet.</p>}
                        {rounds.map((round) => (
                            <article className={`da__round is-${roundState(round, run.chosen_round)}`} key={round.round}>
                                <header>
                                    <span className="da__rno">R{round.round}</span>
                                    <span className="da__rmeta">
                                        {round.max_moves} moves · {round.time_limit_s}s budget
                                        {round.solver_seconds != null && ` · solved in ${round.solver_seconds}s`}
                                    </span>
                                    {round.round === run.chosen_round && <span className="da__chosen">recommended</span>}
                                </header>
                                {round.headroom_before && (
                                    <div className="da__impact">
                                        <span className="da__bar">
                                            <span
                                                className="da__barfill"
                                                style={{ width: `${Math.round((benefit(round) / bestBenefit) * 100)}%` }}
                                            />
                                        </span>
                                        <span className="da__impactpct">
                                            {Math.round((benefit(round) / bestBenefit) * 100)}% of best gain
                                        </span>
                                    </div>
                                )}
                                {round.kpis_after && round.kpis_before && (
                                    <dl className="da__delta">
                                        <div>
                                            <dt>travel / pick</dt>
                                            <dd>
                                                {round.kpis_before.avg_distance_per_pick_m}m →{' '}
                                                <b className="da__up">{round.kpis_after.avg_distance_per_pick_m}m</b>
                                            </dd>
                                        </div>
                                        <div>
                                            <dt>forward pick</dt>
                                            <dd>
                                                {round.kpis_before.forward_pick_coverage_pct}% →{' '}
                                                <b className="da__up">{round.kpis_after.forward_pick_coverage_pct}%</b>
                                            </dd>
                                        </div>
                                        <div>
                                            <dt>daily travel</dt>
                                            <dd>
                                                <b>{Math.round(round.kpis_after.daily_travel_km)}</b> km
                                            </dd>
                                        </div>
                                    </dl>
                                )}
                            </article>
                        ))}
                    </div>
                </section>
            </div>

            <footer className="da__foot">
                <div className="da__policies">
                    {run.guardrails.map((item, index) => (
                        <span key={`g-${index}`} className={`da__pill ${item.allowed ? 'is-ok' : 'is-bad'}`}>
                            rails · {item.stage} {item.allowed ? 'passed' : 'blocked'}
                        </span>
                    ))}
                    {run.openshell.map((item, index) => (
                        <span key={`o-${index}`} className={`da__pill ${item.allowed ? 'is-ok' : 'is-bad'}`}>
                            openshell · {item.service} {item.allowed ? 'granted' : 'held'}
                        </span>
                    ))}
                </div>
                {run.status === 'COMPLETE' && (
                    <div className="da__outcome">
                        {chosen ? (
                            <>
                                Round {chosen.round} recommended — {run.moves.length} moves pending approval.{' '}
                                <Link to="/plan">Review the manifest →</Link>
                            </>
                        ) : (
                            <>No relocation would shorten travel, so the layout is already the best available.</>
                        )}
                    </div>
                )}
            </footer>
        </div>
    )
}
