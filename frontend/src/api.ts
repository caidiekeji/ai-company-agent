/** 后端 API 封装：REST + SSE 流式（fetch + ReadableStream）。 */

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const resp = await fetch(path, {
    method: opts.method || 'GET',
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data)
    } catch {
      /* ignore */
    }
    throw new Error(msg)
  }
  return resp.json() as Promise<T>
}

export type SseHandlers = Record<string, (data: Record<string, unknown>) => void>

/** POST 方式消费 SSE（EventSource 不支持 POST，用 fetch 流式解析）。 */
export async function streamEvents(path: string, body: unknown, handlers: SseHandlers): Promise<void> {
  const resp = await fetch(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok || !resp.body) throw new Error(`流式请求失败 HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const chunks = buf.split('\n\n')
    buf = chunks.pop() ?? ''
    for (const chunk of chunks) {
      let ev = ''
      let data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (!ev) continue
      let parsed: Record<string, unknown> = {}
      try {
        parsed = data ? JSON.parse(data) : {}
      } catch {
        /* ignore */
      }
      handlers[ev]?.(parsed)
      if (ev === 'end') return
    }
  }
}
