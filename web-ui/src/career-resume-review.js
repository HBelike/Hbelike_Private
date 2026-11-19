export function isResumeReviewTurn(turn) {
  return (turn?.input_kind_codes ?? []).includes('resume_review')
}

export function visibleConversationMessages(messages = []) {
  // 后台动作说明仍在历史中留痕，但不伪装为用户发送的提问。
  return messages.filter((message) => !(message.role === 'user' && message.source === 'resume_review'))
}
