import { useEffect, useState } from 'react'

const AGENTS = [
  { key: 'market_data', label: 'Market Data', icon: '📡' },
  { key: 'technical', label: 'Technical Analysis', icon: '📊' },
  { key: 'options_chain', label: 'Options Chain', icon: '🔗' },
  { key: 'oi_analysis', label: 'OI Analysis', icon: '🔥' },
  { key: 'greeks', label: 'Greeks Calculator', icon: '🔢' },
  { key: 'strategy', label: 'Strategy Agent', icon: '🤖' },
  { key: 'evaluator', label: 'Evaluator Agent', icon: '⚖️' },
]

export default function AgentPipeline({ agentStates, startTime }) {
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(id)
  }, [])

  const totalElapsed = startTime ? ((now - startTime) / 1000).toFixed(1) : null

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-center mb-3">
        <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">Agent Pipeline</h2>
        {totalElapsed && (
          <span className="text-xs text-gray-500">Total: {totalElapsed}s</span>
        )}
      </div>
      <div className="space-y-2">
        {AGENTS.map(({ key, label, icon }) => {
          const state = agentStates[key] || 'waiting'
          const elapsed = agentStates[`${key}_elapsed`]
          return (
            <div key={key} className="flex items-center gap-3">
              <span className="text-base w-6 text-center">{icon}</span>
              <span className="flex-1 text-sm text-gray-300">{label}</span>
              {state === 'waiting' && (
                <span className="text-xs text-gray-600 bg-gray-800 px-2 py-0.5 rounded">waiting</span>
              )}
              {state === 'running' && (
                <span className="flex items-center gap-1.5">
                  <span className="spinner"></span>
                  <span className="text-xs text-yellow-400">running</span>
                </span>
              )}
              {state === 'skipped' && (
                <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">skipped</span>
              )}
              {state === 'done' && (
                <span className="flex items-center gap-1.5">
                  <span className="text-green-400 text-sm">✓</span>
                  <span className="text-xs text-green-400">{elapsed ? `${elapsed}s` : 'done'}</span>
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
