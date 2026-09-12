import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Check, ChevronDown, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { api, type ApprovalScope, type GovernorService } from '../api/client'
import { useLive } from '../state/LiveState'
import './OpenShellConsole.css'

/* The governance console is deliberately not part of the planner application.
   It is a separate admin surface, on its own palette, over the whole viewport. */

/** Subagent token usage is not observable, so show a dash rather than a false zero. */
function count(value: number | null | undefined): string {
    return typeof value === 'number' ? value.toLocaleString() : '—'
}

const KIND: Record<string, { kind: string; cls: string }> = {
    'llm.supervisor': { kind: 'llm', cls: 'os-kind--llm' },
    'llm.subagent': { kind: 'llm', cls: 'os-kind--llm' },
    solve_slotting: { kind: 'tools', cls: 'os-kind--tools' },
    read_wms: { kind: 'http', cls: 'os-kind--http' },
    write_wms: { kind: 'secrets', cls: 'os-kind--secrets' },
    read_erp: { kind: 'erp', cls: 'os-kind--erp' },
    read_forecast: { kind: 'db', cls: 'os-kind--db' },
    load_policy: { kind: 'tools', cls: 'os-kind--tools' },
    create_approval: { kind: 'tools', cls: 'os-kind--tools' },
}

const kindOf = (service: string) => KIND[service] ?? { kind: 'http', cls: 'os-kind--http' }

const modeClass = (approval: string) =>
    approval.includes('call') ? 'os-mode--every' : approval.includes('user') ? 'os-mode--once' : 'os-mode--auto'

const serviceName = (service: GovernorService) => service.name ?? service.service ?? ''

function ScopeMenu({ onPick, allLabel = false }: { onPick: (scope: ApprovalScope) => void; allLabel?: boolean }) {
    const all = allLabel ? ' all' : ''
    return (
        <div className="os-scope">
            <button onClick={() => onPick('call')}>
                <b>Approve{all} for this call</b>
                <span>Per-call services hold the next one again</span>
            </button>
            <button onClick={() => onPick('session')}>
                <b>Approve{all} for this dataset</b>
                <span>Stands until a new warehouse is generated</span>
            </button>
            <button onClick={() => onPick('always')}>
                <b>Approve{all} always</b>
                <span>Survives a new warehouse; revoke to undo</span>
            </button>
        </div>
    )
}

