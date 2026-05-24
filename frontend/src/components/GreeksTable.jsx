export default function GreeksTable({ data, strategy }) {
  if (!data) return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase mb-3">Greeks</h2>
      <p className="text-gray-600 text-sm">Waiting for Greeks data...</p>
    </div>
  )

  const rows = [
    { label: 'Sell Put', strike: strategy?.sell_put_strike || data.sell_put_strike, type: 'PUT', action: 'SELL', greeks: data.sell_put },
    { label: 'Buy Put', strike: strategy?.buy_put_strike || data.buy_put_strike, type: 'PUT', action: 'BUY', greeks: data.buy_put },
    { label: 'Sell Call', strike: strategy?.sell_call_strike || data.sell_call_strike, type: 'CALL', action: 'SELL', greeks: data.sell_call },
    { label: 'Buy Call', strike: strategy?.buy_call_strike || data.buy_call_strike, type: 'CALL', action: 'BUY', greeks: data.buy_call },
  ].filter(row => row.strike && row.greeks)

  const sourceLabel = {
    broker: 'Broker Greeks',
    calculated_bid_ask_mid: 'Calculated from Bid/Ask',
    calculated_ltp: 'Calculated from LTP',
    mock: 'Mock Greeks',
    mixed: 'Mixed Sources',
  }[data.greeks_source] || data.greeks_source || 'Unknown Source'

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase mb-3">Greeks</h2>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-gray-900 rounded p-2 text-center">
          <div className="text-xs text-gray-500">ATM Strike</div>
          <div className="text-lg font-bold text-blue-400">{data.atm_strike}</div>
          <div className="text-xs text-gray-400">IV: <span className="text-yellow-400">{data.atm_iv}%</span></div>
        </div>
        <div className="bg-gray-900 rounded p-2 text-center">
          <div className="text-xs text-gray-500">Daily Move</div>
          <div className="text-lg font-bold text-purple-400">
            ±{data.expected_daily_move != null ? data.expected_daily_move.toFixed(0) : '—'}
          </div>
          <div className="text-xs text-gray-400">points</div>
        </div>
      </div>

      <div className="mb-3 text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1 text-gray-400">
        Source: <span className="text-blue-300">{sourceLabel}</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left py-1">Strike</th>
              <th className="text-left py-1">Action</th>
              <th className="text-right py-1">Price Used</th>
              <th className="text-right py-1">LTP</th>
              <th className="text-right py-1">Delta</th>
              <th className="text-right py-1">Theta</th>
              <th className="text-right py-1">IV%</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                <td className="py-1.5 font-mono text-white">
                  {row.strike} <span className="text-gray-600">{row.type[0]}</span>
                </td>
                <td className="py-1.5">
                  <span className={`px-1.5 py-0.5 rounded text-xs font-bold ${
                    row.action === 'SELL' ? 'bg-red-900/40 text-red-400' : 'bg-green-900/40 text-green-400'
                  }`}>{row.action}</span>
                </td>
                <td className="py-1.5 text-right font-mono text-blue-300">{row.greeks?.price_used?.toFixed(2) || '—'}</td>
                <td className="py-1.5 text-right font-mono text-white">{row.greeks?.ltp?.toFixed(2) || '—'}</td>
                <td className="py-1.5 text-right font-mono text-gray-300">{row.greeks?.delta?.toFixed(3) || '—'}</td>
                <td className="py-1.5 text-right font-mono text-orange-400">{row.greeks?.theta?.toFixed(2) || '—'}</td>
                <td className="py-1.5 text-right font-mono text-yellow-400">{row.greeks?.iv?.toFixed(1) || '—'}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
