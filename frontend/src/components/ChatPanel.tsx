import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type ChatMessage, type ChatReply } from '../api'

const SUGGESTIONS = [
  'Which items will stock out this week?',
  'How much cash is tied up in overstock, and where?',
  'If I order 800 units of Orange Juice today, do I avoid a stockout?',
  'Which category is trending up the most?',
]

const TOOL_LABELS: Record<string, string> = {
  get_portfolio_summary: 'Portfolio summary',
  list_items: 'Listed items',
  get_item: 'Item details',
  get_category_trends: 'Category trends',
  get_model_accuracy: 'Model accuracy',
  simulate_order: 'Simulated order',
}

type Turn = ChatMessage & { tools?: ChatReply['tool_calls'] }

export function ChatPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const health = useQuery({ queryKey: ['health'], queryFn: api.health })

  const send = useMutation({
    mutationFn: (history: ChatMessage[]) => api.chat(history),
    onSuccess: (reply) => setTurns((t) => [...t, { role: 'assistant', content: reply.answer, tools: reply.tool_calls }]),
  })

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, send.isPending])

  const ask = (text: string) => {
    const q = text.trim()
    if (!q || send.isPending) return
    const next = [...turns, { role: 'user' as const, content: q }]
    setTurns(next)
    setDraft('')
    send.mutate(next.map(({ role, content }) => ({ role, content })))
  }

  const llmDisabled = health.data && !health.data.llm_enabled

  return (
    <>
      <div className={`fixed inset-0 z-30 bg-slate-900/20 transition-opacity ${open ? 'opacity-100' : 'pointer-events-none opacity-0'}`} onClick={onClose} />
      <aside
        className={`fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l border-slate-200 bg-white shadow-xl transition-transform ${open ? 'translate-x-0' : 'translate-x-full'}`}
        aria-hidden={!open}
      >
        <header className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <div>
            <h2 className="text-sm font-semibold">
              <span className="text-indigo-500">✦</span> Ask your inventory
            </h2>
            <p className="text-xs text-slate-500">Claude answers using live forecasts and stock data</p>
          </div>
          <div className="flex items-center gap-3">
            {turns.length > 0 && (
              <button onClick={() => setTurns([])} className="text-xs text-slate-500 hover:text-slate-800">
                Clear
              </button>
            )}
            <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close chat">
              ✕
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-sm">
          {llmDisabled && (
            <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Chat needs an Anthropic API key. Set <code>ANTHROPIC_API_KEY</code> in <code>backend/.env</code> and restart the backend.
            </p>
          )}
          {turns.length === 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Try asking</p>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => ask(s)}
                  disabled={!!llmDisabled}
                  className="block w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-slate-700 hover:border-indigo-300 hover:bg-indigo-50/50 disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          {turns.map((t, i) =>
            t.role === 'user' ? (
              <div key={i} className="ml-10 rounded-2xl rounded-br-sm bg-slate-900 px-3.5 py-2 text-white">
                {t.content}
              </div>
            ) : (
              <div key={i} className="mr-4 space-y-2">
                {t.tools && t.tools.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {t.tools.map((c, j) => (
                      <span
                        key={j}
                        title={JSON.stringify(c.input)}
                        className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${c.is_error ? 'bg-red-50 text-red-600' : 'bg-indigo-50 text-indigo-600'}`}
                      >
                        ⚙ {TOOL_LABELS[c.name] ?? c.name}
                      </span>
                    ))}
                  </div>
                )}
                <div className="prose-chat leading-relaxed text-slate-800">
                  <Markdown remarkPlugins={[remarkGfm]}>{t.content}</Markdown>
                </div>
              </div>
            ),
          )}
          {send.isPending && (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="size-2 animate-pulse rounded-full bg-indigo-400" /> Looking up forecasts and stock…
            </div>
          )}
          {send.error && <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{send.error.message}</p>}
          <div ref={endRef} />
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            ask(draft)
          }}
          className="flex gap-2 border-t border-slate-100 p-3"
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ask about stock, demand, or orders…"
            disabled={!!llmDisabled}
            className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
          />
          <button
            type="submit"
            disabled={!draft.trim() || send.isPending || !!llmDisabled}
            className="rounded-lg bg-indigo-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            Send
          </button>
        </form>
      </aside>
    </>
  )
}
