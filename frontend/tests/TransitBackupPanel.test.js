// @vitest-environment jsdom
// 面板备份：列表、立即备份、恢复前必须预检。
// 恢复是覆盖性操作，必须"先预检看冲突，再确认执行"，不能一步点下去就动生产面板。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  createTransitBackup: vi.fn(),
  getTransitBackups: vi.fn(),
  restoreTransitBackup: vi.fn(),
  deleteTransitBackup: vi.fn(),
  downloadTransitBackup: vi.fn(),
}))
vi.mock('../src/api', () => api)

import TransitBackupPanel from '../src/views/TransitBackupPanel.vue'

const panels = [
  { id: 1, name: 'HK-01', group_code: 'hk', connect_host: '9.9.9.9' },
  { id: 2, name: 'HK-02', group_code: 'hk', connect_host: '8.8.8.8' },
]

const backups = [
  {
    id: 11, vps_id: 1, vps_name: 'HK-01', filename: 'HK-01-20261126-100000.json.gz',
    size_bytes: 20480, inbound_count: 34, outbound_count: 25, rule_count: 29,
    status: 'ready', created_at: '2026-11-26T10:00:00', restored_at: null,
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  window.confirm = vi.fn(() => true)
  api.getTransitBackups.mockResolvedValue({ data: backups })
  api.createTransitBackup.mockResolvedValue({
    data: { id: 12, inbound_count: 34, size_bytes: 20480 },
  })
  api.restoreTransitBackup.mockResolvedValue({
    data: {
      backup_id: 11, total_inbounds: 34, will_create: 33, conflicts: [52000],
      outbounds_to_add: 25, rules_to_add: 29, dry_run: true,
    },
  })
})

function button(wrapper, label) {
  return wrapper.findAll('button').find((item) => item.text().includes(label))
}

describe('TransitBackupPanel', () => {
  it('列出备份内容与状态', async () => {
    const wrapper = mount(TransitBackupPanel, { props: { panels } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('HK-01')
    expect(text).toContain('34 入站')
    expect(text).toContain('25 出站')
    expect(text).toContain('20.0 KB')
    expect(text).toContain('可用')
  })

  it('选面板后可以立即备份', async () => {
    const wrapper = mount(TransitBackupPanel, { props: { panels } })
    await flushPromises()

    await wrapper.find('.tb-create select').setValue('2')
    await button(wrapper, '立即备份').trigger('click')
    await flushPromises()

    expect(api.createTransitBackup).toHaveBeenCalledWith(2)
    expect(wrapper.text()).toContain('已备份')
  })

  it('恢复先预检，显示端口冲突与将新建数量', async () => {
    const wrapper = mount(TransitBackupPanel, { props: { panels } })
    await flushPromises()

    await button(wrapper, '恢复').trigger('click')
    await flushPromises()

    // 目标面板排除源面板
    const options = wrapper.findAll('.tb-dialog select option').map((item) => item.text())
    expect(options.some((text) => text.includes('HK-02'))).toBe(true)
    expect(options.some((text) => text.includes('HK-01'))).toBe(false)

    await wrapper.find('.tb-dialog select').setValue('2')
    await button(wrapper, '预检冲突').trigger('click')
    await flushPromises()

    expect(api.restoreTransitBackup).toHaveBeenCalledWith(11, { target_vps_id: 2, dry_run: true })
    expect(wrapper.text()).toContain('33')
    expect(wrapper.text()).toContain('52000')
    expect(wrapper.text()).toContain('恢复时会跳过')
  })

  it('预检结果出来后才出现确认恢复按钮', async () => {
    const wrapper = mount(TransitBackupPanel, { props: { panels } })
    await flushPromises()

    await button(wrapper, '恢复').trigger('click')
    await flushPromises()
    await wrapper.find('.tb-dialog select').setValue('2')
    await button(wrapper, '预检冲突').trigger('click')
    await flushPromises()

    expect(button(wrapper, '确认恢复')).toBeTruthy()

    api.restoreTransitBackup.mockResolvedValue({
      data: { backup_id: 11, total_inbounds: 34, will_create: 33, conflicts: [52000],
        outbounds_to_add: 25, rules_to_add: 29, dry_run: false,
        created: Array.from({ length: 33 }, (_, index) => 52001 + index),
        skipped: [{ port: 52000, reason: '目标面板已有同端口入站' }], failed: [] },
    })
    await button(wrapper, '确认恢复').trigger('click')
    await flushPromises()

    expect(api.restoreTransitBackup).toHaveBeenLastCalledWith(11, { target_vps_id: 2, dry_run: false })
    expect(wrapper.text()).toContain('已新建 33 条')
  })

  it('删除前要确认', async () => {
    window.confirm = vi.fn(() => false)
    const wrapper = mount(TransitBackupPanel, { props: { panels } })
    await flushPromises()

    await wrapper.find('.tb-actions button.danger').trigger('click')
    await flushPromises()
    expect(api.deleteTransitBackup).not.toHaveBeenCalled()
  })
})
