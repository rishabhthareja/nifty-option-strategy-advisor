import { useState } from 'react'

function highlight(json) {
  return json
    .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, (match) => {
      let cls = 'text-yellow-300'
      if (/^"/.test(match)) {
        cls = /:$/.test(match) ? 'text-blue-300' : 'text-green-300'
      } else if (/true|false/.test(match)) {
        cls = 'text-purple-400'
      } else if (/null/.test(match)) {
        cls = 'text-gray-500'
      }
      return `<span class="${cls}">${match}</span>`
    })
}

export default function JsonViewer({ agentName, data }) {
  const [open, setOpen] = useState(false)

  if (!data) return null

  const jsonStr = JSON.stringify(data, null, 2)
  const highlighted = highlight(jsonStr)

  const handleCopy = () => navigator.clipboard.writeText(jsonStr)

  const handleDownload = () => {
    const blob = new Blob([jsonStr], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${agentName}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2 bg-gray-900 hover:bg-gray-800 text-left transition-colors"
      >
        <span className="text-xs font-mono text-green-400">{open ? '▼' : '▶'} {agentName}.json</span>
        {!open && <span className="text-xs text-gray-600">{Object.keys(data).length} keys</span>}
      </button>

      {open && (
        <div className="bg-[#0a0a0a] relative">
          <div className="absolute top-2 right-2 flex gap-2 z-10">
            <button
              onClick={handleCopy}
              className="text-xs text-gray-500 hover:text-white bg-gray-800 px-2 py-1 rounded transition-colors"
            >
              Copy
            </button>
            <button
              onClick={handleDownload}
              className="text-xs text-gray-500 hover:text-white bg-gray-800 px-2 py-1 rounded transition-colors"
            >
              Download
            </button>
          </div>
          <pre
            className="text-xs p-4 overflow-x-auto max-h-64 font-mono leading-relaxed"
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        </div>
      )}
    </div>
  )
}
