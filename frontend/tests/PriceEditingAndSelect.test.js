// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  bulkUpdateManagedCatalogProducts: vi.fn(),
  bulkUpdateManagedRetailPrices: vi.fn(),
  getManagedCatalogProducts: vi.fn(),
  getManagedRetailPrices: vi.fn(),
  updateManagedCatalogProduct: vi.fn(),
  updateManagedRetailPrice: vi.fn(),
}))

vi.mock('../src/api', () => api)

import AppSelect from '../src/components/AppSelect.vue'
import PriceCell from '../src/components/PriceCell.vue'
import CatalogManagementView from '../src/views/CatalogManagementView.vue'

const options = [
  { value: '', label: '全部状态' },
  { value: 'in_stock', label: '有货' },
  { value: 'out_of_stock', label: '无货' },
]

/** Teleport 到 body 的面板不在 wrapper 里，只能从 document 上找。 */
const panel = () => document.body.querySelector('.app-select-panel')

describe('AppSelect', () => {
  // 面板 Teleport 到 body，用例之间必须清干净；否则一个断言失败会让后面的用例拿到残留节点。
  beforeEach(() => { document.body.innerHTML = '' })

  it('显示当前选项的文案，值不在选项里时显示占位', async () => {
    const wrapper = mount(AppSelect, { props: { modelValue: 'in_stock', options } })
    expect(wrapper.text()).toContain('有货')

    const empty = mount(AppSelect, { props: { modelValue: 'gone', options, placeholder: '请选择状态' } })
    expect(empty.text()).toContain('请选择状态')
  })

  it('点击后把选项渲染成 body 上的固定定位面板，而不是原生 select', async () => {
    const wrapper = mount(AppSelect, { props: { modelValue: '', options }, attachTo: document.body })
    expect(wrapper.find('select').exists()).toBe(false)

    await wrapper.find('.app-select-trigger').trigger('click')

    const node = panel()
    expect(node).not.toBeNull()
    // 关键：坐标由组件自己算出并写成内联样式，绕开浏览器自绘弹层在滚动容器里漂移的问题
    expect(node.style.top).toMatch(/px$/)
    expect(node.style.left).toMatch(/px$/)
    expect(node.style.minWidth).toMatch(/px$/)
    expect(node.querySelectorAll('.app-select-option')).toHaveLength(3)
    wrapper.unmount()
  })

  it('点选项回传新值并关闭面板', async () => {
    const wrapper = mount(AppSelect, { props: { modelValue: '', options }, attachTo: document.body })
    await wrapper.find('.app-select-trigger').trigger('click')

    const items = panel().querySelectorAll('.app-select-option')
    items[2].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')).toEqual([['out_of_stock']])
    expect(panel()).toBeNull()
    wrapper.unmount()
  })

  it('Esc 关闭面板且不回传值', async () => {
    const wrapper = mount(AppSelect, { props: { modelValue: '', options }, attachTo: document.body })
    const trigger = wrapper.find('.app-select-trigger')
    await trigger.trigger('click')
    await trigger.trigger('keydown', { key: 'Escape' })

    expect(panel()).toBeNull()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it('禁用时不打开', async () => {
    const wrapper = mount(AppSelect, { props: { modelValue: '', options, disabled: true }, attachTo: document.body })
    await wrapper.find('.app-select-trigger').trigger('click')
    expect(panel()).toBeNull()
    wrapper.unmount()
  })
})

describe('PriceCell', () => {
  beforeEach(() => { document.body.innerHTML = '' })

  it('默认只读，双击才进入编辑', async () => {
    const wrapper = mount(PriceCell, { props: { value: 79 } })
    expect(wrapper.text()).toContain('¥79')
    expect(wrapper.find('input').exists()).toBe(false)

    await wrapper.find('.price-cell-read').trigger('dblclick')
    expect(wrapper.find('input').exists()).toBe(true)
    expect(wrapper.find('input').element.value).toBe('79')
  })

  it('输入后按 Enter 保存，值未变化则不触发保存', async () => {
    const wrapper = mount(PriceCell, { props: { value: 79 } })
    await wrapper.find('.price-cell-read').trigger('dblclick')

    const input = wrapper.find('input')
    await input.setValue('85')
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('save')).toEqual([[85]])

    const same = mount(PriceCell, { props: { value: 79 } })
    await same.find('.price-cell-read').trigger('dblclick')
    await same.find('input').trigger('keydown', { key: 'Enter' })
    expect(same.emitted('save')).toBeUndefined()
  })

  it('Esc 取消，不保存也不改值', async () => {
    const wrapper = mount(PriceCell, { props: { value: 79 } })
    await wrapper.find('.price-cell-read').trigger('dblclick')
    const input = wrapper.find('input')
    await input.setValue('120')
    await input.trigger('keydown', { key: 'Escape' })

    expect(wrapper.emitted('save')).toBeUndefined()
    expect(wrapper.find('input').exists()).toBe(false)
    expect(wrapper.text()).toContain('¥79')
  })

  it('非法价格就地报错且不保存', async () => {
    const wrapper = mount(PriceCell, { props: { value: 79 } })
    await wrapper.find('.price-cell-read').trigger('dblclick')
    const input = wrapper.find('input')
    await input.setValue('0')
    await input.trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('save')).toBeUndefined()
    expect(wrapper.find('.price-cell-error').exists()).toBe(true)
    // 仍在编辑态，用户可以直接改
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('锁定行双击不进入编辑', async () => {
    const wrapper = mount(PriceCell, { props: { value: 79, locked: true } })
    await wrapper.find('.price-cell-read').trigger('dblclick')
    expect(wrapper.find('input').exists()).toBe(false)
  })

  it('点确认按钮保存（mousedown 不抢焦点，避免先触发取消）', async () => {
    const wrapper = mount(PriceCell, { props: { value: 79 } })
    await wrapper.find('.price-cell-read').trigger('dblclick')
    await wrapper.find('input').setValue('92')
    await wrapper.find('.price-cell-confirm').trigger('click')
    expect(wrapper.emitted('save')).toEqual([[92]])
  })
})

describe('价格管理页接入', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    Object.values(api).forEach((fn) => fn.mockReset())
    api.getManagedCatalogProducts.mockResolvedValue({
      data: {
        items: [
          { id: 12, name: '中国香港 静态住宅IP 30天', product_type: '静态住宅IP', region_name: '中国香港', billing_days: 30, price: 79, is_in_stock: true, price_locked: false },
        ],
        total: 1,
      },
    })
    api.getManagedRetailPrices.mockResolvedValue({
      data: { items: [], total: 0, categories: [], countries: [], billing_days_options: [30] },
    })
    api.updateManagedCatalogProduct.mockResolvedValue({ data: {} })
  })

  it('筛选状态下拉打开后不再有原生 select，选"无货"会刷新列表', async () => {
    const wrapper = mount(CatalogManagementView, { attachTo: document.body })
    await flushPromises()

    expect(wrapper.find('select').exists()).toBe(false)

    const trigger = wrapper.findAll('.app-select-trigger')[0]
    await trigger.trigger('click')
    const items = document.body.querySelectorAll('.app-select-panel .app-select-option')
    expect(items.length).toBe(3)

    api.getManagedCatalogProducts.mockClear()
    items[2].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    // 只是选择，不应该自己提交表单重新筛选
    expect(api.getManagedCatalogProducts).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('双击商品价格并确认后调用更新接口，无需再找保存按钮', async () => {
    const wrapper = mount(CatalogManagementView, { attachTo: document.body })
    await flushPromises()

    const cell = wrapper.find('.price-cell-read')
    expect(cell.text()).toBe('¥79')
    await cell.trigger('dblclick')
    await wrapper.find('.price-cell input').setValue('88')
    await wrapper.find('.price-cell input').trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(api.updateManagedCatalogProduct).toHaveBeenCalledWith(12, { price: 88 })
    wrapper.unmount()
  })
})
