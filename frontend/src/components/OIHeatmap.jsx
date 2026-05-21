import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts'

function fmt(v) {
  if (v >= 1e7) return (v / 1e7).toFixed(1) + 'Cr'
  if (v >= 1e5) return (v / 1e5).toFixed(1) + 'L'
  return v.toLocaleString()
}

export default function OIHeatmap({ chainData, oiAnalysis, marketData }) {
  if (!chainData || !oiAnalysis || !marketData) return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 h-64 flex items-center justify-center">
      <p className="text-gray-600 text-sm">Waiting for options chain data...</p>
    </div>
  )

  const spot = marketData.nifty_spot
  const atm = Math.round(spot / 50) * 50
  const support = oiAnalysis.support
  const resistance = oiAnalysis.resistance

  // Get strikes around ATM
  const strikes = chainData
    .filter(r => Math.abs(r.strike - atm) <= 300)
    .sort((a, b) => a.strike - b.strike)

  const maxOI = Math.max(...strikes.map(r => Math.max(r.put_oi || 0, r.call_oi || 0)))

  const chartData = strikes.map(r => ({
    strike: r.strike,
    putOI: -(r.put_oi || 0),
    callOI: r.call_oi || 0,
    isAtm: r.strike === atm,
    isSupport: r.strike === support,
    isResistance: r.strike === resistance,
  }))

  const CustomTick = ({ x, y, payload }) => {
    const strike = payload.value
    const isSupport = strike === support
    const isResistance = strike === resistance
    const isAtm = strike === atm
    let label = strike
    let color = '#6b7280'
    if (isAtm) color = '#3b82f6'
    if (isSupport) color = '#22c55e'
    if (isResistance) color = '#ef4444'
    return (
      <g transform={`translate(${x},${y})`}>
        <text x={0} y={0} dy={4} textAnchor="end" fill={color} fontSize={10}>
          {label}
          {isSupport ? ' S' : isResistance ? ' R' : isAtm ? ' ATM' : ''}
        </text>
      </g>
    )
  }

  const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload?.length) return null
    const d = payload[0]?.payload
    return (
      <div className="bg-gray-900 border border-gray-700 rounded p-2 text-xs">
        <div className="text-white font-bold mb-1">{d.strike}</div>
        <div className="text-blue-400">PUT OI: {fmt(Math.abs(d.putOI))}</div>
        <div className="text-orange-400">CALL OI: {fmt(d.callOI)}</div>
      </div>
    )
  }

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-3">
        <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">OI Heatmap</h2>
        <div className="flex gap-4 text-xs">
          <span className="flex items-center gap-1"><span className="w-3 h-2 bg-blue-500 inline-block rounded-sm"></span> Put OI</span>
          <span className="flex items-center gap-1"><span className="w-3 h-2 bg-orange-500 inline-block rounded-sm"></span> Call OI</span>
        </div>
      </div>
      <div className="flex gap-4 text-xs text-gray-400 mb-2">
        <span>Support: <span className="text-green-400 font-bold">{support}</span></span>
        <span>Resistance: <span className="text-red-400 font-bold">{resistance}</span></span>
        <span>PCR: <span className="text-yellow-400 font-bold">{oiAnalysis.pcr} ({oiAnalysis.pcr_sentiment})</span></span>
        <span>Max Pain: <span className="text-purple-400 font-bold">{oiAnalysis.max_pain}</span></span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={chartData} layout="vertical" margin={{ left: 40, right: 20 }}>
          <XAxis type="number" tickFormatter={v => fmt(Math.abs(v))} tick={{ fontSize: 9, fill: '#6b7280' }} />
          <YAxis dataKey="strike" type="category" tick={<CustomTick />} width={60} />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine x={0} stroke="#374151" />
          <Bar dataKey="putOI" name="Put OI" radius={[0, 2, 2, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={entry.isSupport ? '#16a34a' : entry.isAtm ? '#1d4ed8' : '#3b82f6'} />
            ))}
          </Bar>
          <Bar dataKey="callOI" name="Call OI" radius={[0, 2, 2, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={entry.isResistance ? '#dc2626' : entry.isAtm ? '#ea580c' : '#f97316'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
