import { useEffect, useState } from 'react'
import { getLiveData } from '../api'

function isMarketOpen() {
  const now = new Date()
  const day = now.getDay()
  if (day === 0 || day === 6) return false
  const h = now.getHours()
  const m = now.getMinutes()
  const mins = h * 60 + m
  return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30
}

export default function LiveTicker() {
  const [data, setData] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [error, setError] = useState(false)
  const open = isMarketOpen()

  const fetchData = async () => {
    try {
      const d = await getLiveData()
      setData(d)
      setLastUpdated(new Date().toLocaleTimeString())
      setError(false)
    } catch {
      setError(true)
    }
  }

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 30000)
    return () => clearInterval(id)
  }, [])

  const changeColor =
    !data ? 'text-gray-400'
    : data.change_pct > 0 ? 'text-green-400'
    : data.change_pct < 0 ? 'text-red-400'
    : 'text-gray-400'

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg px-6 py-3 flex items-center gap-8 flex-wrap">
      <div className="flex items-center gap-2">
        <span className="text-gray-400 text-sm font-bold tracking-widest">NIFTY</span>
        <span className="text-2xl font-bold text-white">
          {data ? data.nifty_spot.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
        </span>
        {data && (
          <span className={`text-sm font-semibold ${changeColor}`}>
            {data.change_pct >= 0 ? '+' : ''}{data.change_pct.toFixed(2)}%
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <span className="text-gray-400 text-sm">VIX</span>
        <span className="text-lg font-bold text-yellow-400">
          {data ? data.vix.toFixed(2) : '—'}
        </span>
      </div>

      {data && (
        <div className="flex gap-4 text-xs text-gray-500">
          <span>O: <span className="text-gray-300">{data.today_open.toFixed(2)}</span></span>
          <span>H: <span className="text-green-400">{data.today_high.toFixed(2)}</span></span>
          <span>L: <span className="text-red-400">{data.today_low.toFixed(2)}</span></span>
          <span>PC: <span className="text-gray-300">{data.prev_close.toFixed(2)}</span></span>
        </div>
      )}

      <div className="ml-auto flex items-center gap-3">
        {data?.is_mock && (
          <span className="text-xs bg-yellow-900/40 text-yellow-400 border border-yellow-700 px-2 py-0.5 rounded">MOCK DATA</span>
        )}
        {data && !data.is_mock && (
          <span className="text-xs bg-green-900/30 text-green-400 border border-green-800 px-2 py-0.5 rounded">
            {data.data_source || 'LIVE'}
          </span>
        )}
        <div className="flex items-center gap-1.5">
          {open
            ? <><span className="blink inline-block w-2 h-2 rounded-full bg-green-400"></span><span className="text-green-400 text-xs font-semibold">MARKET OPEN</span></>
            : <><span className="inline-block w-2 h-2 rounded-full bg-gray-600"></span><span className="text-gray-500 text-xs">MARKET CLOSED</span></>
          }
        </div>
        {lastUpdated && <span className="text-gray-600 text-xs">Updated {lastUpdated}</span>}
        {error && <span className="text-red-400 text-xs">Connection error</span>}
      </div>
    </div>
  )
}
