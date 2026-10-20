import { useCallback, useEffect, useRef, useState } from 'react'
import { api, upload } from '../api'
import { IconMic, IconPlay, IconStop, IconVolume } from '../icons'

type AsrConfig = { id: number; provider: string; model_id: string; engine_type: string; is_default: number }
type TtsConfig = { id: number; provider: string; model_id: string; voice_id: string; engine_type: string; is_default: number }
type Employee = {
  id: number; name: string; gender: string
  asr_config_id: number | null; tts_config_id: number | null; tts_voice_id: string | null
}
type Voice = { id: string; name: string; gender: string }
type EmpVoice = {
  asr: AsrConfig | null; asr_source: string
  tts: TtsConfig | null; tts_source: string
  voice_id: string | null; voice_is_manual: boolean; available_voices: Voice[]
}

export default function VoicePage() {
  const [asrConfigs, setAsrConfigs] = useState<AsrConfig[]>([])
  const [ttsConfigs, setTtsConfigs] = useState<TtsConfig[]>([])
  const [employees, setEmployees] = useState<Employee[]>([])
  const [selected, setSelected] = useState<Employee | null>(null)
  const [empVoice, setEmpVoice] = useState<EmpVoice | null>(null)
  const [msg, setMsg] = useState('')
  const [recording, setRecording] = useState(false)
  const [preview, setPreview] = useState('')
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const mediaRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const reload = useCallback(async () => {
    const [a, t, e] = await Promise.all([
      api<AsrConfig[]>('/api/configs/asr'),
      api<TtsConfig[]>('/api/configs/tts'),
      api<Employee[]>('/api/employees'),
    ])
    setAsrConfigs(a); setTtsConfigs(t); setEmployees(e)
    if (!selected && e.length) setSelected(e[0])
  }, [selected])

  useEffect(() => { reload().catch((e) => setMsg(String(e))) }, [reload])

  useEffect(() => {
    if (!selected) { setEmpVoice(null); return }
    api<EmpVoice>(`/api/voice/employee/${selected.id}`).then(setEmpVoice).catch(() => setEmpVoice(null))
  }, [selected])

  /** 语音输入：MediaRecorder 录音 → 上传 ASR → 转写填入 */
  async function toggleRecord() {
    if (recording) {
      mediaRef.current?.stop()
      return
    }
    const asr = empVoice?.asr
    if (!asr) { setMsg('未配置 ASR，请先在「模型配置」中配置并设为默认'); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRecording(false)
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        const form = new FormData()
        form.append('file', blob, 'audio.webm')
        try {
          const r = await upload<{ text: string }>(`/api/voice/asr/transcribe?config_id=${asr.id}`, form)
          setPreview(r.text)
          setMsg(`识别成功：${r.text.slice(0, 60)}`)
        } catch (e) { setMsg(String(e)) }
      }
      mediaRef.current = mr
      mr.start()
      setRecording(true)
    } catch {
      setMsg('无法访问麦克风（浏览器权限或非安全上下文）')
    }
  }

  /** TTS 试听 */
  async function previewVoice(text: string) {
    const tts = empVoice?.tts
    if (!tts) { setMsg('未配置 TTS'); return }
    try {
      const r = await api<{ url: string }>('/api/voice/tts/synthesize', {
        method: 'POST',
        body: { config_id: tts.id, text, employee_id: selected?.id },
      })
      audioRef.current?.pause()
      const audio = new Audio(r.url)
      audioRef.current = audio
      await audio.play()
    } catch (e) { setMsg(String(e)) }
  }

  async function updateEmployee(patch: Partial<Employee>) {
    if (!selected) return
    try {
      await api(`/api/employees/${selected.id}`, { method: 'PUT', body: patch })
      setMsg('已保存')
      reload()
    } catch (e) { setMsg(String(e)) }
  }

  return (
    <div>
      <div className="page-head">
        <h2>语音交互</h2>
        <span className="note">ASR 语音输入 · TTS 音色播放 · 员工级覆盖</span>
      </div>
      {msg && <div className="banner">{msg}</div>}

      <div className="split-wide">
        <section className="card">
          <h3>员工</h3>
          {employees.length === 0 && <p className="note">请先在「组织架构」创建 AI 员工。</p>}
          {employees.map((e) => (
            <button key={e.id} className={`link list-btn ${selected?.id === e.id ? 'active' : ''}`}
              onClick={() => setSelected(e)}>
              <IconVolume /> {e.name}
              <span className="note"> {e.gender}</span>
            </button>
          ))}
        </section>

        <div>
          <section className="card">
            <h3>语音输入转文字（F-VOI-01）</h3>
            <div className="row">
              <button className={`btn ${recording ? 'danger' : 'primary'}`} onClick={toggleRecord}>
                {recording ? <IconStop /> : <IconMic />} {recording ? '停止录音' : '开始录音'}
              </button>
              <span className="note">
                当前 ASR：{empVoice?.asr ? `${empVoice.asr.provider} / ${empVoice.asr.model_id}` : '未配置'}
                （{empVoice?.asr_source ?? '-'}）
              </span>
            </div>
            {preview && (
              <div className="bubble-body" style={{ marginTop: 8 }}>{preview}</div>
            )}
          </section>

          <section className="card">
            <h3>员工语音配置（F-ORG-07）</h3>
            {!selected && <p className="note">请选择左侧员工。</p>}
            {selected && empVoice && (
              <>
                <label>ASR 引擎</label>
                <select value={selected.asr_config_id ?? ''}
                  onChange={(e) => updateEmployee({ asr_config_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">继承全局默认</option>
                  {asrConfigs.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.provider} / {c.model_id}{c.is_default ? '（全局默认）' : ''}
                    </option>
                  ))}
                </select>

                <label>TTS 引擎</label>
                <select value={selected.tts_config_id ?? ''}
                  onChange={(e) => updateEmployee({ tts_config_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">继承全局默认</option>
                  {ttsConfigs.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.provider} / {c.model_id}{c.is_default ? '（全局默认）' : ''}
                    </option>
                  ))}
                </select>

                <label>
                  音色（{empVoice.voice_is_manual ? '手动指定' : `按性别自动映射：${selected.gender}`}）
                </label>
                <select value={selected.tts_voice_id ?? ''}
                  onChange={(e) => updateEmployee({ tts_voice_id: e.target.value || null })}>
                  <option value="">自动（性别映射 / 配置默认）</option>
                  {empVoice.available_voices.map((v) => (
                    <option key={v.id} value={v.id}>{v.name}（{v.gender}）</option>
                  ))}
                </select>
                <p className="note">
                  当前生效音色：<strong>{empVoice.voice_id ?? '未确定'}</strong>
                  {empVoice.tts_source && ` · TTS 来源：${empVoice.tts_source}`}
                </p>

                <div className="row">
                  <button className="btn" onClick={() => previewVoice('这是智企 Agent 的语音试听，你好。')}>
                    <IconPlay /> 试听
                  </button>
                </div>
              </>
            )}
          </section>

          <section className="card">
            <h3>引擎可用性</h3>
            <p className="note">
              本地引擎需先在「系统设置 → 模型包管理」下载对应模型包；本地模型不可用时可在
              「模型配置」中切换到已配置的云厂商 ASR/TTS（F-VOI-07）。
            </p>
            <table className="kv">
              <tbody>
                <tr><td>ASR 配置数</td><td>{asrConfigs.length}</td></tr>
                <tr><td>TTS 配置数</td><td>{ttsConfigs.length}</td></tr>
                <tr><td>默认 ASR</td><td>{asrConfigs.find((c) => c.is_default)?.provider ?? '未设置'}</td></tr>
                <tr><td>默认 TTS</td><td>{ttsConfigs.find((c) => c.is_default)?.provider ?? '未设置'}</td></tr>
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  )
}
