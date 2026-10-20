import { useCallback, useEffect, useState } from 'react'
import { api, assetUrl, upload } from '../api'
import { IconPlus, IconTrash, IconUpload, IconUser } from '../icons'

type Employee = {
  id: number; name: string; gender: string; dept_id: number | null
  role_prompt: string; personality_prompt: string
  llm_config_id: number | null; asr_config_id: number | null
  tts_config_id: number | null; tts_voice_id: string | null
  enabled: number; avatar_url: string | null
}
type Dept = { id: number; name: string; employees: Employee[] }
type OrgTree = { departments: Dept[]; unassigned: Employee[] }
type LlmConfig = { id: number; provider: string; model_id: string; is_default: number }
type AsrConfig = { id: number; provider: string; model_id: string }
type TtsConfig = { id: number; provider: string; model_id: string; voice_id: string | null; is_default: number }
type Voice = { id: string; name: string; gender: string }

const empty = (): Employee => ({
  id: 0, name: '', gender: '其他', dept_id: null,
  role_prompt: '', personality_prompt: '',
  llm_config_id: null, asr_config_id: null, tts_config_id: null, tts_voice_id: null,
  enabled: 1, avatar_url: null,
})

export default function OrgPage() {
  const [tree, setTree] = useState<OrgTree>({ departments: [], unassigned: [] })
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([])
  const [asrConfigs, setAsrConfigs] = useState<AsrConfig[]>([])
  const [ttsConfigs, setTtsConfigs] = useState<TtsConfig[]>([])
  const [msg, setMsg] = useState('')
  const [deptName, setDeptName] = useState('')
  const [editing, setEditing] = useState<Employee | null>(null)

  const reload = useCallback(async () => {
    const [t, lc, ac, tc] = await Promise.all([
      api<OrgTree>('/api/departments'),
      api<LlmConfig[]>('/api/configs/llm'),
      api<AsrConfig[]>('/api/configs/asr'),
      api<TtsConfig[]>('/api/configs/tts'),
    ])
    setTree(t); setLlmConfigs(lc); setAsrConfigs(ac); setTtsConfigs(tc)
  }, [])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  async function addDept() {
    if (!deptName.trim()) return
    await api('/api/departments', { method: 'POST', body: { name: deptName.trim() } })
    setDeptName('')
    reload()
  }

  async function delDept(id: number) {
    await api(`/api/departments/${id}`, { method: 'DELETE' })
    reload()
  }

  async function saveEmployee(emp: Partial<Employee>) {
    if (emp.id) {
      await api(`/api/employees/${emp.id}`, { method: 'PUT', body: emp })
    } else {
      await api('/api/employees', { method: 'POST', body: emp })
    }
    setEditing(null)
    reload()
  }

  async function delEmployee(id: number) {
    await api(`/api/employees/${id}`, { method: 'DELETE' })
    setEditing(null)
    reload()
  }

  /** F-ORG-01：拖拽调整部门归属 */
  async function moveTo(eid: number, deptId: number | null) {
    try {
      await api(`/api/employees/${eid}/dept`, { method: 'PUT', body: { dept_id: deptId } })
      reload()
    } catch (e) { setMsg(String(e)) }
  }

  async function toggleEnabled(e: Employee) {
    await api(`/api/employees/${e.id}`, { method: 'PUT', body: { enabled: e.enabled ? 0 : 1 } })
    reload()
  }

  const renderList = (emps: Employee[]) => (
    <ul className="emp-list">
      {emps.map((e) => (
        <li key={e.id} draggable
          onDragStart={(ev) => ev.dataTransfer.setData('text/emp-id', String(e.id))}>
          <div className="emp-row">
            <button className="link" onClick={() => setEditing(e)}>
              {e.avatar_url
                ? <img className="avatar" src={assetUrl(e.avatar_url)} alt="" />
                : <IconUser />}
              {e.name}
              <span className="note"> {e.gender} · {llmConfigs.find((c) => c.id === e.llm_config_id)?.model_id ?? '继承默认'}</span>
              {e.enabled === 0 && <span className="badge warn-badge">已停用</span>}
            </button>
            <button className="btn small ghost" onClick={() => toggleEnabled(e)}>
              {e.enabled ? '停用' : '启用'}
            </button>
          </div>
        </li>
      ))}
    </ul>
  )

  return (
    <div>
      <div className="page-head">
        <h2>组织架构</h2>
        <button className="btn primary" onClick={() => setEditing(empty())}>
          <IconPlus /> 新增员工
        </button>
      </div>
      {msg && <div className="banner">{msg}</div>}
      <p className="note">拖拽员工行可调整部门归属（F-ORG-01）；停用的员工不参与会议调度（F-ORG-08）。</p>

      <div className="split">
        <section className="card">
          <h3>部门</h3>
          <div className="row">
            <input value={deptName} onChange={(e) => setDeptName(e.target.value)}
              placeholder="部门名称" onKeyDown={(e) => e.key === 'Enter' && addDept()} />
            <button className="btn" onClick={addDept}><IconPlus /></button>
          </div>
          {tree.departments.map((d) => (
            <div key={d.id} className="dept-row"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                const id = Number(e.dataTransfer.getData('text/emp-id'))
                if (id) moveTo(id, d.id)
              }}>
              <div>
                <strong>{d.name}</strong>
                <span className="note">（{d.employees.length} 人）</span>
                {renderList(d.employees)}
              </div>
              <button className="btn small danger ghost" onClick={() => delDept(d.id)}><IconTrash /></button>
            </div>
          ))}
          {tree.unassigned.length > 0 && (
            <div className="dept-row"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                const id = Number(e.dataTransfer.getData('text/emp-id'))
                if (id) moveTo(id, null)
              }}>
              <div>
                <strong>未分组</strong>
                {renderList(tree.unassigned)}
              </div>
            </div>
          )}
        </section>

        {editing && (
          <EmployeeForm
            key={editing.id || 'new'}
            emp={editing}
            departments={tree.departments}
            llmConfigs={llmConfigs}
            asrConfigs={asrConfigs}
            ttsConfigs={ttsConfigs}
            onSave={saveEmployee}
            onDelete={editing.id ? delEmployee : undefined}
            onCancel={() => setEditing(null)}
          />
        )}
      </div>
    </div>
  )
}

