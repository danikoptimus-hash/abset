import { defineConfig } from 'vitest/config'

// Item B (memory chart redesign) — first frontend unit-test layer in this
// project (everything else is typecheck/lint/build + Playwright e2e, see
// CLAUDE.md's "Тесты" section). Scoped to src/ only: without an explicit
// include/exclude, vitest's default glob also picks up frontend/e2e/*.spec.ts
// (Playwright specs use `test()` from @playwright/test, which vitest's own
// test runner can't execute — every e2e file "fails" with an unrelated
// "did not expect test() to be called here" error otherwise).
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
})
