/* Every shape here is what the Python service actually returns. */

export type Kpis = {
    daily_travel_km: number
    avg_distance_per_pick_m: number
    daily_picks: number
    forward_pick_coverage_pct: number
    slot_utilisation_pct: number
    blocked_slots: number
    lines_below_reorder: number
}

export type Metric = { value: number; unit: string }

export type Severity = 'severe' | 'warning' | 'notable' | 'info'

export type Problem = {
    id: string
    severity: Severity
    addressable: boolean
    title: string
    detail: string
    metric: Metric | null
}

export type Zone = { id: string; label: string; slots: number; utilisation: number; distance: number }

export type Service = { name: string; endpoint: string; detail: string }

export type ApprovalScope = 'call' | 'session' | 'always'

export type Commit = {
    at: string
    approval_id: string
    moves: number
    relocated: number
    rejected: number
    applied: Move[]
    kpis_before: Kpis
    kpis_after: Kpis
}

export type Dashboard = {
    site: string
    kpis: Kpis
    baseline: Kpis | null
    problems: Problem[]
    analysis: {
        status: 'pending' | 'held' | 'ready' | 'failed'
        error: string | null
        held_reason: string | null
        model: string
    }
    zones: Zone[]
    counts: Record<string, number>
    services: Service[]
    commits: Commit[]
    last_commit: Commit | null
    relocated_total: number
    dataset_seed: number
    run: { id: string | null; status: RunStatus }
    generated_at: string
}

export type Occupant = {
    sku_id: string
    product_name: string
    abc_class: string
    quantity: number
    picks_per_day: number
}

export type Slot = {
    slot_id: string
    zone: string
    zone_id: number
    aisle: number
    bay: number
    level: number
    distance_m: number
    status: string
    temperature_controlled: boolean
    occupant: Occupant | null
}

export type Layout = {
    site: string
    slots: Slot[]
    aisles_per_zone: number
    bays_per_aisle: number
    levels_per_bay: number
    forward_pick_zone: string
    moves: Move[]
}

export type Mover = {
    sku_id: string
    product_name: string
    abc_class: string
    slot: string
    distance_m: number
    picks_per_day: number
    uplift_pct: number
    promotion_uplift_pct: number
    temperature_controlled: boolean
}

export type Demand = {
    headline: (Mover & { series: { day: number; qty: number; promotion: boolean }[] }) | null
    promoted_count: number
    top_movers: Mover[]
    horizon_days: number
}

export type Alternative = { slot_id: string; distance_m: number; gain_metre_picks: number }

export type Move = {
    id: string
    priority: number
    sku: string
    code: string
    abc_class: string
    from_slot: string
    to_slot: string
    day: number
    window: string
    reason: string
    benefit_hours_per_day: number
    labor_minutes: number
    alternatives: Alternative[]
}

export type RunStatus = 'IDLE' | 'RUNNING' | 'HALTED' | 'COMPLETE' | 'FAILED'

export type Harness = {
    attached: boolean
    middleware: string[]
    prompt_suffix_chars: number
}

export type Orchestrator = {
    model: string
    status: 'pending' | 'running' | 'done' | 'failed' | 'waiting-for-approval'
    harness: Harness
    thinking: string[]
    narrative: string
    duration_ms?: number
}

export type Delegation = {
    id: string
    name: string
    question: string
    answer: string
    status: 'running' | 'done' | 'failed'
}

export type Headroom = {
    current_metre_picks: number
    best_metre_picks: number
    headroom_metre_picks: number
    headroom_pct: number
}

export type SolveRound = {
    round: number
    status: 'running' | 'done' | 'failed'
    max_moves: number
    time_limit_s: number
    objective: string
    solver_seconds?: number
    error?: string
    moves?: number
    kpis_before?: Kpis
    kpis_after?: Kpis
    headroom_before?: Headroom
    headroom_after?: Headroom
}

export type SolveSummary = {
    headline: string
    explanation: string
    metrics: Record<string, number>
    move_count: number
}

