import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Check, ShieldAlert, X } from 'lucide-react'
import { api, type GovernorService } from '../api/client'
import { useLive } from '../state/LiveState'
import './OpenShellConsole.css'

/* The governance console is deliberately not part of the planner application.
   It is a separate admin surface, on its own palette, over the whole viewport. */

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

export default function OpenShellConsole() {
    const { openshell, refreshOpenShell } = useLive()

    useEffect(() => {
        void refreshOpenShell()
    }, [refreshOpenShell])

    const resolve = async (requestId: string, approve: boolean) => {
        await api.resolve(requestId, approve)
        await refreshOpenShell()
    }

    const revoke = async (user: string, service: string) => {
        await api.revoke(user, service)
        await refreshOpenShell()
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
                    <div className="os-sec__head">
                        <h2 className="os-sec__title">
                            <ShieldAlert size={15} className="os-approvals__ico" /> PENDING APPROVALS
                        </h2>
                        <p className="os-sec__desc">
                            Agent egress calls held by the governor. Approving flips the service on and releases the held call —
                            the WarehouseIQ run resumes from where it paused.
                        </p>
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
                                            <button className="os-approve" onClick={() => void resolve(request.id, true)}>
                                                <Check size={12} /> Approve
                                            </button>
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
                    <div className="os-sec__head">
                        <h2 className="os-sec__title">GOVERNED SERVICES</h2>
                        <p className="os-sec__desc">
                            Every outbound capability an agent can reach. The approval mode decides whether a call runs freely,
                            once per user, or is held every single time.
                        </p>
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
                                    <td className="os-cell--right">{model.calls}</td>
                                    <td className="os-cell--right">{model.prompt_tokens.toLocaleString()}</td>
                                    <td className="os-cell--right">{model.completion_tokens.toLocaleString()}</td>
                                </tr>
                            ))}
                            <tr>
                                <td colSpan={3} className="os-dim">
                                    Totals · {telemetry.commits} approved WMS commits
                                </td>
                                <td className="os-cell--right">{telemetry.totals.calls}</td>
                                <td className="os-cell--right">{telemetry.totals.prompt_tokens.toLocaleString()}</td>
                                <td className="os-cell--right">{telemetry.totals.completion_tokens.toLocaleString()}</td>
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
