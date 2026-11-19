export const FALLBACK_PLATFORM_MODEL = {
  provider_key: 'deepseek', model_id: 'deepseek-v4-pro', profile_id: null, readiness: 'missing'
}

export function platformDefaultSelection(profiles, descriptor = FALLBACK_PLATFORM_MODEL) {
  const profile = profiles.find(item => item.readiness === 'ready'
    && (descriptor.profile_id ? item.profile.id === descriptor.profile_id
      : item.profile.provider_key === descriptor.provider_key && item.profile.model_id === descriptor.model_id))
  return profile
    ? { mode: 'specific_profile', profileId: profile.profile.id }
    : { mode: 'platform_default', profileId: '' }
}
