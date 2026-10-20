import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { IconCheck, IconPlus, IconTrash } from '../icons'

type Kind = 'llm' | 'asr' | 'tts'
type Vendor = { id: string; name: string; base_url?: string; local?: boolean }
type Credential = { id: number; provider: string; alias: string; base_url?: string; api_key_masked: string }
type Config = {
  id: number; provider: string; model_id?: string; voice_id?: string
  is_default: number; engine_type?: string; credential_label?: string
}

const KIND_LABEL: Record<Kind, string> = { llm: 'LLM 大模型', asr: '语音识别 ASR', tts: '语音合成 TTS' }

export default function ConfigPage() {
  const [kind, setKind] = useState<Kind>('llm')
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [creds, setCreds] = useState<Credential[]>([])
  const [configs, setConfigs] = useState<Config[]>([])
  const [msg, setMsg] = useState('')

  const [vendorId, setVendorId] = useState('')
  const [credSel, setCredSel] = useState<string>('')        // '' | '__new__' | '<id>'
  const [modelId, setModelId] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([])
  const [note, setNote] = useState('')
  const [setDefault, setSetDefault] = useState(false)
  const [alias, setAlias] = useState('默认')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')

  const vendor = vendors.find((v) => v.id === vendorId)
  const isLocal = !!vendor?.local

  const reload = useCallback(async () => {
    const [vs, cs, cfgs] = await Promise.all([
      api<Vendor[]>(`/api/vendors/${kind}`),
      api<Credential[]>('/api/credentials'),
      api<Config[]>(`/api/configs/${kind}`),
    ])
    setVendors(vs); setCreds(cs); setConfigs(cfgs)
    setVendorId(''); setCredSel(''); setModelId(''); setVoiceId('')
    setModels([]); setVoices([]); setNote(''); setApiKey(''); setSetDefault(false)
  }, [kind])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  const vendorCreds = creds.filter((c) => c.provider === vendorId)

  function pickVendor(id: string) {
    const v = vendors.find((x) => x.id === id)
    setVendorId(id)
    setBaseUrl(v?.base_url ?? '')
    setAlias('默认')
    setCredSel('')
    setModels([]); setVoices([]); setNote(''); setModelId(''); setVoiceId('')
    if (v && !v.local) setCredSel(vendorCreds.length ? '' : '__new__')
  }

  async function fetchModels() {
    setNote('拉取中…')
    try {
      const r = await api<{ models: string[]; source: string; note?: string }>(
        `/api/providers/${kind}/models`,
        { method: 'POST', body: { provider: vendorId, credential_id: credSel !== '__new__' && credSel ? Number(credSel) : null } })
      setModels(r.models)
      setNote(r.note || `模型列表来源：${r.source === 'api' ? '厂商接口' : r.source === 'builtin' ? '内置清单' : '手动填写'}`)
    } catch (e) { setNote(String(e)) }
  }

  async function fetchVoices() {
    const r = await api<{ voices: { id: string; name: string }[] }>(
      '/api/providers/tts/voices', { method: 'POST', body: { provider: vendorId } })
    setVoices(r.voices)
  }

  async function save() {
    let cid: number | null = credSel !== '__new__' && credSel ? Number(credSel) : null
    if (credSel === '__new__') {
      if (!apiKey) { setMsg('请填写 API Key'); return }
      const c = await api<Credential>('/api/credentials', {
        method: 'POST',
        body: { provider: vendorId, alias: alias || '默认', base_url: baseUrl, api_key: apiKey },
      })
      cid = c.id
    }
    await api(`/api/configs/${kind}`, {
      method: 'POST',
      body: { provider: vendorId, credential_id: cid, model_id: modelId, voice_id: voiceId || null, set_default: setDefault },
    })
    setMsg('已保存')
    reload()
  }

  async function testConfig(id: number) {
    setMsg('测试中…')
    try {
      const r = await api<{ ok: boolean; message: string }>(`/api/configs/${kind}/${id}/test`, { method: 'POST' })
      setMsg(`${r.ok ? '通过' : '失败'}：${r.message}`)
    } catch (e) { setMsg(String(e)) }
  }

  async function setDefaultCfg(id: number) {
    await api(`/api/configs/${kind}/${id}`, { method: 'PUT', body: { set_default: true } })
    reload()
  }

  async function del(id: number) {
    try { await api(`/api/configs/${kind}/${id}`, { method: 'DELETE' }); reload() }
    catch (e) { setMsg(String(e)) }
  }

  return (
    <div>
      <div className="page-head">
        <h2>模型配置中心</h2>
        <div className="tabs">
          {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
            <button key={k} className={`tab ${kind === k ? 'active' : ''}`} onClick={() => setKind(k)}>
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>
      </div>
      {msg && <div className={`banner ${msg.startsWith('通过') || msg === '已保存' ? 'ok' : ''}`}>{msg}</div>}

      <div className="split">
        <section className="card">
          <h3>新增{KIND_LABEL[kind]}配置</h3>
          <label>厂商</label>
          <select value={vendorId} onChange={(e) => pickVendor(e.target.value)}>
            <option value="">请选择厂商</option>
            {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}{v.local ? '（本地）' : ''}</option>)}
          </select>

          {vendorId && !isLocal && (
            <>
              <label>凭证（同一厂商 Key 只填一次）</label>
              <select value={credSel} onChange={(e) => setCredSel(e.target.value)}>
                {vendorCreds.length > 0 && <option value="">— 选择已有凭证 —</option>}
                {vendorCreds.map((c) => <option key={c.id} value={String(c.id)}>{c.alias} · {c.api_key_masked}</option>)}
                <option value="__new__">+ 新建凭证</option>
              </select>
            </>
          )}

          {credSel === '__new__' && (
            <div className="cred-form">
              <label>别名（多账号用别名区分）</label>
              <input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="默认 / 公司 / 个人" />
              <label>Base URL</label>
              <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.example.com/v1" />
              <label>API Key</label>
              <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="加密存储，不明文落库" />
            </div>
          )}

          {vendorId && (
            <>
              <div className="row">
                <button className="btn" onClick={fetchModels}>获取模型</button>
                {kind === 'tts' && <button className="btn ghost" onClick={fetchVoices}>获取音色</button>}
              </div>
              {models.length > 0 ? (
                <>
                  <label>模型</label>
                  <select value={modelId} onChange={(e) => setModelId(e.target.value)}>
                    <option value="">请选择模型</option>
                    {models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </>
              ) : (
                <>
                  <label>模型 ID（可手动填写兜底）</label>
                  <input value={modelId} onChange={(e) => setModelId(e.target.value)} placeholder="如 deepseek-chat" />
                </>
              )}
              {kind === 'tts' && (voices.length > 0 ? (
                <>
                  <label>音色</label>
                  <select value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
                    <option value="">请选择音色</option>
                    {voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                  </select>
                </>
              ) : (
                <>
                  <label>音色 ID（可手动填写）</label>
                  <input value={voiceId} onChange={(e) => setVoiceId(e.target.value)} />
                </>
              ))}
              {note && <p className="note">{note}</p>}
              <label className="inline">
                <input type="checkbox" checked={setDefault} onChange={(e) => setSetDefault(e.target.checked)} />
                设为全局默认
              </label>
              <div className="row">
                <button className="btn primary" disabled={!modelId} onClick={() => save().catch((e) => setMsg(String(e)))}>
                  <IconPlus /> 保存配置
                </button>
              </div>
            </>
          )}
        </section>

        <section className="card">
          <h3>已保存配置</h3>
          {configs.length === 0 && <p className="note">暂无配置</p>}
          {configs.map((c) => (
            <div key={c.id} className={`config-row ${c.is_default ? 'default' : ''}`}>
              <div>
                <strong>{c.provider}</strong>
                <span className="mono"> {c.model_id}{c.voice_id ? ` / ${c.voice_id}` : ''}</span>
                {c.is_default === 1 && <span className="badge">默认</span>}
                <div className="note">{c.credential_label ?? c.engine_type ?? '—'}</div>
              </div>
              <div className="row">
                {c.is_default !== 1 && (
                  <button className="btn small ghost" title="设为默认" onClick={() => setDefaultCfg(c.id)}><IconCheck /></button>
                )}
                <button className="btn small" onClick={() => testConfig(c.id)}>测试</button>
                <button className="btn small danger ghost" title="删除" onClick={() => del(c.id)}><IconTrash /></button>
              </div>
            </div>
          ))}
          <h3 style={{ marginTop: 16 }}>凭证（按厂商去重共用）</h3>
          {creds.length === 0 && <p className="note">暂无凭证</p>}
          {creds.map((c) => (
            <div key={c.id} className="config-row">
              <div>
                <strong>{c.provider}</strong> · {c.alias}
                <div className="note mono">{c.base_url || '—'}</div>
              </div>
              <span className="mono note">{c.api_key_masked}</span>
            </div>
          ))}
        </section>
      </div>
    </div>
  )
}
