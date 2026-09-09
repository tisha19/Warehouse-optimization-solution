import { useMemo, useState } from 'react'
import type { Move, Slot } from '../api/client'
import './WarehouseMap.css'

/* ---------------------------------------------------------------------------
   Top-down schematic of the pick module. Zone A sits against the dispatch dock
   (the golden zone); each zone holds four aisles of seven bays, each bay three
   levels, which is exactly how the WMS layout is shaped. Proposed relocations
   are drawn as arcs from source slot to target slot.
   --------------------------------------------------------------------------- */

const BAY_W = 22
const BAY_GAP = 2
const LEVEL_H = 8
const LEVEL_GAP = 1
const AISLE_GAP = 14
const ZONE_GAP = 26
const ORIGIN_X = 104
const ORIGIN_Y = 34
const DOCK_X = 26
const DOCK_W = 40

type Geometry = { aisles: number; bays: number; levels: number }

const bayX = (aisle: number, bay: number, geo: Geometry) =>
    ORIGIN_X + (aisle - 1) * (geo.bays * (BAY_W + BAY_GAP) + AISLE_GAP) + (bay - 1) * (BAY_W + BAY_GAP)

const zoneY = (zoneIndex: number, geo: Geometry) =>
    ORIGIN_Y + zoneIndex * (geo.levels * (LEVEL_H + LEVEL_GAP) + ZONE_GAP)

type Props = {
    slots: Slot[]
    moves: Move[]
    selectedMoveId: string | null
    onSelectMove: (id: string | null) => void
    hasPlan: boolean
}

