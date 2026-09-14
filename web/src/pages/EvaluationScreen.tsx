import { Link } from 'react-router-dom'
import { ArrowRight, BarChart3, Gauge, ShieldCheck } from 'lucide-react'
import { useLive } from '../state/LiveState'
import type { EvaluationCategory, EvaluationMetric, EvaluationScorecard } from '../api/client'
import './EvaluationScreen.css'

function formatMetric(metric: EvaluationMetric): string {
    if (metric.value === null || metric.value === undefined) return '—'
    if (typeof metric.value === 'string') return metric.value
    if (metric.unit === '%') return `${metric.value.toFixed(1)}%`
    if (metric.unit === 'tokens') {
        if (Math.abs(metric.value) >= 1000) return `${Math.round(metric.value / 1000)}K tokens`
        return `${Math.round(metric.value)} tokens`
    }
    if (metric.unit === 'ms' || metric.unit === 'runs') {
        return `${Math.round(metric.value)} ${metric.unit}`
    }
    if (Math.abs(metric.value) >= 10000) {
        return `${Math.round(metric.value).toLocaleString('en-GB')} ${metric.unit}`.trim()
    }
    if (Number.isInteger(metric.value)) return `${metric.value} ${metric.unit}`.trim()
    return `${metric.value.toFixed(1)} ${metric.unit}`.trim()
}

function scoreClass(score: number): string {
    if (score >= 85) return 'is-good'
    if (score >= 60) return 'is-warn'
    return 'is-bad'
}

function RingGauge({ value }: { value: number }) {
    const progress = Math.max(0, Math.min(100, value))
    return (
        <svg className={`ev-ring ${scoreClass(value)}`} viewBox="0 0 88 56" aria-hidden>
            <path className="ev-ring__track" d="M 14 42 A 30 30 0 0 1 74 42" pathLength={100} />
            <path
                className="ev-ring__value"
                d="M 14 42 A 30 30 0 0 1 74 42"
                pathLength={100}
                style={{ strokeDasharray: 100, strokeDashoffset: 100 - progress }}
            />
        </svg>
    )
}

function GaugePlot({ value }: { value: number }) {
    return (
        <div className={`ev-gaugePlot ${scoreClass(value)}`}>
            <RingGauge value={value} />
            <div className="ev-gaugePlot__center">
                <strong>{value.toFixed(1)}</strong>
                <span>Overall Agent Score</span>
            </div>
        </div>
    )
}

function CategoryCard({ category }: { category: EvaluationCategory }) {
    return (
        <article className="ev-card">
            <header className="ev-card__head">
                <div>
                    <span className="ev-card__label">{category.label}</span>
                    <span className="ev-card__weight">{category.weight}% weight</span>
                </div>
                <div className={`ev-card__score ${scoreClass(category.score)}`}>{category.score.toFixed(1)}</div>
            </header>
            <div className="ev-card__bar">
                <span style={{ width: `${category.score}%` }} />
            </div>
            <div className="ev-card__metrics">
                {category.metrics.map((metric) => (
                    <div className="ev-metric" key={`${category.key}-${metric.label}`}>
                        <div className="ev-metric__head">
                            <strong>{metric.label}</strong>
                            <span className={`ev-metric__score ${scoreClass(metric.score)}`}>{metric.score.toFixed(0)}</span>
                        </div>
                        <div className="ev-metric__value">
                            <span>{formatMetric(metric)}</span>
                            {metric.note && <p>{metric.note}</p>}
                        </div>
                    </div>
                ))}
            </div>
        </article>
    )
}

export default function EvaluationScreen() {
    const { run } = useLive()
    const scorecard: EvaluationScorecard | null = run?.evaluation ?? null

    return (
        <div className="app-body layout-full ev">
            <header className="ev__head">
                <div className="ev__title">
                    <span className="ev__kicker">Executive dashboard</span>
                    <h1>
                        <BarChart3 size={22} />
                        {run?.id ? `DeepAgent run ${run.id}` : 'No run available'}
                    </h1>
                    <p>{run?.goal ?? 'Start a DeepAgent run to generate the weighted scorecard.'}</p>
                </div>
                <div className={`ev__gauge ${scorecard ? scoreClass(scorecard.overall_score) : 'is-warn'}`}>
                    {scorecard ? <GaugePlot value={scorecard.overall_score} /> : <Gauge size={18} />}
                    {!scorecard && (
                        <div>
                            <strong>—</strong>
                            <span>Overall Agent Score</span>
                        </div>
                    )}
                </div>
            </header>

            {scorecard ? (
                <>
                    <section className="ev__summary">
                        <article className="ev-summary">
                            <div className="ev-summary__copy">{scorecard.summary}</div>
                            <div className="ev-summary__stats">
                                <div>
                                    <span className="ev-summary__label">Run status</span>
                                    <strong>{scorecard.status}</strong>
                                </div>
                                <div>
                                    <span className="ev-summary__label">Best</span>
                                    <strong>{scorecard.categories.find((item) => item.key === scorecard.strongest_category)?.label ?? '—'}</strong>
                                </div>
                                <div>
                                    <span className="ev-summary__label">Watchlist</span>
                                    <strong>{scorecard.categories.find((item) => item.key === scorecard.weakest_category)?.label ?? '—'}</strong>
                                </div>
                            </div>
                        </article>
                        <article className="ev-trend">
                            <div className="ev-trend__head">
                                <span>Category score spread</span>
                                <span>{scorecard.categories.length} categories</span>
                            </div>
                            <div className="ev-spread" role="list" aria-label="Category score spread">
                                {scorecard.categories.map((category) => (
                                    <div className="ev-spread__item" role="listitem" key={category.key}>
                                        <div className="ev-spread__bar">
                                            <span
                                                className={`ev-spread__fill ${scoreClass(category.score)}`}
                                                style={{ height: `${Math.max(16, category.score)}%` }}
                                            />
                                            <div className="ev-spread__content">
                                                <strong>{category.label}</strong>
                                                <span>{category.score.toFixed(1)}</span>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </article>
                    </section>

                    <section className="ev__categories">
                        {scorecard.categories.map((category) => (
                            <CategoryCard key={category.key} category={category} />
                        ))}
                    </section>
                </>
            ) : (
                <section className="ev-empty">
                    <ShieldCheck size={18} />
                    <div>
                        <strong>{run?.status === 'FAILED' ? 'The run failed before scoring.' : 'Waiting for a completed DeepAgent run.'}</strong>
                        <p>The executive dashboard is generated from the finished run payload.</p>
                    </div>
                    <Link className="ev-empty__link" to="/workflow">
                        Open DeepAgent run <ArrowRight size={13} />
                    </Link>
                </section>
            )}
        </div>
    )
}
