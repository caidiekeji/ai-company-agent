import { useState } from 'react'
import { IconKb, IconMeeting, IconModel, IconOrg } from './icons'
import ConfigPage from './pages/ConfigPage'
import KbPage from './pages/KbPage'
import MeetingPage from './pages/MeetingPage'
import OrgPage from './pages/OrgPage'

const TABS = [
  { key: 'org', label: '组织架构', icon: <IconOrg />, page: <OrgPage /> },
  { key: 'config', label: '模型配置', icon: <IconModel />, page: <ConfigPage /> },
  { key: 'meeting', label: 'AI 会议', icon: <IconMeeting />, page: <MeetingPage /> },
  { key: 'kb', label: '知识库', icon: <IconKb />, page: <KbPage /> },
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
