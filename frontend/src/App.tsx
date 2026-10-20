import { useState } from 'react'
import { IconBrowser, IconKb, IconMeeting, IconModel, IconOrg, IconSettings, IconVoice } from './icons'
import ConfigPage from './pages/ConfigPage'
import KbPage from './pages/KbPage'
import MeetingPage from './pages/MeetingPage'
import OrgPage from './pages/OrgPage'
import RpaPage from './pages/RpaPage'
import SettingsPage from './pages/SettingsPage'
import VoicePage from './pages/VoicePage'

const TABS = [
  { key: 'org', label: '组织架构', icon: <IconOrg />, page: <OrgPage /> },
  { key: 'config', label: '模型配置', icon: <IconModel />, page: <ConfigPage /> },
  { key: 'meeting', label: 'AI 会议', icon: <IconMeeting />, page: <MeetingPage /> },
  { key: 'voice', label: '语音交互', icon: <IconVoice />, page: <VoicePage /> },
  { key: 'rpa', label: '浏览器 RPA', icon: <IconBrowser />, page: <RpaPage /> },
  { key: 'kb', label: '知识库', icon: <IconKb />, page: <KbPage /> },
  { key: 'settings', label: '系统设置', icon: <IconSettings />, page: <SettingsPage /> },
] as const

function App() {
  const [tab, setTab] = useState<string>('config')
  const active = TABS.find((t) => t.key === tab) ?? TABS[0]

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">AI</span>
          <span className="brand-name">智企 Agent</span>
        </div>
        <nav>
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`nav-item ${tab === t.key ? 'active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">本地数据 · SQLite</div>
      </aside>
      <main className="content">{active.page}</main>
    </div>
  )
}

export default App
