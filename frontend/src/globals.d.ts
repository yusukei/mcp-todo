declare const __BUILD_TIMESTAMP__: string

interface ImportMetaEnv {
  readonly MODE: string
  readonly DEV: boolean
  readonly PROD: boolean
  // Phase 1.5: opt-in flag for the dev-only design-system preview
  // route. Set to "1" via Docker build arg on staging stacks.
  readonly VITE_DEV_PREVIEW?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
