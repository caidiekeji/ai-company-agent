import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { IconPlus, IconRefresh, IconSearch, IconTrash } from '../icons'

type Item = {
  id: number; title: string; content: string; tags: string
  created_at: string; updated_at: string
  embedding_status?: string; embedding_model?: string
  score?: number; rebuilding?: boolean
  event?: { type: string; ref_id: number | null } | null
}
type EmbModel = { id: string; dim: number; size_mb: number; note: string; installed: boolean; current: boolean }
type EmbInfo = {
  current: string; models: EmbModel[]; rebuilding: number; vec_backend: string
  job: { id: number; status: string; total: number; done: number; progress: number }
}

const MODE_LABEL: Record<string, string> = {
  vector: '语义向量召回',
  fts: 'FTS5 全文（trigram 中文分词）',
  like: '关键词回退',
  '-': '—',
}

export default function KbPage() {
  const [items, setItems] = useState<Item[]>([])
  const [q, setQ] = useState('')
  const [mode, setMode] = useState('')
  const [results, setResults] = useState<Item[] | null>(null)
  const [current, setCurrent] = useState<Item | null>(null)
  const [msg, setMsg] = useState('')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [tags, setTags] = useState('')
  const [emb, setEmb] = useState<EmbInfo | null>(null)
  const [searchMode, setSearchMode] = useState('auto')

  const reload = useCallback(async () => {
    const [its, e] = await Promise.all([
      api<Item[]>('/api/kb/items'),
      api<EmbInfo>('/api/kb/embedding'),
    ])
    setItems(its); setEmb(e)
  }, [])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  async function search() {
    if (!q.trim()) { setResults(null); setMode(''); return }
    try {
      const r = await api<{ results: Item[]; mode: string }>(
        `/api/kb/search?q=${encodeURIComponent(q.trim())}&mode=${searchMode}`)
      setResults(r.results)
      setMode(r.mode)
    } catch (e) { setMsg(String(e)) }
  }

  async function save() {
    if (!title.trim() || !content.trim()) { setMsg('标题与正文必填'); return }
    if (current?.id) {
      await api(`/api/kb/items/${current.id}`, { method: 'PUT', body: { title, content, tags } })
    } else {
      await api('/api/kb/items', { method: 'POST', body: { title, content, tags, auto_event: true } })
    }
    setTitle(''); setContent(''); setTags(''); setCurrent(null)
    reload()
  }

  async function del(id: number) {
    await api(`/api/kb/items/${id}`, { method: 'DELETE' })
    if (current?.id === id) setCurrent(null)
    reload()
  }

  /** F-KB-10：切换嵌入模型 → 后台全量重嵌入 */
  async function switchModel(model: string) {
    try {
      const r = await api<{ message: string }>('/api/kb/embedding/switch', {
        method: 'POST', body: { model },
      })
      setMsg(r.message)
      setTimeout(reload, 1200)
    } catch (e) { setMsg(String(e)) }
  }

  const list = results ?? items

  return (
    <div>
      <div className="page-head">
        <h2>知识库</h2>
        <div className="row">
          <button className="btn" onClick={() => reload()}><IconRefresh /> 刷新</button>
          <button className="btn primary" onClick={() => { setCurrent(null); setTitle(''); setContent(''); setTags('') }}>
            <IconPlus /> 新建条目
          </button>
        </div>
      </div>
      {msg && <div className="banner">{msg}</div>}

      {emb && (
        <div className="card">
          <div className="row space">
            <h3 style={{ margin: 0 }}>语义检索（F-KB-05 / F-KB-10）</h3>
            <span className="note">
              当前：{emb.current} · 向量后端 {emb.vec_backend}
              {emb.rebuilding > 0 && <span className="note warn"> · {emb.rebuilding} 条重建中</span>}
            </span>
          </div>
          <div className="row">
            {emb.models.map((m) => (
              <div key={m.id} className={`config-row ${m.current ? 'default' : ''}`} style={{ flex: 1 }}>
                <div>
                  <strong>{m.id}</strong>
                  <div className="note">{m.dim} 维 · ~{m.size_mb} MB · {m.installed ? '已下载' : '未下载'} · {m.note}</div>
                </div>
                {!m.current && (
                  <button className="btn small" onClick={() => switchModel(m.id)}>切换</button>
                )}
              </div>
            ))}
          </div>
          {emb.job?.status === 'running' && (
            <div className="budget">
              <div className="budget-bar">
                <div className="budget-fill" style={{ width: `${Math.round((emb.job.progress ?? 0) * 100)}%` }} />
              </div>
              <span className="note">重建中 {emb.job.done}/{emb.job.total}（期间相关条目降级为关键词检索）</span>
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="row">
          <input value={q} placeholder="语义/关键词检索，如：会议纪要工具 决议"
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
          <select value={searchMode} onChange={(e) => setSearchMode(e.target.value)} style={{ width: 160 }}>
            <option value="auto">自动（优先语义）</option>
            <option value="vector">仅语义</option>
            <option value="fts">仅全文</option>
            <option value="like">仅关键词</option>
          </select>
          <button className="btn" onClick={search}><IconSearch /> 检索</button>
          {results !== null && (
            <button className="btn ghost" onClick={() => { setResults(null); setMode(''); setQ('') }}>清除</button>
          )}
        </div>
        {results !== null && (
          <p className="note">检索模式：{MODE_LABEL[mode] ?? mode}，命中 {results.length} 条</p>
        )}
      </div>

      <div className="split">
        <section className="card">
          {list.length === 0 && <p className="note">暂无知识条目。会议纪要与 RPA 结果会自动沉淀到这里。</p>}
          {list.map((it) => (
            <div key={it.id} className={`config-row ${current?.id === it.id ? 'default' : ''}`}>
              <button className="link" onClick={() => { setCurrent(it); setTitle(it.title); setContent(it.content); setTags(it.tags) }}>
                <strong>{it.title}</strong>
                {it.rebuilding && <span className="badge warn-badge">重建中</span>}
                {typeof it.score === 'number' && <span className="badge">相关度 {it.score}</span>}
                <div className="note">
                  {it.event ? (it.event.type === 'meeting' ? '会议纪要' : it.event.type === 'rpa' ? 'RPA 提取' : '手动') : '手动'}
                  {it.tags ? ` · ${it.tags}` : ''} · {it.created_at}
                </div>
              </button>
              <button className="btn small danger ghost" onClick={() => del(it.id)}><IconTrash /></button>
            </div>
          ))}
        </section>

        <section className="card">
          <h3>{current ? '编辑条目' : '新建条目'}</h3>
          <label>标题</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
          <label>正文</label>
          <textarea rows={10} value={content} onChange={(e) => setContent(e.target.value)} />
          <label>标签（逗号分隔）</label>
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="竞品,定价" />
          <div className="row">
            <button className="btn primary" onClick={save}>保存</button>
            {current && (
              <button className="btn ghost" onClick={() => { setCurrent(null); setTitle(''); setContent(''); setTags('') }}>
                取消编辑
              </button>
            )}
          </div>
          {current?.embedding_model && (
            <p className="note">向量模型：{current.embedding_model}（{current.embedding_status}）</p>
          )}
        </section>
      </div>
    </div>
  )
}
