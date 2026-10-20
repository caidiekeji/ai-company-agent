/**
 * 后端 API 封装：REST + SSE 流式（fetch + ReadableStream）。
 *
 * 基址解析顺序：
 * 1. Tauri 桌面壳注入的 window.__API_BASE__（Rust 主进程探测到的 sidecar 端口）
 * 2. 运行时探测同源 /api/health（sidecar 托管前端产物时）
 * 3. 开发态默认 http://127.0.0.1:8756
 */

declare global {
  interface Window {
    __API_BASE__?: string
  }
}

let apiBase: string | null = null

export function getApiBase(): string {
  if (apiBase) return apiBase
  if (typeof window !== 'undefined' && window.__API_BASE__) {
    apiBase = window.__API_BASE__.replace(/\/$/, '')
    return apiBase
  }
  apiBase = ''
  return apiBase
}

/** Tauri 环境探测：若在壳内则向 Rust 询问 sidecar 基址。 */
export async function resolveApiBase(): Promise<string> {
  if (apiBase) return apiBase
  const tauri = (globalThis as { __TAURI__?: { core?: { invoke?: (c: string, a?: unknown) => Promise<unknown> } } }).__TAURI__
  if (tauri?.core?.invoke) {
    try {
      const info = (await tauri.core.invoke('sidecar_info')) as { base_url?: string } | null
      if (info?.base_url) {
        apiBase = info.base_url.replace(/\/$/, '')
        return apiBase
      }
    } catch {
      /* 忽略：回落到同源探测 */
    }
  }
  apiBase = ''
  return apiBase
}

function url(path: string): string {
  const base = getApiBase()
  return `${base}${path}`
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  await resolveApiBase()
  const resp = await fetch(url(path), {
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

/** 文件上传（头像、音频）。 */
export async function upload<T = unknown>(path: string, form: FormData): Promise<T> {
  await resolveApiBase()
  const resp = await fetch(url(path), { method: 'POST', body: form })
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

export function assetUrl(path: string): string {
  return url(path)
}

export type SseHandlers = Record<string, (data: Record<string, unknown>) => void>

/** POST 方式消费 SSE（EventSource 不支持 POST，用 fetch 流式解析）。 */
export async function streamEvents(path: string, body: unknown, handlers: SseHandlers): Promise<void> {
  await resolveApiBase()
  const resp = await fetch(url(path), {
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