export type Run = {
    id: string | null
    status: RunStatus
    orchestrator: Orchestrator
    delegations: Delegation[]
    rounds: SolveRound[]
    chosen_round: number | null
    solved_constraints: Constraints | null
    summary: SolveSummary | null
    guardrails: { stage: string; allowed: boolean; policy_id: string; reason: string; at: string }[]
    openshell: { service: string; actor: string; allowed: boolean; reason: string; at: string }[]
    moves: Move[]
    approval: { approval_id?: string; status?: string } | null
    validation: Record<string, unknown> | null
    error: string | null
    halted_on: { service: string; reason: string } | null
    started_at: string | null
    finished_at: string | null
    decisions: Record<string, 'pending' | 'approved' | 'rejected'>
    constraints: Constraints
    goal: string
}

export type Constraints = {
    max_moves: number
    locked_skus: string[]
    cold_chain_locked: boolean
    labour_minutes_per_window: number
    execution_windows: string[]
}

export type CommitState = {
    status: 'IDLE' | 'AWAITING_APPROVAL' | 'COMMITTED' | 'DENIED' | 'FAILED'
    service: string
    error: string | null
    commit: Commit | null
}

export type GovernorService = { name?: string; service?: string; approval?: string; description?: string }
export type Grant = { user: string; service: string; status?: string; calls?: number }
export type PendingRequest = { id: string; user: string; service: string; operation?: string }
export type AuditEntry = { at?: string; timestamp?: string; user: string; service: string; decision: string; source?: string }

export type OpenShell = {
    endpoint: string
    services: GovernorService[]
    grants: Grant[]
    pending: PendingRequest[]
    audit: AuditEntry[]
    telemetry: {
        models: {
            role: string
            model: string
            endpoint: string
            calls: number
            prompt_tokens: number | null
            completion_tokens: number | null
            harness_middleware?: number
        }[]
        totals: { calls: number; prompt_tokens: number; completion_tokens: number }
        solve_rounds?: number
        commits: number
    }
}

/** The service reports failures with a non-2xx status and {error}; nothing is substituted. */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(path, init)
    const body = await response.json().catch(() => null)
    if (!response.ok) {
        const detail = (body as { error?: string } | null)?.error
        throw new Error(detail || `${response.status} ${response.statusText}`)
    }
    return body as T
}

const post = <T,>(path: string, payload: unknown = {}): Promise<T> =>
    call<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })

export const api = {
    dashboard: () => call<Dashboard>('/api/dashboard'),
    layout: () => call<Layout>('/api/layout'),
    demand: () => call<Demand>('/api/demand'),
    run: () => call<Run>('/api/run'),
    startRun: () => post<Run>('/api/run/start'),
    setConstraints: (patch: Partial<Constraints>) => post<Constraints>('/api/constraints', patch),
    resetConstraints: () => post<Constraints>('/api/constraints/reset'),
    decide: (moveId: string, decision: 'approved' | 'rejected' | 'pending') =>
        post<Run>('/api/plan/decide', { move_id: moveId, decision }),
    commit: () => post<CommitState>('/api/plan/commit'),
    commitStatus: () => call<CommitState>('/api/plan/commit'),
    reset: () => post<Dashboard>('/api/reset'),
    openshell: () => call<OpenShell>('/api/openshell'),
    resolve: (requestId: string, approve: boolean, scope: ApprovalScope = 'call') =>
        post<OpenShell>('/api/openshell/resolve', { request_id: requestId, approve, scope }),
    resolveAll: (approve: boolean, scope: ApprovalScope = 'call') =>
        post<OpenShell>('/api/openshell/resolve-all', { approve, scope }),
    revoke: (user: string, service: string) => post<OpenShell>('/api/openshell/revoke', { user, service }),
    revokeAll: () => post<OpenShell>('/api/openshell/revoke-all'),
    preapprove: (scope: ApprovalScope = 'session') => post<OpenShell>('/api/openshell/preapprove', { scope }),
}
