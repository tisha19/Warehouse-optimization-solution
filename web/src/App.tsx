import { Navigate, Route, Routes } from 'react-router-dom'
import TopNav from './components/layout/TopNav'
import { useLive } from './state/LiveState'
import CockpitScreen from './pages/CockpitScreen'
import PlanScreen from './pages/PlanScreen'
import WorkflowScreen from './pages/WorkflowScreen'
import OpenShellConsole from './pages/OpenShellConsole'

export default function App() {
    const { error, clearError } = useLive()
    return (
        <div className="app-shell">
            <TopNav />
            {error && (
                <div className="app-error" role="alert">
                    <span>{error}</span>
                    <button onClick={clearError}>dismiss</button>
                </div>
            )}
            <Routes>
                <Route path="/" element={<Navigate to="/cockpit" replace />} />
                <Route path="/cockpit" element={<CockpitScreen />} />
                <Route path="/plan" element={<PlanScreen />} />
                <Route path="/workflow" element={<WorkflowScreen />} />
                <Route path="/openshell" element={<OpenShellConsole />} />
                <Route path="*" element={<Navigate to="/cockpit" replace />} />
            </Routes>
        </div>
    )
}