export default function OpenShellConsole() {
    const { openshell, refreshOpenShell } = useLive()
    const [revokingAll, setRevokingAll] = useState(false)
    const [scopeMenu, setScopeMenu] = useState<string | null>(null)
    const [bulkBusy, setBulkBusy] = useState(false)
    const [preBusy, setPreBusy] = useState(false)

    useEffect(() => {
        void refreshOpenShell()
    }, [refreshOpenShell])

    const resolve = async (requestId: string, approve: boolean, scope: ApprovalScope = 'call') => {
        await api.resolve(requestId, approve, scope)
        await refreshOpenShell()
    }

    const resolveAll = async (approve: boolean, scope: ApprovalScope = 'call') => {
        setBulkBusy(true)
        try {
            await api.resolveAll(approve, scope)
            await refreshOpenShell()
        } finally {
            setBulkBusy(false)
        }
    }

    const revoke = async (user: string, service: string) => {
        await api.revoke(user, service)
        await refreshOpenShell()
    }

    const preapprove = async (scope: ApprovalScope) => {
        setPreBusy(true)
        try {
            await api.preapprove(scope)
            await refreshOpenShell()
        } finally {
            setPreBusy(false)
        }
    }

    const revokeAll = async () => {
        setRevokingAll(true)
        try {
            await api.revokeAll()
            await refreshOpenShell()
        } finally {
            setRevokingAll(false)
        }
    }

    const header = (
        <header className="os-head">
            <div className="os-head__brand">
                <span className="os-logo">EY</span>
                <span className="os-head__titles">
                    <span className="os-head__title">OpenShell Governance Console</span>
                    <span className="os-head__tag">Agent egress control · approvals · audit</span>
                </span>
            </div>
            <div className="os-head__right">
                <span className="os-status">
                    governor{' '}
                    {openshell ? (
                        <b className="online">online</b>
                    ) : (
                        <b className="offline">unreachable</b>
                    )}
                    {openshell ? ` · ${openshell.services.length} services` : ''}
                </span>
                <Link className="os-head__link" to="/cockpit">
                    <ArrowLeft size={12} /> Back to WarehouseIQ
                </Link>
                <div className="os-user">
                    <span className="os-user__avatar">EY</span>
                    <span className="os-user__id">
                        <b>Platform admin</b>
                        <i>governance operator</i>
                    </span>
                </div>
            </div>
        </header>
    )

    if (!openshell) {
        return (
            <div className="os-shell">
                {header}
                <main className="os-main">
                    <section className="os-card">
                        <div className="os-empty">Reading the governor…</div>
                    </section>
                </main>
            </div>
        )
    }

    const { telemetry } = openshell
    const held = openshell.pending.length

    return (
        <div className="os-shell">
            {header}

            <main className="os-main">
                <section className={`os-card os-approvals${held > 0 ? ' os-approvals--active' : ''}`}>
                    <div className="os-sec__head os-sec__head--row">
                        <div>
                            <h2 className="os-sec__title">
                                <ShieldAlert size={15} className="os-approvals__ico" /> PENDING APPROVALS
                            </h2>
                            <p className="os-sec__desc">
                                Agent egress calls held by the governor. Approving flips the service on and releases the held
                                call — the WarehouseIQ run resumes from where it paused.
                            </p>
                        </div>
                        {held > 1 && (
                            <div className="os-approve__split os-approve__split--bulk">
                                <button className="os-approve" disabled={bulkBusy} onClick={() => void resolveAll(true)}>
                                    <Check size={12} /> {bulkBusy ? 'Approving…' : `Approve all (${held})`}
                                </button>
                                <button
                                    className="os-approve os-approve__more"
                                    title="Approval scope"
                                    disabled={bulkBusy}
                                    onClick={() => setScopeMenu(scopeMenu === 'bulk' ? null : 'bulk')}
                                >
                                    <ChevronDown size={12} />
                                </button>
                                {scopeMenu === 'bulk' && (
                                    <ScopeMenu
                                        onPick={(scope) => {
                                            setScopeMenu(null)
                                            void resolveAll(true, scope)
                                        }}
                                        allLabel
                                    />
                                )}
                            </div>
                        )}
                    </div>

                    {held === 0 ? (
                        <div className="os-empty os-approvals__empty">
                            No calls awaiting approval — agents are cleared to run.
                        </div>
                    ) : (
                        <div className="os-approval-list">
                            {openshell.pending.map((request) => {
                                const meta = kindOf(request.service)
                                return (
                                    <div className="os-approval" key={request.id}>
                                        <span className={`os-kind ${meta.cls}`}>{meta.kind}</span>
                                        <div className="os-approval__body">
                                            <div className="os-approval__label">{request.service}</div>
                                            <div className="os-approval__meta">
                                                <code>{request.service}</code> · {request.operation ?? 'call'}
                                            </div>
                                            <div className="os-approval__who">
                                                <span className="os-approval__who-item">
                                                    Requested by <b>{request.user}</b>
                                                </span>
                                            </div>
                                        </div>
                                        <div className="os-approval__actions">
                                            <div className="os-approve__split">
                                                <button
                                                    className="os-approve"
                                                    onClick={() => void resolve(request.id, true)}
                                                >
                                                    <Check size={12} /> Approve
                                                </button>
                                                <button
                                                    className="os-approve os-approve__more"
                                                    title="Approval scope"
                                                    onClick={() => setScopeMenu(scopeMenu === request.id ? null : request.id)}
                                                >
                                                    <ChevronDown size={12} />
                                                </button>
                                                {scopeMenu === request.id && (
                                                    <ScopeMenu
                                                        onPick={(scope) => {
                                                            setScopeMenu(null)
                                                            void resolve(request.id, true, scope)
                                                        }}
                                                    />
                                                )}
                                            </div>
                                            <button className="os-reject" onClick={() => void resolve(request.id, false)}>
                                                <X size={12} /> Deny
                                            </button>
                                        </div>
                                    </div>
                                )
                            })}
                        </div>
                    )}
                </section>

                <section className="os-card">
                    <div className="os-sec__head os-sec__head--row">
                        <div>
                            <h2 className="os-sec__title">GOVERNED SERVICES</h2>
                            <p className="os-sec__desc">
                                Every outbound capability an agent can reach. The approval mode decides whether a call runs
                                freely, once per user, or is held every single time. An agent only asks for the next service
                                once the last one is allowed, so the queue never holds more than one — grant them ahead of
                                time and a run finishes without stopping.
                            </p>
                        </div>
                        <div className="os-approve__split os-approve__split--bulk">
                            <button className="os-grant" disabled={preBusy} onClick={() => void preapprove('session')}>
                                <ShieldCheck size={12} />
                                {preBusy ? 'Granting…' : `Grant all ${openshell.services.length} to warehouse-planner`}
                            </button>
                            <button
                                className="os-grant os-grant__more"
                                title="How long the grant lasts"
                                disabled={preBusy}
                                onClick={() => setScopeMenu(scopeMenu === 'pre' ? null : 'pre')}
                            >
                                <ChevronDown size={12} />
                            </button>
                            {scopeMenu === 'pre' && (
                                <div className="os-scope">
                                    <button
                                        onClick={() => {
                                            setScopeMenu(null)
                                            void preapprove('session')
                                        }}
                                    >
                                        <b>Grant for this dataset</b>
                                        <span>Dropped when a new warehouse is generated</span>
                                    </button>
                                    <button
                                        onClick={() => {
                                            setScopeMenu(null)
                                            void preapprove('always')
                                        }}
                                    >
                                        <b>Grant until revoked</b>
                                        <span>Survives a new warehouse</span>
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                    <div className="os-svc-grid">
                        {openshell.services.map((service) => {
                            const name = serviceName(service)
                            const approval = service.approval ?? 'auto'
                            const meta = kindOf(name)
                            const calls = openshell.grants.find((grant) => grant.service === name)?.calls ?? 0
                            return (
                                <div className="os-svc" key={name}>
                                    <div className="os-svc__top">
                                        <span className="os-svc__name">{name}</span>
                                        <span className={`os-kind ${meta.cls}`}>{meta.kind}</span>
                                        <span className={`os-svc__calls${calls > 0 ? ' hot' : ''}`}>{calls}</span>
                                    </div>
                                    <p className="os-svc__desc">{service.description}</p>
                                    <div className="os-svc__chips">
                                        <span className={`os-mode ${modeClass(approval)}`}>{approval}</span>
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </section>

                <section className="os-card">
                    <div className="os-sec__head os-sec__head--row">
                        <div>
                            <h2 className="os-sec__title">ACTIVE GRANTS</h2>
                            <p className="os-sec__desc">
                                Services a user has already cleared. Revoking one puts the next call back in the approval queue.
                            </p>
                        </div>
                        {openshell.grants.length > 0 && (
                            <button className="os-revoke os-revoke--all" onClick={() => void revokeAll()} disabled={revokingAll}>
                                {revokingAll ? 'Revoking…' : `Revoke all (${openshell.grants.length})`}
                            </button>
                        )}
                    </div>
                    {openshell.grants.length === 0 ? (
                        <div className="os-empty">No grants have been issued.</div>
                    ) : (
                        <table className="os-table">
                            <thead>
                                <tr>
                                    <th>Service</th>
                                    <th>User</th>
                                    <th>Status</th>
                                    <th className="os-cell--right">Calls</th>
                                    <th className="os-cell--right" />
                                </tr>
                            </thead>
                            <tbody>
                                {openshell.grants.map((grant) => (
                                    <tr key={`${grant.user}|${grant.service}`}>
                                        <td className="os-mono">{grant.service}</td>
                                        <td className="os-dim">{grant.user}</td>
                                        <td>
                                            <span className={`os-badge os-dec--${grant.status ?? 'allowed'}`}>
                                                {grant.status ?? 'allowed'}
                                            </span>
                                        </td>
                                        <td className="os-cell--right">{grant.calls ?? 0}</td>
                                        <td className="os-cell--right">
                                            <button
                                                className="os-revoke"
                                                onClick={() => void revoke(grant.user, grant.service)}
                                            >
                                                Revoke
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </section>

                <section className="os-card">
                    <div className="os-sec__head">
                        <h2 className="os-sec__title">MODEL EGRESS</h2>
                        <p className="os-sec__desc">
                            What the agents actually sent through the self-hosted NIM, counted by the governor.
                        </p>
                    </div>
                    <table className="os-table">
                        <thead>
                            <tr>
                                <th>Role</th>
                                <th>Model</th>
                                <th>Endpoint</th>
                                <th className="os-cell--right">Calls</th>
                                <th className="os-cell--right">Prompt</th>
                                <th className="os-cell--right">Completion</th>
                            </tr>
                        </thead>
                        <tbody>
                            {telemetry.models.map((model) => (
                                <tr key={model.role}>
                                    <td>{model.role}</td>
                                    <td className="os-mono">{model.model}</td>
                                    <td className="os-dim">{model.endpoint}</td>
                                    <td className="os-cell--right">{count(model.calls)}</td>
                                    <td className="os-cell--right">{count(model.prompt_tokens)}</td>
                                    <td className="os-cell--right">{count(model.completion_tokens)}</td>
                                </tr>
                            ))}
                            <tr>
                                <td colSpan={3} className="os-dim">
                                    Totals · {telemetry.commits} approved WMS commits
                                </td>
                                <td className="os-cell--right">{count(telemetry.totals?.calls)}</td>
                                <td className="os-cell--right">{count(telemetry.totals?.prompt_tokens)}</td>
                                <td className="os-cell--right">{count(telemetry.totals?.completion_tokens)}</td>
                            </tr>
                        </tbody>
                    </table>
                </section>

                <section className="os-card">
                    <div className="os-sec__head">
                        <h2 className="os-sec__title">AUDIT LOG</h2>
                        <p className="os-sec__desc">Every governed call, in the order the governor saw it.</p>
                    </div>
                    {openshell.audit.length === 0 ? (
                        <div className="os-empty">Nothing has been called yet.</div>
                    ) : (
                        <table className="os-table">
                            <thead>
                                <tr>
                                    <th>Time</th>
                                    <th>User</th>
                                    <th>Service</th>
                                    <th>Decision</th>
                                    <th>Source</th>
                                </tr>
                            </thead>
                            <tbody>
                                {openshell.audit
                                    .slice()
                                    .reverse()
                                    .map((entry, index) => (
                                        <tr key={index}>
                                            <td className="os-dim">{(entry.at ?? entry.timestamp ?? '').slice(11, 19)}</td>
                                            <td className="os-dim">{entry.user}</td>
                                            <td className="os-mono">{entry.service}</td>
                                            <td>
                                                <span className={`os-badge os-dec--${entry.decision}`}>{entry.decision}</span>
                                            </td>
                                            <td className="os-dim">{entry.source ?? '—'}</td>
                                        </tr>
                                    ))}
                            </tbody>
                        </table>
                    )}
                </section>
            </main>
        </div>
    )
}
