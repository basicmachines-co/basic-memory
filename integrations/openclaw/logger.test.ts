import { beforeEach, describe, expect, it, jest } from "bun:test"
import { initLogger, log } from "./logger.ts"

describe("logger", () => {
  let mockBackend: {
    info: jest.MockedFunction<any>
    warn: jest.MockedFunction<any>
    error: jest.MockedFunction<any>
    debug?: jest.MockedFunction<any>
  }

  beforeEach(() => {
    mockBackend = {
      info: jest.fn(),
      warn: jest.fn(),
      error: jest.fn(),
      debug: jest.fn(),
    }
  })

  describe("initLogger", () => {
    it("should initialize with backend and debug flag", () => {
      expect(() => initLogger(mockBackend, true)).not.toThrow()
    })
  })

  describe("log.info", () => {
    beforeEach(() => {
      initLogger(mockBackend, false)
    })

    it("should call backend.info with prefixed message", () => {
      log.info("test message")
      expect(mockBackend.info).toHaveBeenCalledWith(
        "basic-memory: test message",
      )
    })

    it("should pass through additional arguments", () => {
      log.info("test message", { extra: "data" }, 123)
      expect(mockBackend.info).toHaveBeenCalledWith(
        "basic-memory: test message",
        { extra: "data" },
        123,
      )
    })
  })

  describe("log.warn", () => {
    beforeEach(() => {
      initLogger(mockBackend, false)
    })

    it("should call backend.warn with prefixed message", () => {
      log.warn("warning message")
      expect(mockBackend.warn).toHaveBeenCalledWith(
        "basic-memory: warning message",
      )
    })

    it("should pass through additional arguments", () => {
      log.warn("warning message", { extra: "data" })
      expect(mockBackend.warn).toHaveBeenCalledWith(
        "basic-memory: warning message",
        { extra: "data" },
      )
    })
  })

  describe("log.error", () => {
    beforeEach(() => {
      initLogger(mockBackend, false)
    })

    it("should call backend.error with prefixed message", () => {
      log.error("error message")
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message",
      )
    })

    it("should append error message when Error is provided", () => {
      const error = new Error("Something went wrong")
      log.error("error message", error)
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message — Something went wrong",
      )
    })

    it("should append string when string error is provided", () => {
      log.error("error message", "string error")
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message — string error",
      )
    })

    it("should handle non-string, non-Error values", () => {
      log.error("error message", 123)
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message — 123",
      )

      log.error("error message", null)
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message — null",
      )

      log.error("error message", undefined)
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message",
      )
    })

    it("should handle empty/falsy error values", () => {
      log.error("error message", "")
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message",
      )

      log.error("error message", 0)
      expect(mockBackend.error).toHaveBeenCalledWith(
        "basic-memory: error message — 0",
      )
    })
  })

  describe("log.debug", () => {
    describe("when debug is enabled", () => {
      beforeEach(() => {
        initLogger(mockBackend, true)
      })

      it("should call backend.debug with prefixed message", () => {
        log.debug("debug message")
        expect(mockBackend.debug).toHaveBeenCalledWith(
          "basic-memory [debug]: debug message",
        )
      })

      it("should pass through additional arguments", () => {
        log.debug("debug message", { data: "test" })
        expect(mockBackend.debug).toHaveBeenCalledWith(
          "basic-memory [debug]: debug message",
          { data: "test" },
        )
      })

      it("should fallback to info if backend.debug is not available", () => {
        delete mockBackend.debug
        initLogger(mockBackend, true)

        log.debug("debug message")
        expect(mockBackend.info).toHaveBeenCalledWith(
          "basic-memory [debug]: debug message",
        )
      })
    })

    describe("when debug is disabled", () => {
      beforeEach(() => {
        initLogger(mockBackend, false)
      })

      it("should not call any backend methods", () => {
        log.debug("debug message")
        expect(mockBackend.info).not.toHaveBeenCalled()
        expect(mockBackend.warn).not.toHaveBeenCalled()
        expect(mockBackend.error).not.toHaveBeenCalled()
        expect(mockBackend.debug).not.toHaveBeenCalled()
      })
    })
  })

  describe("before initialization", () => {
    it("should not throw when using log methods before init", () => {
      // Create new logger state by not calling initLogger
      expect(() => {
        log.info("test")
        log.warn("test")
        log.error("test")
        log.debug("test")
      }).not.toThrow()
    })
  })

  describe("backend without debug method", () => {
    beforeEach(() => {
      const simpleBackend = {
        info: jest.fn(),
        warn: jest.fn(),
        error: jest.fn(),
        // no debug method
      }
      initLogger(simpleBackend, true)
      mockBackend = simpleBackend as any
    })

    it("should use info method as fallback for debug", () => {
      log.debug("debug message")
      expect(mockBackend.info).toHaveBeenCalledWith(
        "basic-memory [debug]: debug message",
      )
    })
  })

  describe("logger state isolation", () => {
    it("should maintain separate state for debug flag", () => {
      // First init with debug disabled
      initLogger(mockBackend, false)
      log.debug("should not appear")
      expect(mockBackend.debug).not.toHaveBeenCalled()

      // Re-init with debug enabled
      mockBackend.debug?.mockClear()
      initLogger(mockBackend, true)
      log.debug("should appear")
      expect(mockBackend.debug).toHaveBeenCalled()
    })

    it("should maintain separate backend instances", () => {
      const backend1 = {
        info: jest.fn(),
        warn: jest.fn(),
        error: jest.fn(),
      }

      const backend2 = {
        info: jest.fn(),
        warn: jest.fn(),
        error: jest.fn(),
      }

      initLogger(backend1, false)
      log.info("message 1")
      expect(backend1.info).toHaveBeenCalled()
      expect(backend2.info).not.toHaveBeenCalled()

      initLogger(backend2, false)
      log.info("message 2")
      expect(backend2.info).toHaveBeenCalled()
    })
  })
})