function EmployeeForm(props: {
  emp: Employee
  departments: Dept[]
  llmConfigs: LlmConfig[]
  asrConfigs: AsrConfig[]
  ttsConfigs: TtsConfig[]
  onSave: (e: Partial<Employee>) => Promise<void>
  onDelete?: (id: number) => Promise<void>
  onCancel: () => void
}) {
  const [emp, setEmp] = useState<Employee>({ ...props.emp })
  const [preview, setPreview] = useState('')
  const [voices, setVoices] = useState<Voice[]>([])
  const set = <K extends keyof Employee>(k: K, v: Employee[K]) => setEmp({ ...emp, [k]: v })

  const tts = props.ttsConfigs.find((c) => c.id === emp.tts_config_id)

  useEffect(() => {
    const provider = tts?.provider ?? props.ttsConfigs.find((c) => c.is_default)?.provider
    if (!provider) { setVoices([]); return }
    api<{ voices: Voice[] }>(`/api/voice/voices?provider=${encodeURIComponent(provider)}`)
      .then((r) => setVoices(r.voices)).catch(() => setVoices([]))
  }, [tts?.provider, props.ttsConfigs])

  // 实时预览 system prompt 拼接结果（PRD 交互要求）
  useEffect(() => {
    if (!emp.id) {
      setPreview(`你是一场多 Agent 协作会议中的「${emp.name || '（未命名）'}」（岗位角色）。\n` +
        `议题：{议题}\n岗位职责：${emp.role_prompt || '（未填写）'}\n性格：${emp.personality_prompt || '（未填写）'}`)
      return
    }
    api<{ system_prompt: string }>(`/api/employees/${emp.id}/prompt-preview?topic=议题`)
      .then((r) => setPreview(r.system_prompt)).catch(() => setPreview(''))
  }, [emp.id, emp.name, emp.role_prompt, emp.personality_prompt])

  async function pickAvatar(file: File | undefined) {
    if (!file || !emp.id) return
    const form = new FormData()
    form.append('file', file)
    try {
      await upload(`/api/employees/${emp.id}/avatar`, form)
      setEmp({ ...emp, avatar_url: `/api/employees/${emp.id}/avatar` })
    } catch (e) { /* 忽略 */ }
  }

  return (
    <section className="card">
      <h3>{emp.id ? `编辑员工：${emp.name}` : '新增员工'}</h3>
      <div className="row">
        {emp.avatar_url && <img className="avatar lg" src={assetUrl(emp.avatar_url)} alt="" />}
        <label className="btn small" style={{ margin: 0 }}>
          <IconUpload /> {emp.avatar_url ? '更换头像' : '上传头像'}
          <input type="file" accept="image/*" style={{ display: 'none' }}
            onChange={(e) => pickAvatar(e.target.files?.[0])} />
        </label>
        {emp.id && <span className="note">保存后可上传头像</span>}
      </div>

      <label>姓名 *</label>
      <input value={emp.name} onChange={(e) => set('name', e.target.value)} />

      <label>性别（用于 TTS 默认音色初始映射）</label>
      <select value={emp.gender} onChange={(e) => set('gender', e.target.value)}>
        <option value="男">男</option><option value="女">女</option><option value="其他">其他</option>
      </select>

      <label>所属部门</label>
      <select value={emp.dept_id ?? ''} onChange={(e) => set('dept_id', e.target.value ? Number(e.target.value) : null)}>
        <option value="">未分组</option>
        {props.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
      </select>

      <label>岗位职责（system prompt，支持 {'{{meeting_topic}}'}）*</label>
      <textarea rows={4} value={emp.role_prompt} onChange={(e) => set('role_prompt', e.target.value)}
        placeholder="如：你是产品经理，结论先行，关注用户价值与成本。" />

      <label>性格</label>
      <textarea rows={2} value={emp.personality_prompt} onChange={(e) => set('personality_prompt', e.target.value)}
        placeholder="如：务实、简洁、敢于反对。" />

      <label>LLM 绑定</label>
      <select value={emp.llm_config_id ?? ''} onChange={(e) => set('llm_config_id', e.target.value ? Number(e.target.value) : null)}>
        <option value="">继承全局默认</option>
        {props.llmConfigs.map((c) => (
          <option key={c.id} value={c.id}>{c.provider} / {c.model_id}{c.is_default ? '（默认）' : ''}</option>
        ))}
      </select>

      <label>ASR 引擎（F-ORG-07，留空继承全局）</label>
      <select value={emp.asr_config_id ?? ''} onChange={(e) => set('asr_config_id', e.target.value ? Number(e.target.value) : null)}>
        <option value="">继承全局默认</option>
        {props.asrConfigs.map((c) => <option key={c.id} value={c.id}>{c.provider} / {c.model_id}</option>)}
      </select>

      <label>TTS 引擎（F-ORG-07，留空继承全局）</label>
      <select value={emp.tts_config_id ?? ''} onChange={(e) => set('tts_config_id', e.target.value ? Number(e.target.value) : null)}>
        <option value="">继承全局默认</option>
        {props.ttsConfigs.map((c) => <option key={c.id} value={c.id}>{c.provider} / {c.model_id}</option>)}
      </select>

      <label>音色（留空则按性别自动映射）</label>
      <select value={emp.tts_voice_id ?? ''} onChange={(e) => set('tts_voice_id', e.target.value || null)}>
        <option value="">按性别自动映射</option>
        {voices.map((v) => <option key={v.id} value={v.id}>{v.name}（{v.gender}）</option>)}
      </select>

      <label className="inline">
        <input type="checkbox" checked={emp.enabled === 1} onChange={(e) => set('enabled', e.target.checked ? 1 : 0)} />
        启用（参与会议调度）
      </label>

      {preview && (
        <>
          <h4>System Prompt 预览</h4>
          <pre className="prompt-preview">{preview}</pre>
        </>
      )}

      <div className="row">
        <button className="btn primary" onClick={() => props.onSave(emp).catch(() => undefined)}>保存</button>
        <button className="btn ghost" onClick={props.onCancel}>取消</button>
        {props.onDelete && (
          <button className="btn danger ghost" onClick={() => props.onDelete?.(emp.id)}><IconTrash /></button>
        )}
      </div>
    </section>
  )
}
