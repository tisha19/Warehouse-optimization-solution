import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type Constraints, type Dashboard, type Demand, type Layout, type OpenShell, type Run } from '../api/client'

type Live = {
    dashboard: Dashboard | null
    layout: Layout | null
    demand: Demand | null
    run: Run | null
    openshell: OpenShell | null
    error: string | null
    busy: boolean
    refreshWarehouse: () => Promise<void>
    refreshRun: () => Promise<void>
    refreshOpenShell: () => Promise<void>
    startRun: () => Promise<void>
    setConstraints: (patch: Partial<Constraints>) => Promise<void>
    resetConstraints: () => Promise<void>
    decide: (moveId: string, decision: 'approved' | 'rejected' | 'pending') => Promise<void>
    reset: () => Promise<void>
    clearError: () => void
}

const LiveContext = createContext<Live | null>(null)

const ACTIVE: Run['status'][] = ['RUNNING', 'HALTED']

export function LiveProvider({ children }: { children: ReactNode }) {
    const [dashboard, setDashboard] = useState<Dashboard | null>(null)
    const [layout, setLayout] = useState<Layout | null>(null)
    const [demand, setDemand] = useState<Demand | null>(null)
    const [run, setRun] = useState<Run | null>(null)
    const [openshell, setOpenShell] = useState<OpenShell | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [busy, setBusy] = useState(false)
    const timer = useRef<number | null>(null)

    const guard = useCallback(async (work: () => Promise<void>) => {
        try {
            await work()
        } catch (caught) {
            setError(caught instanceof Error ? caught.message : String(caught))
        }
    }, [])

    // Each panel is filled as soon as its own call returns; the slot layout is the
    // slowest of the three and must not hold up the demand headline.
    const refreshWarehouse = useCallback(async () => {
        await guard(async () => {
            await Promise.all([
                api.dashboard().then(setDashboard),
                api.demand().then(setDemand),
                api.layout().then(setLayout),
            ])
        })
    }, [guard])

    const refreshRun = useCallback(async () => {
        await guard(async () => setRun(await api.run()))
    }, [guard])

    const refreshOpenShell = useCallback(async () => {
        await guard(async () => setOpenShell(await api.openshell()))
    }, [guard])

    useEffect(() => {
        void refreshWarehouse()
        void refreshRun()
    }, [refreshWarehouse, refreshRun])

    // Poll only while a run is in flight; a halted run resumes on its own once
    // the governor is answered, so the UI has to keep watching.
    useEffect(() => {
        const active = run && ACTIVE.includes(run.status)
        if (!active) {
            if (timer.current) window.clearInterval(timer.current)
            timer.current = null
            return
        }
        timer.current = window.setInterval(() => {
            void refreshRun()
            void refreshOpenShell()
        }, 1500)
        return () => {
            if (timer.current) window.clearInterval(timer.current)
            timer.current = null
        }
    }, [run, refreshRun, refreshOpenShell])

    // The analysis is produced on a background thread and nothing pushes the
    // result to the client, so the dashboard is polled rather than waiting for
    // an action to refresh it. Without this the panel can sit on "re-assessing"
    // over stale findings until the page is reloaded.
    useEffect(() => {
        const pending = dashboard?.analysis.status === 'pending'
        const poll = window.setInterval(() => void refreshWarehouse(), pending ? 2500 : 8000)
        return () => window.clearInterval(poll)
    }, [dashboard?.analysis.status, refreshWarehouse])

    const startRun = useCallback(async () => {
        setBusy(true)
        await guard(async () => setRun(await api.startRun()))
        setBusy(false)
    }, [guard])

    const setConstraints = useCallback(
        async (patch: Partial<Constraints>) => {
            await guard(async () => {
                await api.setConstraints(patch)
                setRun(await api.run())
                // Constraints feed the headroom and the analysis, so the cockpit
                // is out of date the moment one changes.
                await refreshWarehouse()
            })
        },
        [guard, refreshWarehouse],
    )

    const resetConstraints = useCallback(async () => {
        await guard(async () => {
            await api.resetConstraints()
            setRun(await api.run())
            await refreshWarehouse()
        })
    }, [guard, refreshWarehouse])

    const decide = useCallback(async (moveId: string, decision: 'approved' | 'rejected' | 'pending') => {
        await guard(async () => setRun(await api.decide(moveId, decision)))
    },
        [guard],
    )

    const reset = useCallback(async () => {
        setBusy(true)
        await guard(async () => {
            await api.reset()
            await refreshWarehouse()
            setRun(await api.run())
        })
        setBusy(false)
    }, [guard, refreshWarehouse])

    return (
        <LiveContext.Provider
            value={{
                dashboard,
                layout,
                demand,
                run,
                openshell,
                error,
                busy,
                refreshWarehouse,
                refreshRun,
                refreshOpenShell,
                startRun,
                setConstraints,
                resetConstraints,
                decide,
                reset,
                clearError: () => setError(null),
            }}
        >
            {children}
        </LiveContext.Provider>
    )
}

export function useLive() {
    const value = useContext(LiveContext)
    if (!value) throw new Error('useLive must be used inside LiveProvider')
    return value
}
