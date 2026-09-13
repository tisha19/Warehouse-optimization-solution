import { Link } from 'react-router-dom'
import { ArrowRight, BarChart3, Gauge, ShieldCheck } from 'lucide-react'
import { useLive } from '../state/LiveState'
import type { EvaluationCategory, EvaluationMetric, EvaluationScorecard } from '../api/client'
import './EvaluationScreen.css'

function formatMetric(metric: EvaluationMetric): string {
    if (metric.value === null || metric.value === undefined) return '—'
    if (typeof metric.value === 'string') return metric.value
    if (metric.unit === '%') return `${metric.value.toFixed(1)}%`
    if (metric.unit === 'ms' || metric.unit === 'tokens' || metric.unit === 'runs') {
        return `${Math.round(metric.value)} ${metric.unit}`
    }
    if (Number.isInteger(metric.value)) return `${metric.value} ${metric.unit}`.trim()
    return `${metric.value.toFixed(1)} ${metric.unit}`.trim()
}

function scoreClass(score: number): string {
    if (score >= 85) return 'is-good'
    if (score >= 60) return 'is-warn'
    return 'is-bad'
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
                    <Gauge size={18} />
                    <strong>{scorecard ? scorecard.overall_score.toFixed(1) : '—'}</strong>
                    <span>Overall Agent Score</span>
                </div>
            </header>

            {scorecard ? (
                <>
                    <section className="ev__summary">
                        <article className="ev-summary">
                            <div>
                                <span className="ev-summary__label">Run status</span>
                                <strong>{scorecard.status}</strong>
                            </div>
                            <div>
                                <span className="ev-summary__label">Strongest category</span>
                                <strong>{scorecard.categories.find((item) => item.key === scorecard.strongest_category)?.label ?? '—'}</strong>
                            </div>
                            <div>
                                <span className="ev-summary__label">Watchlist</span>
                                <strong>{scorecard.categories.find((item) => item.key === scorecard.weakest_category)?.label ?? '—'}</strong>
                            </div>
                            <div className="ev-summary__copy">{scorecard.summary}</div>
                        </article>
                        <article className="ev-weights">
                            {scorecard.weights.map((weight) => {
                                const category = scorecard.categories.find((item) => item.key === weight.key)
                                return (
                                    <div className="ev-weight" key={weight.key}>
                                        <span>{weight.label}</span>
                                        <strong>{weight.weight}%</strong>
                                        <b className={scoreClass(category?.score ?? 0)}>{(category?.score ?? 0).toFixed(1)}</b>
                                    </div>
                                )
                            })}
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
