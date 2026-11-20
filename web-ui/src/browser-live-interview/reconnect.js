export const RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000]

export class LiveInterviewReconnect {
  constructor({ delays = RECONNECT_DELAYS_MS, timers = globalThis } = {}) {
    this.delays = delays
    this.timers = timers
    this.generation = 0
    this.pending = null
  }

  cancel() {
    this.generation += 1
    if (this.pending) {
      this.timers.clearTimeout(this.pending.timer)
      this.pending.resolve(false)
      this.pending = null
    }
  }

  async run(connect, isCurrent = () => true) {
    this.cancel()
    const generation = this.generation
    let lastError = null
    for (let index = 0; index < this.delays.length; index += 1) {
      const waited = await new Promise((resolve) => {
        const timer = this.timers.setTimeout(() => {
          this.pending = null
          resolve(true)
        }, this.delays[index])
        this.pending = { timer, resolve }
      })
      if (!waited || generation !== this.generation || !isCurrent()) return false
      try {
        await connect(index + 1)
        return generation === this.generation && isCurrent()
      } catch (error) {
        lastError = error
        if (generation !== this.generation || !isCurrent()) return false
      }
    }
    throw lastError || new Error('实时连接恢复失败')
  }
}
