export function getCareerLayoutMode(width, navigationCollapsed = false) {
  const normalizedWidth = Number(width) || 0
  if (normalizedWidth < 820) return 'single'
  // 收起导航释放的空间用于聊天，避免因此提前展开职位信息栏。
  const layoutWidth = normalizedWidth - (navigationCollapsed ? 80 : 0)
  if (layoutWidth < 1360) return 'compact'
  return 'wide'
}

export function getPanelAfterLayoutChange(previousMode, nextMode, panel) {
  return previousMode === nextMode ? panel : ''
}
