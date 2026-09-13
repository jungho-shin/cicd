import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'

const config = window.APP_CONFIG ?? {}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App
      env={config.env ?? 'unknown'}
      // 빌드 시점에 CI 가 이미지 태그를 넣는다(Dockerfile 의 APP_VERSION)
      version={import.meta.env.VITE_APP_VERSION ?? 'local'}
    />
  </StrictMode>,
)
