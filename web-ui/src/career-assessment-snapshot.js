export function assessmentBindingKey(context, conversationId = '') {
  return JSON.stringify([conversationId, context?.binding_id,
    context?.candidate_profile?.id, context?.candidate_profile?.version,
    context?.target_role?.id, context?.target_role?.version])
}

export function updateAssessmentSnapshot(previous, context, conversationId = '') {
  const key = assessmentBindingKey(context, conversationId)
  if (['ready', 'fallback_ready'].includes(context?.assessment?.status)) {
    return { key, assessment: context.assessment }
  }
  return previous?.key === key ? previous : null
}
