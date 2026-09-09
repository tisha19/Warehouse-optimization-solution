import { useEffect } from 'react'
import { Check, ShieldCheck, X } from 'lucide-react'
import { api } from '../api/client'
import { useLive } from '../state/LiveState'
import './OpenShellConsole.css'

export default function OpenShellConsole() {
    const { openshell, refreshOpenShell } = useLive()

    useEffect(() => {
        void refreshOpenShell()
    }, [refreshOpenShell])

    if (!openshell) {
        return <div className="app-body layout-full os-loading">Reading the governor…</div>
    }

    const resolve = async (requestId: string, approve: boolean) => {
        await api.resolve(requestId, approve)
        await refreshOpenShell()
    }

    const revoke = async (user: string, service: string) => {
        await api.revoke(user, service)
        await refreshOpenShell()
    }

    const { telemetry } = openshell

    return (
        <div className="app-body layout-full os">
            <header className="os__head">
                <div>
                    <h1>
                        <ShieldCheck size={18} /> OpenShell
                    </h1>
                    <p>
                        governor online · {openshell.services.length} services · {openshell.pending.length} pending
                    </p>
                </div>
                <code className="os__endpoint">{openshell.endpoint}</code>
            </header>

            <div className="os__metrics">
                <div className="os__metric">
                    <span>model calls</span>
                    <b>{telemetry.totals.calls}</b>
                    <small>this process</small>
                </div>
                <div className="os__metric">
                    <span>prompt tokens</span>
                    <b>{telemetry.totals.prompt_tokens.toLocaleString()}</b>
                    <small>sent to the NIM</small>
                </div>
                <div className="os__metric">
                    <span>completion tokens</span>
                    <b>{telemetry.totals.completion_tokens.toLocaleString()}</b>
                    <small>returned</small>
                </div>
                <div className="os__metric">
                    <span>WMS commits</span>
                    <b>{telemetry.commits}</b>
                    <small>approved writes</small>
                </div>
                {telemetry.models.map((model) => (
                    <div className="os__metric os__metric--wide" key={model.role}>
                        <span>{model.role}</span>
                        <b>{model.model}</b>
                        <small>
                            {model.endpoint} · {model.calls} calls
                        </small>
                    </div>
                ))}
            </div>

            <div className="os__grid">
                <section className="os__panel">
                    <div className="os__panelhead">
                        <span>Pending approvals</span>
                    </div>
                    {openshell.pending.length === 0 ? (
                        <p className="os__empty">No calls awaiting approval — agents are cleared to run.</p>
                    ) : (
                        openshell.pending.map((request) => (
                            <div className="os__row" key={request.id}>
                                <code>{request.service}</code>
                                <span className="os__dim">
                                    {request.user} · {request.operation ?? 'call'}
                                </span>
                                <span className="os__actions">
                                    <button className="os__btn os__btn--ok" onClick={() => void resolve(request.id, true)}>
                                        <Check size={12} /> Approve
                                    </button>
                                    <button className="os__btn os__btn--bad" onClick={() => void resolve(request.id, false)}>
                                        <X size={12} /> Deny
                                    </button>
                                </span>
                            </div>
                        ))
                    )}
                </section>

                <section className="os__panel">
                    <div className="os__panelhead">
                        <span>Grants</span>
                    </div>
                    {openshell.grants.length === 0 ? (
                        <p className="os__empty">No grants have been issued.</p>
                    ) : (
                        openshell.grants.map((grant) => (
                            <div className="os__row" key={`${grant.user}|${grant.service}`}>
                                <code>{grant.service}</code>
                                <span className="os__dim">
                                    {grant.user} · {grant.status ?? 'approved'}
                                    {grant.calls ? ` · ${grant.calls} calls` : ''}
                                </span>
                                <span className="os__actions">
                                    <button className="os__btn os__btn--bad" onClick={() => void revoke(grant.user, grant.service)}>
                                        Revoke
                                    </button>
                                </span>
                            </div>
                        ))
                    )}
                </section>
            </div>

            <section className="os__panel">
                <div className="os__panelhead">
                    <span>Governed services</span>
                </div>
                <div className="os__services">
                    {openshell.services.map((service) => {
                        const name = service.name ?? service.service ?? ''
                        const approval = service.approval ?? 'auto'
                        return (
                            <div className="os__row" key={name}>
                                <code>{name}</code>
                                <span className={`os__pill os__pill--${approval.includes('call') ? 'strict' : approval.includes('user') ? 'once' : 'auto'}`}>
                                    {approval}
                                </span>
                                <span className="os__dim">{service.description}</span>
                            </div>
                        )
                    })}
                </div>
            </section>

            <section className="os__panel">
                <div className="os__panelhead">
                    <span>Audit log</span>
                </div>
                <div className="os__audit">
                    {openshell.audit
                        .slice()
                        .reverse()
                        .map((entry, index) => (
                            <div className={`os__auditline os__auditline--${entry.decision}`} key={index}>
                                <time>{(entry.at ?? entry.timestamp ?? '').slice(11, 19)}</time>
                                <span>
                                    {entry.user} · <code>{entry.service}</code> · {entry.decision}
                                    {entry.source ? ` (${entry.source})` : ''}
                                </span>
                            </div>
                        ))}
                </div>
            </section>
        </div>
    )
}
