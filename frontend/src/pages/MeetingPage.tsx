import { useCallback, useEffect, useRef, useState } from 'react'
import { api, streamEvents } from '../api'
import { IconPlay, IconPlus, IconSend, IconStop, IconTrash } from '../icons'

type Meeting = {
  id: number; topic: string; status: string; scheduler_mode: string
  max_rounds: number; token_budget: number; total_tokens_used: number
  summary?: string; decisions: string[]; todos: { task: string; owner: string }[]
  cost_estimate: { est_tokens: number; used: number; budget: number }
  participants?: { id: number; name: string }[]
}
type Msg = { id: number; role: string; content: string; employee_name: string; employee_id: number | null }
type Employee = { id: number; name: string; enabled: number }

export default function MeetingPage() {
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [employees, setEmployees] = useState<Employee[]>([])
  const [current, setCurrent] = useState<Meeting | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [msg, setMsg] = useState('')
  const [running, setRunning] = useState(false)
  // 创建表单
  const [topic, setTopic] = useState('')
  const [picked, setPicked] = useState<number[]>([])
  const [hostId, setHostId] = useState<number | ''>('')
  const [mode, setMode] = useState('round_robin')
  const [maxRounds, setMaxRounds] = useState(4)
  const [budget, setBudget] = useState(60000)
  // 插话
  const [speak, setSpeak] = useState('')
  const [nextPick, setNextPick] = useState<number | ''>('')
  const feedRef = useRef<HTMLDivElement>(null)

  const reload = useCallback(async () => {
    const [ms, emps] = await Promise.all([
      api<Meeting[]>('/api/meetings'),
      api<Employee[]>('/api/employees'),
    ])
    setMeetings(ms); setEmployees(emps.filter((e) => e.enabled === 1))
  }, [])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  useEffect(() => { feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight }) }, [messages])

  async function open(id: number) {
    const m = await api<Meeting & { messages: Msg[]; participants: { id: number; name: string }[] }>(`/api/meetings/${id}`)
    setCurrent(m)
    setMessages(m.messages)
  }

  async function create() {
    if (!topic.trim() || picked.length < 2) { setMsg('议题必填，参会员工至少 2 人'); return }
    const r = await api<{ id: number }>('/api/meetings', {
      method: 'POST',
      body: { topic: topic.trim(), participant_ids: picked, host_employee_id: hostId || null,
              scheduler_mode: mode, max_rounds: maxRounds, token_budget: budget },
    })
    setTopic(''); setPicked([])
    await reload()
    open(r.id)
  }

  async function run() {
    if (!current) return
    setRunning(true)
    setMessages((prev) => [...prev.filter((m) => m.content !== '[正在发言…]')])
    try {
      await streamEvents(`/api/meetings/${current.id}/run`, null, {
        speaker_start: (d) => setMessages((p) => [...p, { id: Date.now(), role: 'member', content: '[正在发言…]', employee_name: String(d.name), employee_id: Number(d.employee_id) }]),
        token: (d) => setMessages((p) => {
          const copy = [...p]
          const last = copy[copy.length - 1]
          if (last && last.employee_id === Number(d.employee_id)) {
            copy[copy.length - 1] = { ...last, content: last.content.replace('[正在发言…]', '') + String(d.text) }
          }
          return copy
        }),
        speaker_end: (d) => setCurrent((c) => c ? { ...c, total_tokens_used: Number(d.used) } : c),
        budget_warn: () => setMsg('Token 预算已用 80%'),
        speaker_error: (d) => setMsg(`${d.name} 发言失败：${d.message}`),
        summary: () => open(current.id).catch(() => undefined),
        end: () => { setRunning(false); reload(); },
        error: (d) => { setMsg(String(d.message)); setRunning(false) },
      })
    } catch (e) { setMsg(String(e)); setRunning(false) }
  }

  async function stop() {
    if (!current) return
    await api(`/api/meetings/${current.id}/stop`, { method: 'POST' })
  }

  async function userSpeak() {
    if (!current || !speak.trim()) return
    await api(`/api/meetings/${current.id}/speak`, {
      method: 'POST',
      body: { content: speak.trim(), next_employee_id: nextPick || null },
    })
    setMessages((p) => [...p, { id: Date.now(), role: 'user', content: speak.trim(), employee_name: '我', employee_id: null }])
    setSpeak(''); setNextPick('')
  }

  async function del(id: number) {
    await api(`/api/meetings/${id}`, { method: 'DELETE' })
    if (current?.id === id) { setCurrent(null); setMessages([]) }
    reload()
  }

  const budgetPct = current ? Math.min(100, Math.round((current.total_tokens_used / (current.token_budget || 1)) * 100)) : 0

  return (
    <div>
      <div className="page-head"><h2>AI 会议</h2></div>
      {msg && <div className="banner">{msg}</div>}

      <div className="split-wide">
        <section className="card">
          <h3>发起会议</h3>
          <label>议题</label>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="如：是否开发一款 AI 会议纪要工具" />
          <label>参会员工（至少 2 人）</label>
          <div className="checks">
            {employees.map((e) => (
              <label key={e.id} className="inline">
                <input type="checkbox" checked={picked.includes(e.id)}
                  onChange={(ev) => setPicked(ev.target.checked ? [...picked, e.id] : picked.filter((x) => x !== e.id))} />
                {e.name}
              </label>
            ))}
            {employees.length === 0 && <p className="note">请先在「组织架构」创建员工</p>}
          </div>
          <label>主持人</label>
          <select value={hostId} onChange={(e) => setHostId(e.target.value ? Number(e.target.value) : '')}>
            <option value="">不指定</option>
            {employees.filter((e) => picked.includes(e.id)).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
          <label>调度策略</label>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="round_robin">轮流发言</option>
            <option value="host_pick">主持人指定</option>
            <option value="ai_pick">AI 自主选择下一位</option>
          </select>
          <div className="row">
            <div className="field"><label>轮次上限</label>
              <input type="number" min={1} max={50} value={maxRounds} onChange={(e) => setMaxRounds(Number(e.target.value))} /></div>
            <div className="field"><label>Token 预算</label>
              <input type="number" min={1000} step={1000} value={budget} onChange={(e) => setBudget(Number(e.target.value))} /></div>
          </div>
          <button className="btn primary" onClick={create}><IconPlus /> 创建会议</button>

          <h3 style={{ marginTop: 16 }}>会议列表</h3>
          {meetings.map((m) => (
            <div key={m.id} className={`config-row ${current?.id === m.id ? 'default' : ''}`}>
              <button className="link" onClick={() => open(m.id)}>
                <strong>{m.topic}</strong>
                <div className="note">{m.status === 'ended' ? '已结束' : m.status === 'running' ? '进行中' : m.status === 'stopped' ? '已中断' : '草稿'}
                  · {m.total_tokens_used}/{m.token_budget} tok</div>
              </button>
              <button className="btn small danger ghost" onClick={() => del(m.id)}><IconTrash /></button>
            </div>
          ))}
        </section>

        <section className="card">
          {!current ? (
            <p className="note">选择或创建一场会议</p>
          ) : (
            <>
              <div className="row space">
                <h3>{current.topic}</h3>
                <div className="row">
                  {!running ? (
                    <button className="btn primary" onClick={run}><IconPlay /> 开始会议</button>
                  ) : (
                    <button className="btn danger" onClick={stop}><IconStop /> 打断/停止</button>
                  )}
                </div>
              </div>
              <div className="budget">
                <div className="budget-bar"><div className="budget-fill" style={{ width: `${budgetPct}%` }} /></div>
                <span className={`note ${budgetPct >= 80 ? 'warn' : ''}`}>
                  Token {current.total_tokens_used}/{current.token_budget}（{budgetPct}%）·
                  预估总量 ~{current.cost_estimate.est_tokens}
                </span>
              </div>

              <div className="feed" ref={feedRef}>
                {messages.filter((m) => m.content !== '[已入会]').map((m, i) => (
                  <div key={m.id ?? i} className={`bubble ${m.role === 'user' ? 'user' : ''}`}>
                    <div className="bubble-head">
                      <strong>{m.employee_name}</strong>
                      <span className="note">{m.role === 'user' ? '老板插话' : m.role === 'host' ? '主持人' : '成员'}</span>
                    </div>
                    <div className="bubble-body">{m.content}</div>
                  </div>
                ))}
              </div>

              <div className="row">
                <input value={speak} placeholder="以老板身份插话…"
                  onChange={(e) => setSpeak(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && userSpeak()} />
                <select value={nextPick} onChange={(e) => setNextPick(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">下一位：自动</option>
                  {current.participants?.map((p) => <option key={p.id} value={p.id}>@{p.name}</option>)}
                </select>
                <button className="btn" onClick={userSpeak}><IconSend /></button>
              </div>

              {current.summary && (
                <div className="summary">
                  <h4>会议纪要（已自动入库）</h4>
                  <p>{current.summary}</p>
                  {current.decisions?.length > 0 && (
                    <><h4>决议</h4><ul>{current.decisions.map((d, i) => <li key={i}>{String(d)}</li>)}</ul></>
                  )}
                  {current.todos?.length > 0 && (
                    <><h4>待办</h4><ul>{current.todos.map((t, i) => <li key={i}>{t.task}（{t.owner}）</li>)}</ul></>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}
