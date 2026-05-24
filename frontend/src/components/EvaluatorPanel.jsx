function isEvaluatorSkipped(data) {
  return data?.status === 'skipped' || data?.skipped === true
}

export default function EvaluatorPanel({ data, strategy }) {
  if (!data) return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase mb-3">Evaluator</h2>
      <p className="text-gray-600 text-sm">Waiting for evaluation...</p>
    </div>
  )

  if (isEvaluatorSkipped(data)) {
    const reason = data.reason === 'pre_flight' ? 'pre-flight guardrails' : (data.reason || 'system')
    return (
      <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 space-y-3">
        <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">Independent Evaluator</h2>
        <div className="bg-gray-900/80 border border-gray-700 rounded-lg p-4 text-center space-y-2">
          <p className="text-gray-400 text-sm font-semibold uppercase tracking-wide">Skipped</p>
          <p className="text-gray-300 text-sm">
            No evaluator LLM run — strategy already returned WAIT from {reason}.
          </p>
          {strategy?.wait_reason && (
            <p className="text-xs text-yellow-300/90 text-left leading-snug border-t border-gray-700 pt-2 mt-2">
              {strategy.wait_reason}
            </p>
          )}
        </div>
      </div>
    )
  }

  const score = data.quality_score
  if (score == null || typeof score !== 'number') {
    return (
      <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase mb-3">Evaluator</h2>
        <p className="text-gray-500 text-sm">Evaluation result unavailable.</p>
      </div>
    )
  }

  const scoreColor = score >= 8 ? 'text-green-400' : score >= 6 ? 'text-yellow-400' : 'text-red-400'
  const scoreRing = score >= 8 ? 'border-green-500' : score >= 6 ? 'border-yellow-500' : 'border-red-500'
  const confColor = {
    HIGH: 'bg-green-900/40 text-green-400 border-green-700',
    MEDIUM: 'bg-yellow-900/40 text-yellow-400 border-yellow-700',
    LOW: 'bg-red-900/40 text-red-400 border-red-700',
  }[data.recommendation_confidence] || ''

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 space-y-4">
      <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">Independent Evaluator</h2>

      <div className="flex items-center gap-6">
        <div className={`w-20 h-20 rounded-full border-4 ${scoreRing} flex flex-col items-center justify-center`}>
          <span className={`text-3xl font-bold ${scoreColor}`}>{score.toFixed(1)}</span>
          <span className="text-xs text-gray-500">/10</span>
        </div>
        <div className="space-y-2">
          <div className="flex flex-col gap-1">
            {data.is_validated
              ? <span className="text-green-400 font-bold flex items-center gap-1"><span className="text-lg">✓</span> VALIDATED</span>
              : <span className="text-red-400 font-bold flex items-center gap-1"><span className="text-lg">✗</span> NOT VALIDATED</span>
            }
            {!data.is_validated && strategy?.integrity_blocked && (
              <span className="text-xs text-red-300">Blocked by data guardrail</span>
            )}
            {!data.is_validated && strategy && strategy.data_trade_ready === false && !strategy.integrity_blocked && (
              <span className="text-xs text-amber-300">Data not trade-ready (mock quote or chain)</span>
            )}
          </div>
          <span className={`text-xs border px-2 py-0.5 rounded ${confColor}`}>
            {data.recommendation_confidence} CONFIDENCE
          </span>
        </div>
      </div>

      {data.evidence_supporting?.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Evidence Supporting</p>
          <ul className="space-y-1">
            {data.evidence_supporting.map((e, i) => (
              <li key={i} className="flex items-start gap-1.5 text-sm text-green-400">
                <span className="mt-0.5 shrink-0">✓</span><span>{e}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.evidence_contradicting?.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Evidence Contradicting</p>
          <ul className="space-y-1">
            {data.evidence_contradicting.map((e, i) => (
              <li key={i} className="flex items-start gap-1.5 text-sm text-red-400">
                <span className="mt-0.5 shrink-0">⚠</span><span>{e}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.gaps_in_analysis?.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Gaps in Analysis</p>
          <ul className="space-y-1">
            {data.gaps_in_analysis.map((g, i) => (
              <li key={i} className="flex items-start gap-1.5 text-sm text-gray-400">
                <span className="mt-0.5 shrink-0">○</span><span>{g}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Evaluator Reasoning</p>
        <p className="text-sm text-gray-300 leading-relaxed">{data.evaluator_reasoning}</p>
      </div>

      {data.suggested_adjustment && (
        <div className="bg-blue-900/20 border border-blue-800 rounded p-3">
          <p className="text-xs text-blue-400 font-bold mb-1">Suggested Adjustment</p>
          <p className="text-sm text-blue-300">{data.suggested_adjustment}</p>
        </div>
      )}
    </div>
  )
}
