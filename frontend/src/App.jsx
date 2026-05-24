import { useState, useRef } from 'react'
import LiveTicker from './components/LiveTicker'
import AgentPipeline from './components/AgentPipeline'
import TechnicalPanel from './components/TechnicalPanel'
import OIHeatmap from './components/OIHeatmap'
import GreeksTable from './components/GreeksTable'
import StrategyCard from './components/StrategyCard'
import EvaluatorPanel from './components/EvaluatorPanel'
import HITLPanel from './components/HITLPanel'
import JsonViewer from './components/JsonViewer'
import AnalysisErrorBanner from './components/AnalysisErrorBanner'
import { startAnalysis } from './api'
import { parseAnalysisError } from './utils/parseAnalysisError'

const AGENT_KEYS = ['market_data', 'technical', 'options_chain', 'oi_analysis', 'greeks', 'strategy', 'evaluator']

export default function App() {
  const [agentStates, setAgentStates] = useState({})
  const [agentData, setAgentData] = useState({})
  const [isRunning, setIsRunning] = useState(false)
  const [isComplete, setIsComplete] = useState(false)
  const [runId, setRunId] = useState(null)
  const [startTime, setStartTime] = useState(null)
  const [error, setError] = useState(null)
  const esRef = useRef(null)

  const handleStart = () => {
    if (isRunning) return
    esRef.current?.close()
    esRef.current = null
    setIsRunning(true)
    setIsComplete(false)
    setError(null)
    setRunId(null)
    setAgentData({})
    const initialStates = {}
    AGENT_KEYS.forEach(k => { initialStates[k] = 'waiting' })
    setAgentStates(initialStates)
    setStartTime(Date.now())

    const es = startAnalysis((event) => {
      const { agent, status, data, elapsed, run_id, strategy, evaluation } = event

      if (agent === 'error') {
        setError(parseAnalysisError(event))
        setIsRunning(false)
        es.close()
        return
      }

      if (agent === 'complete') {
        setRunId(run_id)
        const evalPayload =
          evaluation?.status === 'skipped'
            ? { status: 'skipped', reason: evaluation.reason || 'pre_flight' }
            : evaluation
        setAgentData(prev => ({
          ...prev,
          strategy: strategy,
          evaluator: evalPayload,
        }))
        setIsRunning(false)
        setIsComplete(true)
        es.close()
        return
      }

      if (status === 'running') {
        setAgentStates(prev => ({ ...prev, [agent]: 'running' }))
      }

      if (status === 'skipped') {
        setAgentStates(prev => ({ ...prev, [agent]: 'skipped' }))
        if (agent === 'evaluator') {
          setAgentData(prev => ({
            ...prev,
            evaluator: { status: 'skipped', reason: event.reason || 'pre_flight' },
          }))
        }
      }

      if (status === 'done') {
        setAgentStates(prev => ({
          ...prev,
          [agent]: 'done',
          [`${agent}_elapsed`]: elapsed,
        }))
        if (data && agent !== 'options_chain') {
          setAgentData(prev => ({ ...prev, [agent]: data }))
        }
        if (agent === 'options_chain' && data) {
          // options chain raw data stored separately
          setAgentData(prev => ({ ...prev, options_chain_meta: data }))
        }
      }
    })

    esRef.current = es
  }

  const handleStop = () => {
    esRef.current?.close()
    setIsRunning(false)
  }

  const market = agentData.market_data
  const technical = agentData.technical
  const oi = agentData.oi_analysis
  const greeks = agentData.greeks
  const strategy = agentData.strategy
  const evaluation = agentData.evaluator

  // Reconstruct chain data for heatmap from oi analysis top strikes
  const chainForHeatmap = oi ? [
    ...(oi.top_put_strikes || []).map(s => ({ strike: s.strike, put_oi: s.oi, call_oi: 0 })),
    ...(oi.top_call_strikes || []).map(s => ({ strike: s.strike, put_oi: 0, call_oi: s.oi })),
  ].reduce((acc, cur) => {
    const existing = acc.find(r => r.strike === cur.strike)
    if (existing) {
      existing.put_oi += cur.put_oi
      existing.call_oi += cur.call_oi
    } else {
      acc.push({ ...cur })
    }
    return acc
  }, []) : null

  const jsonViewerData = {
    market_data: agentData.market_data,
    technical: agentData.technical,
    oi_analysis: agentData.oi_analysis,
    greeks: agentData.greeks,
    strategy: agentData.strategy,
    evaluator: agentData.evaluator,
  }

  return (
    <div className="min-h-screen bg-[#0f0f0f] p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-wide">Nifty Options Advisor</h1>
          <p className="text-xs text-gray-500">AI-powered options strategy recommendation system</p>
        </div>
        <div className="flex items-center gap-3">
          {isComplete && !isRunning && (
            <span className="text-xs text-green-400 border border-green-700 px-2 py-1 rounded">
              Run: {runId}
            </span>
          )}
          <button
            onClick={isRunning ? handleStop : handleStart}
            disabled={false}
            className={`px-5 py-2 rounded-lg font-bold text-sm transition-colors ${
              isRunning
                ? 'bg-red-700 hover:bg-red-600 text-white'
                : 'bg-blue-600 hover:bg-blue-500 text-white'
            }`}
          >
            {isRunning ? '■ Stop' : '▶ Run Analysis'}
          </button>
        </div>
      </div>

      {/* Live Ticker */}
      <LiveTicker />

      <AnalysisErrorBanner error={error} />

      {/* Main grid — shows once analysis started */}
      {(isRunning || isComplete) && (
        <>
          {/* Row 1: Pipeline + Technical | OI Heatmap | Greeks */}
          <div className="grid grid-cols-12 gap-4">
            {/* Left column */}
            <div className="col-span-3 space-y-4">
              <AgentPipeline agentStates={agentStates} startTime={startTime} />
              <TechnicalPanel data={technical} />
            </div>

            {/* Center */}
            <div className="col-span-5">
              <OIHeatmap
                chainData={chainForHeatmap}
                oiAnalysis={oi}
                marketData={market}
              />
            </div>

            {/* Right column */}
            <div className="col-span-4">
              <GreeksTable data={greeks} strategy={strategy} />
            </div>
          </div>

          {/* Row 2: Strategy + Evaluator */}
          <div className="grid grid-cols-12 gap-4">
            <div className="col-span-6">
              <StrategyCard data={strategy} />
            </div>
            <div className="col-span-6">
              <EvaluatorPanel data={evaluation} strategy={strategy} />
            </div>
          </div>

          {/* HITL Panel */}
          {isComplete && (
            <HITLPanel
              runId={runId}
              strategy={strategy}
              evaluation={evaluation}
              onDecision={(d) => console.log('Decision:', d)}
            />
          )}

          {/* JSON Viewer */}
          {isComplete && (
            <div className="space-y-2">
              <h2 className="text-sm font-bold text-gray-500 uppercase tracking-widest">Raw Agent Outputs</h2>
              {Object.entries(jsonViewerData).map(([key, data]) =>
                data ? <JsonViewer key={key} agentName={key} data={data} /> : null
              )}
            </div>
          )}
        </>
      )}

      {/* Idle state */}
      {!isRunning && !isComplete && (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="text-6xl mb-4">📊</div>
          <h2 className="text-xl text-gray-400 mb-2">Ready to Analyse</h2>
          <p className="text-gray-600 text-sm max-w-md">
            Click <span className="text-blue-400 font-semibold">Run Analysis</span> to start the agent pipeline.
            All 7 agents will run sequentially, analysing market data, technicals, OI, and recommending an options strategy.
          </p>
        </div>
      )}

      <div className="text-center text-gray-700 text-xs py-2">
        Nifty Options Advisor · For educational purposes only · Not financial advice
      </div>
    </div>
  )
}
