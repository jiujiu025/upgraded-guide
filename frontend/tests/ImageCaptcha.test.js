// @vitest-environment jsdom
// 注册图形验证码：九宫格渲染、点选/取消、答案回传、换一题与加载失败。
// 关键约定：组件不做任何本地判题，只把后端给的 token 和选中的格子编号原样交回去。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getCaptcha: vi.fn() }))
vi.mock('../src/api', () => api)

import ImageCaptcha from '../src/components/ImageCaptcha.vue'

const challenge = (token = 'token-a') => ({
  token,
  target: '星星',
  count: 2,
  expires_in: 180,
  tiles: Array.from({ length: 9 }, (_, index) => ({
    index,
    svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="#d64545"/></svg>',
  })),
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ImageCaptcha', () => {
  it('渲染九宫格并把 SVG 转成 data URI', async () => {
    api.getCaptcha.mockResolvedValue({ data: challenge() })
    const wrapper = mount(ImageCaptcha)
    await flushPromises()
    const tiles = wrapper.findAll('.captcha-tile')
    expect(tiles).toHaveLength(9)
    expect(wrapper.text()).toContain('星星')
    expect(wrapper.text()).toContain('共 2 张')
    expect(tiles[0].find('img').attributes('src')).toMatch(/^data:image\/svg\+xml/)
  })

  it('一张没选时不给答案，选中后按编号升序返回', async () => {
    api.getCaptcha.mockResolvedValue({ data: challenge() })
    const wrapper = mount(ImageCaptcha)
    await flushPromises()
    expect(wrapper.vm.answer()).toBeNull()

    const tiles = wrapper.findAll('.captcha-tile')
    await tiles[5].trigger('click')
    await tiles[1].trigger('click')
    expect(wrapper.vm.answer()).toEqual({ token: 'token-a', selection: [1, 5] })

    await tiles[5].trigger('click')
    expect(wrapper.vm.answer()).toEqual({ token: 'token-a', selection: [1] })
  })

  it('换一题会清空已选并重新请求', async () => {
    api.getCaptcha.mockResolvedValueOnce({ data: challenge('token-a') })
    const wrapper = mount(ImageCaptcha)
    await flushPromises()
    await wrapper.findAll('.captcha-tile')[3].trigger('click')
    expect(wrapper.vm.answer()).not.toBeNull()

    api.getCaptcha.mockResolvedValueOnce({ data: challenge('token-b') })
    await wrapper.find('.captcha-refresh').trigger('click')
    await flushPromises()
    expect(wrapper.vm.answer()).toBeNull()
    expect(api.getCaptcha).toHaveBeenCalledTimes(2)
    await wrapper.findAll('.captcha-tile')[0].trigger('click')
    expect(wrapper.vm.answer()).toEqual({ token: 'token-b', selection: [0] })
  })

  it('加载失败时提示且不能提交', async () => {
    api.getCaptcha.mockRejectedValue({ userMessage: '请求过于频繁，请稍后再试' })
    const wrapper = mount(ImageCaptcha)
    await flushPromises()
    expect(wrapper.text()).toContain('请求过于频繁')
    expect(wrapper.findAll('.captcha-tile')).toHaveLength(0)
    expect(wrapper.vm.answer()).toBeNull()
  })
})
