import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { IconBrowser, IconPlus, IconRefresh, IconShield } from '../icons'

type Element = { ref: string; role: string; name: string; state: Record<string, unknown> }
type Snapshot = { url: string; title: string; elements: Element[]; token_estimate: number; max_tokens: number }
type RpaStatus = { playwright_available: boolean; browser_installed: boolean; sessions: { id: string; status: string }[] }
type Run = {
  id: number; start_url: string; status: string; title: string; extracted_content: string
  employee_id: number | null; error: string; started_at: string
}
type Template = { id: number; name: string; description: string; url: string; steps: { type: string; url?: string }[] }

export default function RpaPage() {
  const [status, setStatus] = useState<RpaStatus | null>(null)
  const [url, setUrl] = useState('')
  const [sid, setSid] = useState('')
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [content, setContent] = useState('')
  const [msg, setMsg] = useState('')
  const [pending, setPending] = useState<{ label: string } | null>(null)
  const [tplName, setTplName] = useState('')

  const reload = useCallback(async () => {
    const [s, r, t] = await Promise.all([
      api<RpaStatus>('/api/rpa/status'),
      api<Run[]>('/api/rpa/runs'),
      api<Template[]>('/api/rpa/templates'),
    ])
    setStatus(s); setRuns(r); setTemplates(t)
  }, [])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  async function open() {
    if (!url.trim()) return
    try {
      const r = await api<{ session_id: string; snapshot: Snapshot; login_wall: { need_human: boolean } }>(
        '/api/rpa/open', { method: 'POST', body: { url: url.trim() } })
      setSid(r.session_id)
      setSnap(r.snapshot)
      setMsg(r.login_wall?.need_human
        ? '检测到登录墙或验证码：请在浏览器窗口中手动完成登录，完成后点击「重新获取快照」'
        : `已打开，识别到 ${r.snapshot.elements.length} 个可交互元素（≈${r.snapshot.token_estimate} token）`)
    } catch (e) { setMsg(String(e)) }
  }

  async function refreshSnapshot() {
    if (!sid) return
    try {
      const r = await api<Snapshot>(`/api/rpa/${sid}/snapshot`)
      setSnap(r)
    } catch (e) { setMsg(String(e)) }
  }

  async function act(action: { type: string; ref?: string; value?: string; delta?: number }) {
    if (!sid) return
    try {
      const r = await api<{ ok: boolean; need_confirm?: boolean; label?: string; detail?: string; message?: string }>(
        `/api/rpa/${sid}/action`, { method: 'POST', body: action })
      if (r.need_confirm) {
        setPending({ label: r.label ?? '' })
        setMsg(`高风险动作需确认：${r.label}`)
        return
      }
      setMsg(r.detail || r.message || '完成')
      await refreshSnapshot()
    } catch (e) { setMsg(String(e)) }
  }

  async function confirm() {
    if (!sid) return
    try {
      const r = await api<{ detail?: string }>(`/api/rpa/${sid}/confirm`, { method: 'POST', body: {} })
      setMsg(r.detail || '已确认并执行')
      setPending(null)
      await refreshSnapshot()
    } catch (e) { setMsg(String(e)) }
  }

  async function extract() {
    if (!sid) return
    try {
      const r = await api<{ content: string }>(`/api/rpa/${sid}/extract`, { method: 'POST', body: { format: 'markdown' } })
      setContent(r.content)
      setMsg(`已提取 ${r.content.length} 字符`)
    } catch (e) { setMsg(String(e)) }
  }

  async function closeSession() {
    if (!sid) return
    await api(`/api/rpa/${sid}/close`, { method: 'POST', body: {} })
    setSid(''); setSnap(null)
    setMsg('会话已关闭')
  }

  async function quickRun() {
    if (!url.trim()) return
    try {
      const r = await api<{ run_id: number; content: string; knowledge_item_id: number }>(
        '/api/rpa/run', { method: 'POST', body: { url: url.trim(), title: `RPA 提取：${url.trim()}` } })
      setContent(r.content)
      setMsg(`任务完成，已写入知识库（条目 #${r.knowledge_item_id}）`)
      reload()
    } catch (e) { setMsg(String(e)) }
  }

  async function toKnowledge(rid: number) {
    try {
      const r = await api<{ knowledge_item_id: number }>(`/api/rpa/runs/${rid}/to-knowledge`, { method: 'POST', body: {} })
      setMsg(`已写入知识库（条目 #${r.knowledge_item_id}）`)
    } catch (e) { setMsg(String(e)) }
  }

  async function saveTemplate() {
    if (!tplName.trim()) return
    await api('/api/rpa/templates', {
      method: 'POST',
      body: { name: tplName.trim(), url: url.trim(), steps: [{ type: 'open', url: url.trim() }, { type: 'extract' }] },
    })
    setTplName('')
    reload()
  }

  return (
    <div>
      <div className="page-head">
        <h2>浏览器 RPA</h2>
        <div className="row">
          <button className="btn" onClick={() => reload()}><IconRefresh /> 刷新</button>
        </div>
      </div>
      {msg && <div className="banner">{msg}</div>}

      {status && !status.playwright_available && (
        <div className="banner warn">
          Playwright 运行时未安装。请在「系统设置 → 模型包管理」安装 Chromium 内核，
          并确认后端依赖包含 <code>playwright</code>。
        </div>
      )}

      <div className="split-wide">
        <div>
          <section className="card">
            <h3><IconBrowser /> 打开网页</h3>
            <input value={url} onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/pricing" />
            <div className="row">
              <button className="btn primary" onClick={open}>打开</button>
              <button className="btn" onClick={quickRun}>一键提取并入库</button>
              {sid && <button className="btn ghost" onClick={closeSession}>关闭会话</button>}
            </div>
            <p className="note">
              仅允许访问「系统设置」中域名白名单内的站点；提交/支付类动作需二次确认。
            </p>
          </section>

          <section className="card">
            <h3>任务模板（F-RPA-09）</h3>
            <div className="row">
              <input value={tplName} onChange={(e) => setTplName(e.target.value)} placeholder="模板名称" />
              <button className="btn" onClick={saveTemplate}><IconPlus /> 保存</button>
            </div>
            {templates.map((t) => (
              <div key={t.id} className="config-row">
                <span>
                  <strong>{t.name}</strong>
                  <div className="note">{t.url} · {t.steps.length} 步</div>
                </span>
                <button className="btn small" onClick={() => { setUrl(t.url); setMsg(`已载入模板「${t.name}」`) }}>
                  载入
                </button>
              </div>
            ))}
          </section>

          <section className="card">
            <h3>历史任务</h3>
            {runs.length === 0 && <p className="note">暂无任务记录。</p>}
            {runs.map((r) => (
              <div key={r.id} className="config-row">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <strong>{r.title || r.start_url}</strong>
                  <div className="note">
                    #{r.id} · {r.status} · {r.started_at}
                    {r.extracted_content ? ` · ${r.extracted_content.length} 字符` : ''}
                  </div>
                  {r.error && <div className="note warn">{r.error}</div>}
                </div>
                {r.extracted_content && (
                  <button className="btn small" onClick={() => toKnowledge(r.id)}>入知识库</button>
                )}
              </div>
            ))}
          </section>
        </div>

        <div>
          <section className="card">
            <h3>无障碍树快照（F-RPA-01）</h3>
            {!snap && <p className="note">打开网页后自动获取。</p>}
            {snap && (
              <>
                <p className="note">
                  {snap.title} · {snap.elements.length} 个元素 · ≈{snap.token_estimate} token
                  （上限 {snap.max_tokens}）
                </p>
                {pending && (
                  <div className="banner warn">
                    <IconShield /> 高风险动作需确认：<strong>{pending.label}</strong>
                    <button className="btn small" style={{ marginLeft: 8 }} onClick={confirm}>确认执行</button>
                    <button className="btn small ghost" onClick={() => setPending(null)}>取消</button>
                  </div>
                )}
                <div className="feed">
                  {snap.elements.map((el) => (
                    <div key={el.ref} className="rpa-el">
                      <span className="mono note">{el.ref}</span>
                      <span className="badge">{el.role}</span>
                      <span className="rpa-name">{el.name || '(无名称)'}</span>
                      <span className="row" style={{ margin: 0, gap: 4 }}>
                        <button className="btn small" onClick={() => act({ type: 'click', ref: el.ref })}>点击</button>
                        {el.role === 'textbox' || el.role === 'searchbox' ? (
                          <button className="btn small" onClick={() => {
                            const v = window.prompt('输入内容', '')
                            if (v) act({ type: 'type', ref: el.ref, value: v })
                          }}>输入</button>
                        ) : null}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="row">
                  <button className="btn" onClick={refreshSnapshot}><IconRefresh /> 重新获取</button>
                  <button className="btn" onClick={() => act({ type: 'scroll', delta: 600 })}>下滚</button>
                  <button className="btn" onClick={extract}>提取正文</button>
                </div>
              </>
            )}
          </section>

          <section className="card">
            <h3>提取内容（F-RPA-03）</h3>
            {!content && <p className="note">提取的网页内容会以 Markdown 结构化输出，并可写入知识库。</p>}
            {content && <pre className="extract-box">{content}</pre>}
          </section>
        </div>
      </div>
    </div>
  )
}
