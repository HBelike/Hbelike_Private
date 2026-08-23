type Bridge = NonNullable<Window['interviewBridge']>

const previewBridge: Bridge = {
  platform: 'win32',
  async openLogin() {},
  async apiRequest<T>(_apiBaseUrl: string, path: string, init?: { method?: string; body?: unknown }): Promise<T> {
    if (path.endsWith('/setup-options')) {
      return {
        candidate_profiles: [{ id: 'preview-candidate', display_name: '中文产品与技术简历', source_filename: 'resume.pdf' }],
        target_roles: [{ id: 'preview-role', company_name: '示例企业', role_name: '高级产品经理' }],
        asr_models: [{ id: 'preview-asr', display_name: 'OpenAI 双语实时转写', readiness: 'ready' }],
        answer_models: [{ id: 'preview-answer', display_name: '中文回答模型', readiness: 'ready' }],
      } as T
    }
    if (path.endsWith('/interview-library/tree')) {
      return {
        items: [{ id: 'company:preview', label: '示例企业', children: [{ id: 'preview-experience', label: '高级产品经理一面', role_name: '产品经理' }] }],
      } as T
    }
    if (path.endsWith('/history')) {
      return {
        session: { status: 'completed' },
        utterances: [
          { id: 'u1', role: 'interviewer', raw_text: '请结合项目解释 CAP theorem？', corrected_text: null },
          { id: 'u2', role: 'candidate', raw_text: '我会先说明业务约束。', corrected_text: null },
        ],
        answers: [{ id: 'a1', question_version: 1, status: 'completed', normalized_question: '请结合项目解释 CAP theorem？', answer_text: '直接结论：CAP 描述分布式系统在网络分区下的一致性与可用性权衡。' }],
      } as T
    }
    if (path.endsWith('/sessions') && init?.method === 'POST') return { session: { id: 'preview-session' } } as T
    return {} as T
  },
  async openSocket() {},
  sendSocket() {},
  closeSocket() {},
  onSocketEvent() { return () => undefined },
  onSocketClosed() { return () => undefined },
}

if (!window.interviewBridge && !import.meta.env.DEV) {
  throw new Error('桌面桥接未加载，请从 Windows 客户端启动实时面试助手')
}

export const isElectronBridge = Boolean(window.interviewBridge)
export const bridge: Bridge = window.interviewBridge ?? previewBridge
