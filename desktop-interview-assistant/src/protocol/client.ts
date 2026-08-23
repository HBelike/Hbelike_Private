export type ServerEvent = Record<string, unknown> & { type: string }

import { bridge } from '../bridge'

export class InterviewSocketClient {
  private removeEventListener: (() => void) | null = null
  private removeCloseListener: (() => void) | null = null

  constructor(
    private readonly onEvent: (event: ServerEvent) => void,
    private readonly onClosed: (reason: string) => void,
  ) {}

  async open(apiBaseUrl: string, sessionId: string): Promise<void> {
    this.removeEventListener = bridge.onSocketEvent((payload) => {
      if (payload && typeof payload === 'object' && 'type' in payload) this.onEvent(payload as ServerEvent)
    })
    this.removeCloseListener = bridge.onSocketClosed((payload) => {
      this.onClosed(payload.reason || `连接已关闭（${payload.code}）`)
    })
    await bridge.openSocket(apiBaseUrl, sessionId)
  }

  send(payload: unknown): void {
    bridge.sendSocket(payload)
  }

  close(): void {
    bridge.closeSocket()
    this.removeEventListener?.()
    this.removeCloseListener?.()
    this.removeEventListener = null
    this.removeCloseListener = null
  }
}
