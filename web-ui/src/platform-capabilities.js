export function isMobileBrowser(browser = globalThis.navigator) {
  return browser?.userAgentData?.mobile === true
    || /Android|iPhone|iPod|Mobile|CriOS|FxiOS|EdgiOS/i.test(browser?.userAgent || '')
}
