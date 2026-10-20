import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { IconPlus, IconSearch, IconTrash } from '../icons'

type Item = {
  id: number; title: string; content: string; tags: string
  created_at: string; updated_at: string
  event?: { type: string; ref_id: number | null } | null
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

  const reload = useCallback(async () => {
    setItems(await api<Item[]>('/api/kb/items'))
  }, [])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  async function search() {
    if (!q.trim()) { setResults(null); setMode(''); return }
    const resp = await fetch(`/api/kb/search?q=${encodeURIComponent(q.trim())}`)
    const data = await resp.json()
    setResults(data.results)
    setMode(data.mode)
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

  const list = results ?? items

  return (
    <div>
      <div className="page-head">
        <h2>知识库</h2>
        <button className="btn primary" onClick={() => { setCurrent(null); setTitle(''); setContent(''); setTags('') }}>
          <IconPlus /> 新建条目
        </button>
      </div>
      {msg && <div className="banner">{msg}</div>}

      <div className="card">
        <div className="row">
          <input value={q} placeholder="语义/关键词检索，如：会议纪要工具 决议"
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()} />
          <button className="btn" onClick={search}><IconSearch /> 检索</button>
          {results !== null && (
            <button className="btn ghost" onClick={() => { setResults(null); setMode(''); setQ('') }}>清除</button>
          )}
        </div>
        {results !== null && (
          <p className="note">检索模式：{mode === 'fts' ? 'FTS5 全文（trigram）' : mode === 'like' ? '关键词回退' : '—'}，命中 {results.length} 条</p>
        )}
      </div>

      <div className="split">
        <section className="card">
          {list.length === 0 && <p className="note">暂无知识条目。会议纪要与 RPA 结果会自动沉淀到这里。</p>}
          {list.map((it) => (
            <div key={it.id} className={`config-row ${current?.id === it.id ? 'default' : ''}`}>
              <button className="link" onClick={() => { setCurrent(it); setTitle(it.title); setContent(it.content); setTags(it.tags) }}>
                <strong>{it.title}</strong>
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
        </section>
      </div>
    </div>
  )
}
