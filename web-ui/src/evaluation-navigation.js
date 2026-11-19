export const EVALUATION_VIEWS = {
  overview: { route: '/evaluations', label: '评测总览' },
  datasets: { route: '/evaluations/datasets', label: '真实数据集' },
  experiments: { route: '/evaluations/experiments', label: '实验记录' },
  metrics: { route: '/evaluations/metrics', label: '指标字典' }
}

export function evaluationViewForRoute(route) {
  return Object.keys(EVALUATION_VIEWS).find(key => EVALUATION_VIEWS[key].route === route) || 'overview'
}

export function evaluationRouteForView(view) {
  return (EVALUATION_VIEWS[view] || EVALUATION_VIEWS.overview).route
}
