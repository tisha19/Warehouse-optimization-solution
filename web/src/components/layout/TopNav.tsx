import { NavLink, useNavigate } from 'react-router-dom'
import { Activity, ShieldCheck, RotateCcw } from 'lucide-react'
import { useLive } from '../../state/LiveState'
import './TopNav.css'

const NAV = [
    { label: 'Cockpit', to: '/cockpit' },
    { label: 'Move Plan', to: '/plan' },
    { label: 'DeepAgent Run', to: '/workflow' },
]

export default function TopNav() {
    const navigate = useNavigate()
    const { dashboard, demand, openshell, reset, busy } = useLive()
    const pending = openshell?.pending?.length ?? 0
    const headline = demand?.headline

    const handleReset = async () => {
        await reset()
        navigate('/cockpit')
    }

    return (
        <header className="topnav">
            <div className="topnav__brand">
                <div className="topnav__logo">
                    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden>
                        <path d="M3 8.5 12 3.5l9 5v7l-9 5-9-5z" fill="none" stroke="#38bdf8" strokeWidth="1.4" strokeLinejoin="round" />
                        <path d="M3 8.5 12 13.5l9-5M12 13.5V21" fill="none" stroke="#3b82f6" strokeWidth="1.1" />
                        <path d="M7 11v4.5" stroke="#34d399" strokeWidth="1.3" strokeLinecap="round" />
                    </svg>
                </div>
                <div className="topnav__brand-text">
                    <span className="topnav__brand-title">WAREHOUSEIQ</span>
                    <span className="topnav__brand-sub">WAREHOUSE DECISION COPILOT</span>
                </div>
            </div>

            <div className="topnav__meta">
                <div className="topnav__weather">
                    <Activity size={17} className="topnav__weather-icon" />
                    <div className="topnav__meta-text">
                        <span className="topnav__meta-strong">
                            {headline ? `${headline.product_name} +${headline.promotion_uplift_pct}%` : 'Reading the forecast…'}
                        </span>
                        <span className="topnav__meta-dim">
                            {headline ? `${demand?.horizon_days}-day forecast · ${headline.picks_per_day} picks/day` : ''}
                        </span>
                    </div>
                </div>
                <div className="topnav__clock">
                    <span className="topnav__meta-strong">{dashboard?.site ?? ''}</span>
                    <span className="topnav__meta-dim">dataset {dashboard?.dataset_seed ?? '—'}</span>
                </div>
            </div>

            <nav className="topnav__nav">
                {NAV.map((item) => (
                    <NavLink key={item.to} to={item.to} className={({ isActive }) => `topnav__link${isActive ? ' topnav__link--active' : ''}`}>
                        {item.label}
                    </NavLink>
                ))}
            </nav>

            <div className="topnav__actions">
                <NavLink
                    className={({ isActive }) => `topnav__gov${pending > 0 ? ' topnav__gov--pending' : ''}${isActive ? ' topnav__link--active' : ''}`}
                    to="/openshell"
                    title="OpenShell governance console"
                >
                    <ShieldCheck size={14} /> OpenShell
                    {pending > 0 && <span className="topnav__navbadge">{pending}</span>}
                </NavLink>
                <button className="topnav__user" onClick={handleReset} disabled={busy} title="Generate a different warehouse and clear this session">
                    <span className="topnav__user-name">New dataset</span>
                    <RotateCcw size={14} className="topnav__user-caret" />
                </button>
            </div>
        </header>
    )
}
