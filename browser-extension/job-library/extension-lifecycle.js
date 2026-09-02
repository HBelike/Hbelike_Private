export function runExtensionTask(task, onError) {
  return Promise.resolve()
    .then(() => task())
    .catch((error) => {
      if (typeof onError !== 'function') return undefined
      return Promise.resolve()
        .then(() => onError(error))
        .catch(() => undefined)
    })
}

export function settleExtensionCalls(calls) {
  return Promise.allSettled(
    calls.map((call) => Promise.resolve().then(() => call()))
  )
}
