const IMAGE_PREVIEW_LABEL = '点击查看原图'

export function decorateOpenableImages(container) {
  if (!container?.querySelectorAll) return

  container.querySelectorAll('img').forEach((image) => {
    const imageUrl = image.getAttribute?.('src') || image.currentSrc || image.src
    if (!imageUrl) return

    let link = image.closest?.('a')
    if (!link) {
      link = image.ownerDocument.createElement('a')
      image.replaceWith(link)
      link.append(image)
    }

    link.classList.add('article-image-link')
    link.setAttribute('href', imageUrl)
    link.setAttribute('target', '_blank')
    link.setAttribute('rel', 'noreferrer')
    link.setAttribute('aria-label', IMAGE_PREVIEW_LABEL)
    link.setAttribute('title', IMAGE_PREVIEW_LABEL)
  })
}
