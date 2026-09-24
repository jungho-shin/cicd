import { describe, expect, it } from 'vitest'
import { renderToString } from 'react-dom/server'
import App from './App.jsx'

describe('App', () => {
  it('환경과 버전을 표시한다', () => {
    const html = renderToString(<App env="dev" version="develop-1234abcd" />)
    expect(html).toContain('dev')
    expect(html).toContain('develop-1234abcd')
  })
})
