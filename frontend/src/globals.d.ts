declare const __BUILD_TIMESTAMP__: string

interface ImportMetaEnv {
  readonly MODE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
