// @vitest-environment jsdom
// 文章页必须按 slug 取内容：之前所有 /help/:slug 都渲染同一篇静态文章，
// 点哪个链接内容都一样，属于可见的假数据。
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const route = vi.hoisted(() => ({ params: { slug: 'clash' } }))
const router = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => router,
}))

import ArticlePage from '../src/components/ArticlePage.vue'

function mountPage(slug) {
  route.params = { slug }
  return mount(ArticlePage, { global: { stubs: { Footer: true } } })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ArticlePage', () => {
  it('不同 slug 渲染不同文章标题', () => {
    const clash = mountPage('clash')
    expect(clash.text()).toContain('Clash 导入线路的完整步骤')

    const live = mountPage('live-stability')
    expect(live.text()).toContain('直播前的线路检查清单')
    expect(live.text()).not.toContain('Clash 导入线路的完整步骤')
  })

  it('未知 slug 给出明确提示而不是假装有内容', () => {
    const wrapper = mountPage('not-exist')
    expect(wrapper.text()).toContain('这篇内容还在整理')
    expect(wrapper.text()).toContain('返回教程')
  })

  it('目录锚点与正文标题一一对应', () => {
    const wrapper = mountPage('v2ray')
    const anchors = wrapper.findAll('aside a')
    const headings = wrapper.findAll('article h2')
    expect(anchors.length).toBe(headings.length)
    expect(anchors.map((item) => item.attributes('href'))).toEqual(headings.map((item) => `#${item.attributes('id')}`))
  })
})
