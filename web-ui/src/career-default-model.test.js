import test from 'node:test'
import assert from 'node:assert/strict'
import { platformDefaultSelection } from './career-default-model.js'

const profile = (id, model, provider='deepseek') => ({readiness:'ready',profile:{id,model_id:model,provider_key:provider}})

test('所有用户的初始选择优先共享Pro，不能选到顺序靠前的免费模型或Flash', () => {
  const items=[profile('free','qwen3.6-flash','qwen'),profile('flash','deepseek-v4-flash'),profile('pro','deepseek-v4-pro')]
  assert.deepEqual(platformDefaultSelection(items),{mode:'specific_profile',profileId:'pro'})
  assert.deepEqual(platformDefaultSelection(items,{provider_key:'deepseek',model_id:'deepseek-v4-pro',profile_id:'pro'}),{mode:'specific_profile',profileId:'pro'})
})

test('共享默认模型未就绪时保留平台默认策略，不伪装成免费自动选择', () => {
  assert.deepEqual(platformDefaultSelection([profile('flash','deepseek-v4-flash')]),{mode:'platform_default',profileId:''})
  assert.deepEqual(platformDefaultSelection([{...profile('pro','deepseek-v4-pro'),readiness:'disabled'}]),{mode:'platform_default',profileId:''})
})
