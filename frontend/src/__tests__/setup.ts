import '@testing-library/jest-dom'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './mocks/server'

// MSW サーバーをテストスイート全体で起動
beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => {
  server.resetHandlers() // テスト間でハンドラーをリセット
  localStorage.clear()   // localStorage をクリア
})
afterAll(() => server.close())
