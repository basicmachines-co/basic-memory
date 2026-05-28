export type LoggerBackend = {
  info(msg: string, ...args: unknown[]): void
  warn(msg: string, ...args: unknown[]): void
  error(msg: string, ...args: unknown[]): void
  debug?(msg: string, ...args: unknown[]): void
}

const NOOP_LOGGER: LoggerBackend = {
  info() {},
  warn() {},
  error() {},
  debug() {},
}

let _backend: LoggerBackend = NOOP_LOGGER
let _debug = false

export function initLogger(backend: LoggerBackend, debug: boolean): void {
  _backend = backend
  _debug = debug
}

export const log = {
  info(msg: string, ...args: unknown[]): void {
    _backend.info(`basic-memory: ${msg}`, ...args)
  },

  warn(msg: string, ...args: unknown[]): void {
    _backend.warn(`basic-memory: ${msg}`, ...args)
  },

  error(msg: string, err?: unknown): void {
    const detail =
      err instanceof Error
        ? err.message
        : err !== undefined && err !== ""
          ? String(err)
          : ""
    _backend.error(`basic-memory: ${msg}${detail ? ` — ${detail}` : ""}`)
  },

  debug(msg: string, ...args: unknown[]): void {
    if (!_debug) return
    const fn = _backend.debug ?? _backend.info
    fn(`basic-memory [debug]: ${msg}`, ...args)
  },
}
