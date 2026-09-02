export function formatWeekRange(value) {
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? '').trim())
  if (!matched) return value ? String(value) : '周期未知'

  const end = new Date(Date.UTC(Number(matched[1]), Number(matched[2]) - 1, Number(matched[3])))
  if (Number.isNaN(end.getTime())) return String(value)
  const start = new Date(end)
  start.setUTCDate(end.getUTCDate() - 6)
  const formatDate = (date) => [
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, '0'),
    String(date.getUTCDate()).padStart(2, '0')
  ].join('-')
  return `${formatDate(start)} 至 ${formatDate(end)}`
}
