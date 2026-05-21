const MONTHS = {
  JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
  JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
}

/** OpenAlgo compact expiry e.g. 26MAY26 -> "26 May 2026" */
export function formatCompactExpiry(expiry) {
  if (!expiry) return null
  const raw = String(expiry).replace(/-/g, '').toUpperCase()
  const m = /^(\d{2})([A-Z]{3})(\d{2})$/.exec(raw)
  if (!m) return expiry
  const day = parseInt(m[1], 10)
  const mon = m[2]
  const year = 2000 + parseInt(m[3], 10)
  if (MONTHS[mon] == null) return expiry
  const label = new Date(year, MONTHS[mon], day).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
  return label
}

/** One-line expiry label for strategy / HITL panels */
export function expirySummary(strategy) {
  if (!strategy?.option_expiry) return null
  const dateLabel = formatCompactExpiry(strategy.option_expiry)
  const dte = strategy.days_to_expiry
  const dtePart = dte != null ? `${dte} DTE` : null
  if (strategy.is_expiry_day) {
    return { primary: dateLabel || strategy.option_expiry, badge: '0 DTE · expiry day', dtePart }
  }
  return {
    primary: dateLabel || strategy.option_expiry,
    badge: strategy.option_expiry,
    dtePart,
  }
}

export function legSymbol(expiry, strike, type) {
  if (!expiry || !strike) return null
  const compact = String(expiry).replace(/-/g, '').toUpperCase()
  return `NIFTY${compact}${strike}${type === 'PE' || type === 'PUT' ? 'PE' : 'CE'}`
}
