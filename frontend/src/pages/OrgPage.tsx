import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { IconPlus, IconTrash, IconUser } from '../icons'

type Employee = {
  id: number; name: string; gender: string; dept_id: number | null
  role_prompt: string; personality_prompt: string
  llm_config_id: number | null; enabled: number
}
type Dept = { id: number; name: string; employees: Employee[] }
type OrgTree = { departments: Dept[]; unassigned: Employee[] }
type LlmConfig = { id: number; provider: string; model_id: string; is_default: number }

export default function OrgPage() {
  const [tree, setTree] = useState<OrgTree>({ departments: [], unassigned: [] })
  const [llmConfigs, setLlmConfigs] = useState<LlmConfig[]>([])
  const [msg, setMsg] = useState('')
  const [deptName, setDeptName] = useState('')
  const [editing, setEditing] = useState<Employee | null>(null)

  const reload = useCallback(async () => {
    const [t, lc] = await Promise.all([
      api<OrgTree>('/api/departments'),
      api<LlmConfig[]>('/api/configs/llm'),
    ])
    setTree(t); setLlmConfigs(lc)
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

  return (
    <div>
      <div className="page-head">
        <h2>组织架构</h2>
        <button className="btn primary" onClick={() => setEditing({ id: 0, name: '', gender: '其他', dept_id: null, role_prompt: '', personality_prompt: '', llm_config_id: null, enabled: 1 })}>
          <IconPlus /> 新增员工
        </button>
      </div>
      {msg && <div className="banner">{msg}</div>}

      <div className="split">
        <section className="card">
          <h3>部门</h3>
          <div className="row">
            <input value={deptName} onChange={(e) => setDeptName(e.target.value)} placeholder="部门名称" />
            <button className="btn" onClick={addDept}><IconPlus /></button>
          </div>
          {tree.departments.map((d) => (
            <div key={d.id} className="dept-row">
              <div>
                <strong>{d.name}</strong>
                <span className="note">（{d.employees.length} 人）</span>
                <ul className="emp-list">
                  {d.employees.map((e) => (
                    <li key={e.id}>
                      <button className="link" onClick={() => setEditing(e)}>
                        <IconUser /> {e.name}
                        <span className="note"> {e.gender} · {llmConfigs.find((c) => c.id === e.llm_config_id)?.model_id ?? '继承默认'}</span>
                        {e.enabled === 0 && <span className="badge">已停用</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              <button className="btn small danger ghost" onClick={() => delDept(d.id)}><IconTrash /></button>
            </div>
          ))}
          {tree.unassigned.length > 0 && (
            <div className="dept-row">
              <div>
                <strong>未分组</strong>
                <ul className="emp-list">
                  {tree.unassigned.map((e) => (
                    <li key={e.id}>
                      <button className="link" onClick={() => setEditing(e)}><IconUser /> {e.name}</button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </section>

        {editing && (
          <EmployeeForm
            emp={editing}
            departments={tree.departments}
            llmConfigs={llmConfigs}
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
  onSave: (e: Partial<Employee>) => Promise<void>
  onDelete?: (id: number) => Promise<void>
  onCancel: () => void
}) {
  const [emp, setEmp] = useState<Employee>({ ...props.emp })
  const set = <K extends keyof Employee>(k: K, v: Employee[K]) => setEmp({ ...emp, [k]: v })

  return (
    <section className="card">
      <h3>{emp.id ? `编辑员工：${emp.name}` : '新增员工'}</h3>
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
      <label className="inline">
        <input type="checkbox" checked={emp.enabled === 1} onChange={(e) => set('enabled', e.target.checked ? 1 : 0)} />
        启用（参与会议调度）
      </label>
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
