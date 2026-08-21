import assert from 'node:assert/strict'
import test from 'node:test'

import { decorateOpenableImages } from './image-preview.js'

function createFakeElement(tagName) {
  const attributes = new Map()
  const classes = new Set()
  return {
    tagName,
    attributes,
    classes,
    child: null,
    setAttribute(name, value) {
      attributes.set(name, value)
    },
    getAttribute(name) {
      return attributes.get(name) ?? null
    },
    classList: {
      add(name) {
        classes.add(name)
      }
    },
    append(child) {
      this.child = child
    }
  }
}

test('decorateOpenableImages 用原图链接包裹正文图片', () => {
  const link = createFakeElement('A')
  const image = createFakeElement('IMG')
  image.currentSrc = '/api/media-assets/131/file'
  image.src = 'http://localhost/api/media-assets/131/file'
  image.closest = () => null
  image.ownerDocument = {
    createElement(tagName) {
      assert.equal(tagName, 'a')
      return link
    }
  }
  image.replaceWith = (replacement) => {
    assert.equal(replacement, link)
  }
  const container = {
    querySelectorAll(selector) {
      assert.equal(selector, 'img')
      return [image]
    }
  }

  decorateOpenableImages(container)

  assert.equal(link.child, image)
  assert.equal(link.attributes.get('href'), '/api/media-assets/131/file')
  assert.equal(link.attributes.get('target'), '_blank')
  assert.equal(link.attributes.get('rel'), 'noreferrer')
  assert.equal(link.attributes.get('aria-label'), '点击查看原图')
  assert.equal(link.attributes.get('title'), '点击查看原图')
  assert.equal(link.classes.has('article-image-link'), true)
})

test('decorateOpenableImages 复用正文图片已有链接并指向原图', () => {
  const link = createFakeElement('A')
  const image = createFakeElement('IMG')
  image.src = '/images/project.png'
  image.closest = (selector) => {
    assert.equal(selector, 'a')
    return link
  }
  const container = {
    querySelectorAll() {
      return [image]
    }
  }

  decorateOpenableImages(container)

  assert.equal(link.child, null)
  assert.equal(link.attributes.get('href'), '/images/project.png')
  assert.equal(link.attributes.get('target'), '_blank')
  assert.equal(link.attributes.get('rel'), 'noreferrer')
})

test('decorateOpenableImages 忽略没有地址的图片', () => {
  const image = createFakeElement('IMG')
  image.src = ''
  image.currentSrc = ''
  image.closest = () => null
  let created = false
  image.ownerDocument = {
    createElement() {
      created = true
    }
  }
  const container = {
    querySelectorAll() {
      return [image]
    }
  }

  decorateOpenableImages(container)

  assert.equal(created, false)
})
