// @vitest-environment jsdom
// 交付时粘贴链接：前端只做能确定的识别（协议/服务器/端口/传输），校验以后端为准。
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DeliveryLinkInput from '../src/components/DeliveryLinkInput.vue'

const VLESS = 'vless://11111111-2222-3333-4444-555555555555@us.example.com:443?type=ws&security=tls&sni=us.example.com#美国洛杉矶'

// 组件用 v-model 回传链接，测试里要把 update 事件接回 props，否则输入框里的值不会成为"当前链接"
function mountInput(initial = '') {
  const wrapper = mount(DeliveryLinkInput, {
    props: {
      modelValue: initial,
      'onUpdate:modelValue': (value) => wrapper.setProps({ modelValue: value }),
    },
  })
  return wrapper
}

describe('交付链接输入', () => {
  it('粘贴 vless 链接后回显协议、服务器与传输方式', async () => {
    const wrapper = mountInput()
    await wrapper.find('.link-text').setValue(VLESS)

    const hint = wrapper.find('.link-ok')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('VLESS')
    expect(hint.text()).toContain('us.example.com:443')
    expect(hint.text()).toContain('ws')
    expect(hint.text()).toContain('美国洛杉矶')
  })

  it('认不出的链接给警示而不是假装识别成功', async () => {
    const wrapper = mountInput()
    await wrapper.find('.link-text').setValue('随便一段文字')
    expect(wrapper.find('.link-ok').exists()).toBe(false)
    expect(wrapper.find('.link-bad').exists()).toBe(true)
  })

  it('手动填写后拼出标准链接并回传给父组件', async () => {
    const wrapper = mountInput()
    await wrapper.find('.link-switch').trigger('click')

    await wrapper.find('input[placeholder="us.example.com"]').setValue('jp.example.com')
    await wrapper.find('input[placeholder="443"]').setValue('8443')
    await wrapper.find('input[placeholder="uuid 或密码"]').setValue('secret-pass')
    await wrapper.find('input[placeholder="客户端里显示的名字"]').setValue('日本')

    const generate = wrapper.findAll('.manual-grid button').at(-1)
    await generate.trigger('click')

    const emitted = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(emitted.startsWith('vless://')).toBe(true)
    expect(emitted).toContain('jp.example.com:8443')
    expect(emitted).toContain('secret-pass')
  })

  it('写成 vmess 时拼的是 base64(JSON)，能被后端解出来', async () => {
    const wrapper = mountInput()
    await wrapper.find('.link-switch').trigger('click')
    await wrapper.find('select').setValue('vmess')
    await wrapper.find('input[placeholder="us.example.com"]').setValue('au.example.com')
    await wrapper.find('input[placeholder="443"]').setValue('443')
    await wrapper.find('input[placeholder="uuid 或密码"]').setValue('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    await wrapper.findAll('.manual-grid button').at(-1).trigger('click')

    const link = wrapper.emitted('update:modelValue').at(-1)[0]
    expect(link.startsWith('vmess://')).toBe(true)
    const payload = JSON.parse(atob(link.slice('vmess://'.length)))
    expect(payload.add).toBe('au.example.com')
    expect(payload.port).toBe('443')
    expect(payload.id).toBe('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
  })
})
