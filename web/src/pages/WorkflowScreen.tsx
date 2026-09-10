import { Link } from 'react-router-dom'
import { Activity, AlertTriangle, ArrowRight, Cpu, ShieldCheck } from 'lucide-react'
import { useLive } from '../state/LiveState'
import './DeepAgentRun.css'

const STATE_CLASS: Record<string, string> = {
    pending: 'queued',
    running: 'running',
    done: 'ok',
    blocked: 'held',
    failed: 'failed',
}

export default function WorkflowScreen() {
    const { run, openshell, refreshOpenShell } = useLive()

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

    const done = run.stages.filter((s) => s.status === 'done').length
    const elapsed = run.started_at ? ((new Date(run.finished_at ?? Date.now()).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(1) : '0.0'
    const agentFor = (key: string) =>
        key === 'plan' ? run.agents.find((a) => a.role === 'supervisor') : undefined

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
                    <span className="da__timer">{elapsed}s</span>
                    <Link className="da__gov" to="/openshell">
                        <ShieldCheck size={13} /> governed
                    </Link>
                </div>
            </header>

            {run.status === 'HALTED' && run.halted_on && (
                <div className="da__hold">
                    <AlertTriangle size={15} />
                    <div>
                        <strong>Process halted.</strong> OpenShell has not granted <code>{run.halted_on.service}</code>. {run.halted_on.reason}
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

            {run.status === 'COMPLETE' && (
                <div className="da__done">
                    {run.moves.length > 0 ? (
                        <>
                            Trace closed — cuOpt returned a {run.moves.length}-move plan at {run.cuopt?.metrics.travel_reduction_pct}% travel
                            reduction. <Link to="/plan">Review the move manifest →</Link>
                        </>
                    ) : (
                        <>Trace closed — no relocation would shorten travel, so the layout is already the best available.</>
                    )}
                </div>
            )}

            <div className="da__bar">
                Streaming spans — orchestrator delegating to intelligence agents and the cuOpt solver
                <span className="da__progress">
                    {done}/{run.stages.length}
                </span>
                complete
            </div>

            <div className="da__body">
                <div className="da__spans">
                    <div className="da__spanhead">
                        <span>span</span>
                        <span>dur</span>
                        <span>state</span>
                    </div>
                    {run.stages.map((stage) => {
                        const agent = agentFor(stage.key)
                        const specialists = stage.key === 'specialists' ? run.agents.filter((a) => a.role === 'specialist') : []
                        return (
                            <div className={`da__span is-${STATE_CLASS[stage.status] ?? 'queued'}`} key={stage.key}>
                                <div className="da__spanrow">
                                    <div className="da__spanid">
                                        <Cpu size={14} />
                                        <div>
                                            <strong>{stage.key}</strong>
                                            <span>{stage.label}</span>
                                        </div>
                                    </div>
                                    <span className="da__dur">{stage.duration_ms ? `${(stage.duration_ms / 1000).toFixed(2)}s` : '—'}</span>
                                    <span className={`da__badge da__badge--${STATE_CLASS[stage.status] ?? 'queued'}`}>{stage.status}</span>
                                </div>
                                {stage.detail && <div className="da__spandetail">{stage.detail}</div>}
                                {agent?.reasoning.map((line, index) => (
                                    <div className="da__line" key={index}>
                                        <span className="da__lineno">{index + 1}</span>
                                        {line}
                                    </div>
                                ))}
                                {specialists.map((specialist) => (
                                    <div className="da__sub" key={specialist.name}>
                                        <div className="da__subhead">
                                            <strong>{specialist.name}</strong>
                                            <span>
                                                {specialist.telemetry.duration_ms}ms · {specialist.telemetry.completion_tokens} out /{' '}
                                                {specialist.telemetry.prompt_tokens} in tokens
                                            </span>
                                        </div>
                                        {specialist.reasoning.map((line, index) => (
                                            <div className="da__line" key={index}>
                                                <span className="da__lineno">{index + 1}</span>
                                                {line}
                                            </div>
                                        ))}
                                        {specialist.findings.length > 0 && (
                                            <div className="da__chips">
                                                {specialist.findings.map((finding) => (
                                                    <span key={finding}>{finding}</span>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )
                    })}
                </div>

                <aside className="da__rail">
                    <section className="da__card">
                        <div className="da__cardhead">
                            <Activity size={14} /> run objective
                        </div>
                        <p>{run.goal}</p>
                        <div className="da__kv">
                            <span>max moves</span>
                            <b>{run.constraints.max_moves}</b>
                        </div>
                        <div className="da__kv">
                            <span>labour per window</span>
                            <b>{Math.round(run.constraints.labour_minutes_per_window / 60)} h</b>
                        </div>
                        <div className="da__kv">
                            <span>cold-chain locked</span>
                            <b>{run.constraints.cold_chain_locked ? 'on' : 'off'}</b>
                        </div>
                        <div className="da__kv">
                            <span>locked SKUs</span>
                            <b>{run.constraints.locked_skus.length || '—'}</b>
                        </div>
                    </section>

                    <section className="da__card">
                        <div className="da__cardhead">
                            <Cpu size={14} /> telemetry
                        </div>
                        <div className="da__kv">
                            <span>tokens in</span>
                            <b>{run.tokens.prompt || '—'}</b>
                        </div>
                        <div className="da__kv">
                            <span>tokens out</span>
                            <b>{run.tokens.completion || '—'}</b>
                        </div>
                        <div className="da__kv">
                            <span>model calls</span>
                            <b>{run.tokens.calls}</b>
                        </div>
                        {run.cuopt && (
                            <>
                                <div className="da__kv">
                                    <span>cuOpt solve</span>
                                    <b>{run.cuopt.telemetry.duration_ms}ms</b>
                                </div>
                                <div className="da__kv">
                                    <span>model size</span>
                                    <b>
                                        {run.cuopt.telemetry.candidate_skus} SKUs · {run.cuopt.telemetry.slots} slots
                                    </b>
                                </div>
                            </>
                        )}
                        {run.agents[0]?.telemetry.model && (
                            <div className="da__kv da__kv--wrap">
                                <span>serving</span>
                                <b>{run.agents[0].telemetry.model}</b>
                            </div>
                        )}
                    </section>

                    <section className="da__card">
                        <div className="da__cardhead">
                            <ShieldCheck size={14} /> policy
                        </div>
                        {run.guardrails.length === 0 && run.openshell.length === 0 && <p className="da__dim">No policy decision yet.</p>}
                        {run.guardrails.map((event, index) => (
                            <div className="da__policy" key={`g${index}`}>
                                <span className={`da__pill da__pill--${event.allowed ? 'ok' : 'bad'}`}>{event.allowed ? 'allowed' : 'blocked'}</span>
                                guardrails · {event.stage} rail
                            </div>
                        ))}
                        {run.openshell.map((event, index) => (
                            <div className="da__policy" key={`o${index}`}>
                                <span className={`da__pill da__pill--${event.allowed ? 'ok' : 'bad'}`}>{event.allowed ? 'granted' : 'held'}</span>
                                <code>{event.service}</code>
                            </div>
                        ))}
                        {openshell && openshell.pending.length > 0 && (
                            <Link className="da__holdbtn da__holdbtn--sm" to="/openshell">
                                {openshell.pending.length} awaiting approval
                            </Link>
                        )}
                    </section>
                </aside>
            </div>

            <div className="da__log">
                {run.events
                    .slice()
                    .reverse()
                    .map((event, index) => (
                        <div className={`da__logline da__logline--${event.kind}`} key={index}>
                            <time>{new Date(event.at).toLocaleTimeString()}</time>
                            <span>{event.message}</span>
                        </div>
                    ))}
            </div>
        </div>
    )
}
