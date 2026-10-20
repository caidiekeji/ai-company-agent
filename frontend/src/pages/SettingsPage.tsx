import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { IconDownload, IconPlus, IconRefresh, IconShield, IconTrash } from '../icons'

type Settings = Record<string, string>
type Whitelist = { id: number; domain: string; note: string }
type Package = {
  id: string; category: string; display_name: string; version: string
  size_bytes: number; installed: number; download_state: string; progress: number
  license: string; note: string; error: string | null
}
type Privacy = Record<string, string[]>
type Usage = { database_bytes: number; models_bytes: number; logs_bytes: number; total_bytes: number; data_dir: string }

const fmtSize = (n: number) => {
  if (!n) return '0 B'
  if (n > 1073741824) return `${(n / 1073741824).toFixed(2)} GB`
  if (n > 1048576) return `${(n / 1048576).toFixed(1)} MB`
  if (n > 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings>({})
  const [whitelist, setWhitelist] = useState<Whitelist[]>([])
  const [packages, setPackages] = useState<Package[]>([])
  const [privacy, setPrivacy] = useState<Privacy>({})
  const [usage, setUsage] = useState<Usage | null>(null)
  const [domain, setDomain] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    const [s, p, priv, u] = await Promise.all([
      api<{ settings: Settings; whitelist: Whitelist[]; privacy: Privacy; usage: Usage }>('/api/settings'),
      api<Package[]>('/api/settings/packages'),
      api<Privacy>('/api/settings/privacy'),
      api<Usage>('/api/settings/usage'),
    ])
    setSettings(s.settings)
    setWhitelist(s.whitelist)
    setPrivacy(priv)
    setUsage(u)
    setPackages(p)
  }, [])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  async function save(key: string, value: string | boolean) {
    await api('/api/settings', { method: 'PUT', body: { settings: { [key]: value } } })
    setSettings((s) => ({ ...s, [key]: String(value) }))
  }

  async function addDomain() {
    if (!domain.trim()) return
    try {
      await api('/api/settings/whitelist', { method: 'POST', body: { domain: domain.trim() } })
      setDomain('')
      reload()
    } catch (e) { setMsg(String(e)) }
  }

  async function delDomain(id: number) {
    await api(`/api/settings/whitelist/${id}`, { method: 'DELETE' })
    reload()
  }

  async function download(pkg: Package) {
    setBusy(true)
    try {
      const r = await api<{ ok: boolean; message: string }>(`/api/settings/packages/${pkg.id}/download`, { method: 'POST' })
      setMsg(r.message)
      setTimeout(reload, 1500)
    } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }

  async function removePkg(id: string) {
    await api(`/api/settings/packages/${id}`, { method: 'DELETE' })
    reload()
  }

  async function clearCache(target: 'logs' | 'models' | 'vectors') {
    const r = await api<{ freed_bytes: number }>('/api/settings/clear-cache', { method: 'POST', body: { target } })
    setMsg(`已清理，释放 ${fmtSize(r.freed_bytes)}`)
    reload()
  }

  async function doExport() {
    const r = await api<{ filename: string; path: string }>('/api/settings/export', { method: 'POST' })
    setMsg(`已导出：${r.filename}（${r.path}）`)
  }

  async function checkUpdate() {
    const r = await api<{ update_available: boolean; latest_version: string; message: string }>(
      '/api/settings/update/check', { method: 'POST', body: {} })
    setMsg(r.update_available ? `发现新版本 ${r.latest_version}` : (r.message || '已是最新版本'))
  }

  const toggleRow = (key: string, label: string, hint: string) => (
    <label className="inline switch-row" key={key}>
      <input
        type="checkbox"
        checked={settings[key] === 'true'}
        onChange={(e) => save(key, e.target.checked)}
      />
      <span>
        <strong>{label}</strong>
        <span className="note">{hint}</span>
      </span>
    </label>
  )

  return (
    <div>
      <div className="page-head">
        <h2>系统设置</h2>
        <div className="row">
          <button className="btn" onClick={() => reload()}><IconRefresh /> 刷新</button>
          <button className="btn" onClick={doExport}><IconDownload /> 导出备份</button>
        </div>
      </div>
      {msg && <div className="banner">{msg}</div>}

      <div className="split-wide">
        <div>
          <section className="card">
            <h3><IconShield /> RPA 安全设置</h3>
            {toggleRow('rpa_high_risk_confirm', '高风险动作二次确认', '提交表单 / 支付类按钮需手动确认（F-RPA-07）')}
            {toggleRow('rpa_allow_non_whitelist', '允许非白名单域名', '关闭时非白名单站点直接拦截（F-RPA-06）')}
            {toggleRow('rpa_headless', '无头模式运行浏览器', '关闭后可看到浏览器窗口（人工接管更方便）')}
            <h4>域名白名单</h4>
            <div className="row">
              <input value={domain} onChange={(e) => setDomain(e.target.value)}
                placeholder="example.com" onKeyDown={(e) => e.key === 'Enter' && addDomain()} />
              <button className="btn" onClick={addDomain}><IconPlus /> 添加</button>
            </div>
            {whitelist.length === 0 && <p className="note warn">白名单为空，RPA 将拒绝所有站点访问。</p>}
            {whitelist.map((w) => (
              <div key={w.id} className="config-row">
                <span>
                  <strong>{w.domain}</strong>
                  {w.note && <span className="note"> · {w.note}</span>}
                </span>
                <button className="btn small danger ghost" onClick={() => delDomain(w.id)}><IconTrash /></button>
              </div>
            ))}
          </section>

          <section className="card">
            <h3>语音与检索</h3>
            {toggleRow('voice_auto_play', '自动播放员工发言', '按员工音色播放 TTS（F-VOI-03）')}
            {toggleRow('context_compress_default', '会议上下文压缩', '对历史发言做摘要压缩，降低 token 消耗（F-MTG-10）')}
            <label>嵌入模型（语义检索）</label>
            <select value={settings.embedding_model ?? 'bge-small-zh-v1.5'}
              onChange={(e) => save('embedding_model', e.target.value)}>
              <option value="bge-small-zh-v1.5">bge-small-zh-v1.5（512 维，默认）</option>
              <option value="bge-m3">bge-m3（1024 维，多语言）</option>
            </select>
            <label>LLM 价格（元 / 1K token，用于成本预估）</label>
            <input value={settings.llm_price_per_1k ?? '0.002'}
              onChange={(e) => save('llm_price_per_1k', e.target.value)} />
          </section>

          <section className="card">
            <h3>更新</h3>
            {toggleRow('auto_update_enabled', '自动检查更新', '静默升级（签名包，F-SYS-07）')}
            <label>更新源（manifest JSON 地址）</label>
            <input value={settings.update_manifest_url ?? ''}
              placeholder="https://github.com/.../latest.json"
              onChange={(e) => save('update_manifest_url', e.target.value)} />
            <button className="btn" onClick={checkUpdate}>检查更新</button>
          </section>
        </div>

        <div>
          <section className="card">
            <h3>模型包管理</h3>
            <p className="note">语音 / 嵌入模型与 RPA 浏览器内核按需下载，不随安装包分发（F-MOD-04）。</p>
            {packages.map((p) => (
              <div key={p.id} className="config-row">
                <div style={{ flex: 1 }}>
                  <strong>{p.display_name}</strong>
                  <div className="note">
                    {p.category} · {fmtSize(p.size_bytes)} · {p.license}
                  </div>
                  {p.note && <div className="note">{p.note}</div>}
                  {p.download_state === 'downloading' && (
                    <div className="budget-bar" style={{ marginTop: 4 }}>
                      <div className="budget-fill" style={{ width: `${Math.round(p.progress * 100)}%` }} />
                    </div>
                  )}
                  {p.error && <div className="note warn">{p.error}</div>}
                </div>
                <div className="row" style={{ margin: 0 }}>
                  {p.installed ? (
                    <button className="btn small danger ghost" onClick={() => removePkg(p.id)}><IconTrash /></button>
                  ) : (
                    <button className="btn small" disabled={busy} onClick={() => download(p)}>
                      <IconDownload /> 下载
                    </button>
                  )}
                </div>
              </div>
            ))}
          </section>

          <section className="card">
            <h3>存储与缓存</h3>
            {usage && (
              <table className="kv">
                <tbody>
                  <tr><td>数据库</td><td>{fmtSize(usage.database_bytes)}</td></tr>
                  <tr><td>模型目录</td><td>{fmtSize(usage.models_bytes)}</td></tr>
                  <tr><td>日志</td><td>{fmtSize(usage.logs_bytes)}</td></tr>
                  <tr><td><strong>合计</strong></td><td><strong>{fmtSize(usage.total_bytes)}</strong></td></tr>
                </tbody>
              </table>
            )}
            {usage && <p className="note mono">数据目录：{usage.data_dir}</p>}
            <div className="row">
              <button className="btn small" onClick={() => clearCache('logs')}>清日志</button>
              <button className="btn small" onClick={() => clearCache('vectors')}>清向量索引</button>
              <button className="btn small danger" onClick={() => clearCache('models')}>清模型目录</button>
            </div>
          </section>

          <section className="card">
            <h3>隐私说明（F-SYS-05）</h3>
            {Object.entries(privacy).map(([k, lines]) => (
              <div key={k}>
                <h4>{k}</h4>
                <ul className="privacy-list">
                  {lines.map((l, i) => <li key={i}>{l}</li>)}
                </ul>
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  )
}
