import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import { ErrorBoundary } from './components/shared/ErrorBoundary'
import './style.css'
import './styles/foundation-v5.css'
import './styles/layout-v5.css'
import './styles/page-shell-v5.css'
import './styles/modules/global-polish-v7.css'
import './styles/modules/dashboard-v6.css'
import './styles/modules/evaluation-v6.css'
import './styles/modules/knowledge-base-v6.css'
import './styles/modules/api-testing-v6.css'
import './styles/modules/workspace-v8.css'
import './styles/modules/workspace-v9.css'
import './styles/modules/workspace-v10.css'
import './styles/modules/workspace-v11.css'
import './styles/modules/workspace-v12.css'
import './styles/modules/workspace-v13.css'
import './styles/modules/agent-platform-v1.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
