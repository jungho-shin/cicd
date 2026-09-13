import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 테스트는 renderToString 만 쓰므로 브라우저 흉내(jsdom)가 필요 없다
  test: { environment: 'node' },
})