export default function WarehouseMap({ slots, moves, selectedMoveId, onSelectMove, hasPlan }: Props) {
    const [showHeat, setShowHeat] = useState(true)

    const geo: Geometry = useMemo(
        () => ({
            aisles: Math.max(...slots.map((s) => s.aisle), 1),
            bays: Math.max(...slots.map((s) => s.bay), 1),
            levels: Math.max(...slots.map((s) => s.level), 1),
        }),
        [slots],
    )

    const zones = useMemo(() => Array.from(new Set(slots.map((s) => s.zone))).sort(), [slots])
    const byId = useMemo(() => new Map(slots.map((s) => [s.slot_id, s])), [slots])

    const centre = useMemo(() => {
        const zoneIndex = new Map(zones.map((z, i) => [z, i]))
        return (slotId: string) => {
            const slot = byId.get(slotId)
            if (!slot) return null
            const index = zoneIndex.get(slot.zone) ?? 0
            return {
                cx: bayX(slot.aisle, slot.bay, geo) + BAY_W / 2,
                cy: zoneY(index, geo) + (slot.level - 1) * (LEVEL_H + LEVEL_GAP) + LEVEL_H / 2,
            }
        }
    }, [byId, geo, zones])

    const sourceSlots = useMemo(() => new Set(moves.map((m) => m.from_slot)), [moves])
    const targetSlots = useMemo(() => new Set(moves.map((m) => m.to_slot)), [moves])

    const width = ORIGIN_X + geo.aisles * (geo.bays * (BAY_W + BAY_GAP) + AISLE_GAP) + 30
    const height = zoneY(zones.length, geo) + 10
    const zoneBandH = geo.levels * (LEVEL_H + LEVEL_GAP)
    const rowW = geo.aisles * (geo.bays * (BAY_W + BAY_GAP) + AISLE_GAP) - AISLE_GAP

    const occupied = slots.filter((s) => s.occupant).length
    const blocked = slots.filter((s) => s.status !== 'ACTIVE').length

    return (
        <div className="wmap">
            <div className="wmap__head">
                <div>
                    <h2 className="wmap__title">Pick module · {hasPlan ? 'proposed layout' : 'current slotting'}</h2>
                    <p className="wmap__sub">
                        {slots.length} slots · {zones.length} zones × {geo.aisles} aisles × {geo.bays} bays × {geo.levels} levels
                        {hasPlan ? ' · proposed moves drawn as arcs' : ' · no plan yet'}
                    </p>
                </div>
                <div className="wmap__tools">
                    <button className={`wmap__toggle${showHeat ? ' is-on' : ''}`} onClick={() => setShowHeat((v) => !v)}>
                        Velocity heat
                    </button>
                    <span className="wmap__legend">
                        <span className="wmap__key wmap__key--a" /> A-class
                        <span className="wmap__key wmap__key--b" /> B-class
                        <span className="wmap__key wmap__key--c" /> C-class
                        {hasPlan && (
                            <>
                                <span className="wmap__key wmap__key--move" /> proposed move
                            </>
                        )}
                    </span>
                </div>
            </div>

            <div className="wmap__canvas">
                <svg viewBox={`0 0 ${width} ${height}`} className="wmap__svg" role="img">
                    <defs>
                        <marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
                            <path d="M0 0 L6 3 L0 6 z" fill="#38bdf8" />
                        </marker>
                        <marker id="arrowSel" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
                            <path d="M0 0 L6 3 L0 6 z" fill="#fbbf24" />
                        </marker>
                        <linearGradient id="dockGrad" x1="0" y1="0" x2="1" y2="0">
                            <stop offset="0%" stopColor="#1d4ed8" stopOpacity="0.55" />
                            <stop offset="100%" stopColor="#1d4ed8" stopOpacity="0.05" />
                        </linearGradient>
                    </defs>

                    <rect x={DOCK_X} y={ORIGIN_Y - 24} width={DOCK_W} height={height - ORIGIN_Y + 6} rx="6" fill="url(#dockGrad)" stroke="#2a4a86" />
                    <text
                        x={DOCK_X + DOCK_W}
                        y={height / 2}
                        className="wmap__docklabel"
                        transform={`rotate(-90 ${DOCK_X + DOCK_W / 2} ${height / 2})`}
                    >
                        DISPATCH DOCK
                    </text>

                    <rect
                        x={ORIGIN_X - 8}
                        y={ORIGIN_Y - 12}
                        width={rowW + 16}
                        height={zoneBandH + 20}
                        rx="8"
                        fill="rgba(251,191,36,0.07)"
                        stroke="rgba(251,191,36,0.35)"
                        strokeDasharray="4 3"
                    />
                    <text x={ORIGIN_X + rowW - 58} y={ORIGIN_Y - 18} className="wmap__zonelabel">
                        GOLDEN ZONE
                    </text>

                    {zones.map((zone, zoneIndex) => {
                        const y = zoneY(zoneIndex, geo)
                        const zoneSlots = slots.filter((s) => s.zone === zone)
                        const mean = zoneSlots.reduce((sum, s) => sum + s.distance_m, 0) / Math.max(zoneSlots.length, 1)
                        return (
                            <g key={zone}>
                                <text x={ORIGIN_X - 16} y={y + 10} className="wmap__aisle" textAnchor="end">
                                    {zone}
                                </text>
                                <text x={ORIGIN_X - 16} y={y + 22} className="wmap__aisledist" textAnchor="end">
                                    {Math.round(mean)}m
                                </text>
                                {zoneSlots.map((slot) => {
                                    const x = bayX(slot.aisle, slot.bay, geo)
                                    const sy = y + (slot.level - 1) * (LEVEL_H + LEVEL_GAP)
                                    const abc = slot.occupant?.abc_class?.toLowerCase()
                                    const cls = [
                                        'wmap__bin',
                                        slot.occupant && showHeat && abc ? `wmap__bin--${abc}` : '',
                                        slot.temperature_controlled ? 'wmap__bin--chill' : '',
                                        slot.status !== 'ACTIVE' ? 'is-gap' : '',
                                        sourceSlots.has(slot.slot_id) ? 'is-source' : '',
                                        targetSlots.has(slot.slot_id) ? 'is-target' : '',
                                    ]
                                        .filter(Boolean)
                                        .join(' ')
                                    return (
                                        <rect key={slot.slot_id} x={x} y={sy} width={BAY_W} height={LEVEL_H} rx="1.5" className={cls}>
                                            <title>
                                                {slot.slot_id} · aisle {slot.aisle} bay {slot.bay} level {slot.level} · {slot.distance_m}m
                                                {slot.occupant
                                                    ? `\n${slot.occupant.product_name} (${slot.occupant.sku_id}) · ${slot.occupant.picks_per_day} picks/day · class ${slot.occupant.abc_class}`
                                                    : '\nempty face'}
                                                {slot.status !== 'ACTIVE' ? `\n${slot.status}` : ''}
                                            </title>
                                        </rect>
                                    )
                                })}
                            </g>
                        )
                    })}

                    {moves.map((move) => {
                        const from = centre(move.from_slot)
                        const to = centre(move.to_slot)
                        if (!from || !to) return null
                        const selected = selectedMoveId === move.id
                        const midY = Math.min(from.cy, to.cy) - 22
                        const d = `M ${from.cx} ${from.cy} Q ${(from.cx + to.cx) / 2} ${midY} ${to.cx} ${to.cy}`
                        return (
                            <g key={move.id} onClick={() => onSelectMove(selected ? null : move.id)}>
                                <path d={d} className="wmap__movehit" />
                                <path d={d} className={`wmap__move${selected ? ' is-selected' : ''}`} markerEnd={selected ? 'url(#arrowSel)' : 'url(#arrow)'} />
                                <title>
                                    {move.id} · {move.sku} · {move.from_slot} → {move.to_slot} · {move.benefit_hours_per_day} hr/day
                                </title>
                            </g>
                        )
                    })}
                </svg>
            </div>

            <div className="wmap__foot">
                <span className="wmap__footitem">
                    <b>{occupied}</b> of {slots.length} faces occupied
                </span>
                <span className="wmap__footitem">
                    <b>{blocked}</b> blocked for maintenance
                </span>
                {hasPlan ? (
                    <>
                        <span className="wmap__footitem">
                            <b>{moves.length}</b> proposed moves
                        </span>
                        <span className="wmap__footitem wmap__footitem--hint">Click a move arc to open its explanation</span>
                    </>
                ) : (
                    <span className="wmap__footitem wmap__footitem--warn">No move plan yet — run the DeepAgent to generate one</span>
                )}
            </div>
        </div>
    )
}
