export default function TechnicalPanel({ data }) {
  if (!data) return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase mb-3">Technical Analysis</h2>
      <p className="text-gray-600 text-sm">Waiting for analysis...</p>
    </div>
  )

  const rsiColor = data.rsi > 70 ? 'text-red-400' : data.rsi < 30 ? 'text-green-400' : 'text-yellow-400'
  const trendColor =
    data.trend.includes('UP') ? 'bg-green-900/40 text-green-400 border-green-700'
    : data.trend.includes('DOWN') ? 'bg-red-900/40 text-red-400 border-red-700'
    : 'bg-gray-800 text-gray-400 border-gray-600'
  const macdColor = data.macd_signal_text === 'BULLISH' ? 'bg-green-900/40 text-green-400 border-green-700' : 'bg-red-900/40 text-red-400 border-red-700'

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 space-y-3">
      <div className="flex justify-between items-center">
        <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">Technical Analysis</h2>
        <span className={`text-xs border px-2 py-0.5 rounded ${trendColor}`}>{data.trend}</span>
      </div>

      {/* RSI */}
      <div>
        <div className="flex justify-between text-xs text-gray-400 mb-1">
          <span>RSI (14)</span>
          <span className={rsiColor}>{data.rsi} — {data.rsi_signal}</span>
        </div>
        <div className="relative h-3 bg-gray-800 rounded-full overflow-hidden">
          <div className="absolute left-0 top-0 h-full w-[30%] bg-green-900/50 rounded-l-full"></div>
          <div className="absolute right-0 top-0 h-full w-[30%] bg-red-900/50 rounded-r-full"></div>
          <div
            className="absolute top-0.5 h-2 w-2 rounded-full bg-white shadow"
            style={{ left: `calc(${data.rsi}% - 4px)` }}
          ></div>
        </div>
        <div className="flex justify-between text-xs text-gray-600 mt-0.5">
          <span>0 Oversold</span><span>50</span><span>Overbought 100</span>
        </div>
      </div>

      {/* MACD */}
      <div className="flex justify-between items-center">
        <div>
          <span className="text-xs text-gray-400">MACD Line </span>
          <span className="text-sm text-white font-mono">{data.macd_line}</span>
        </div>
        <span className={`text-xs border px-2 py-0.5 rounded ${macdColor}`}>{data.macd_signal_text}</span>
      </div>

      {/* SMAs */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-gray-900 rounded p-2">
          <div className="text-xs text-gray-500">SMA 20</div>
          <div className="text-sm font-mono text-white">{data.sma_20.toLocaleString('en-IN')}</div>
        </div>
        <div className="bg-gray-900 rounded p-2">
          <div className="text-xs text-gray-500">SMA 50</div>
          <div className="text-sm font-mono text-white">{data.sma_50.toLocaleString('en-IN')}</div>
        </div>
      </div>

      {/* ATR & BB */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-gray-900 rounded p-2">
          <div className="text-xs text-gray-500">ATR (14)</div>
          <div className="text-sm font-mono text-yellow-400">{data.atr} pts</div>
        </div>
        <div className="bg-gray-900 rounded p-2">
          <div className="text-xs text-gray-500">BB Width</div>
          <div className="text-sm font-mono text-purple-400">{data.bb_width}</div>
        </div>
      </div>
    </div>
  )
}
