/**
 * 时间处理工具。
 *
 * 后端返回的是带时区的 ISO 字符串（如 2026-11-01T04:37:49.122924Z），
 * new Date() 会自动按**浏览器本地时区**解析，所以这里的比较和格式化
 * 全部用本地时间，不需要手动做时区换算。
 */

import type { Conversation, ConversationGroup } from '../types'

const DAY_MS = 24 * 60 * 60 * 1000

/** 取某个时刻当天的 00:00（本地时区），用于"是不是同一天"的判断 */
function startOfDay(date: Date): number {
  const copy = new Date(date)
  copy.setHours(0, 0, 0, 0)
  return copy.getTime()
}

/** 今天的 00:00 时间戳 */
function todayStart(): number {
  return startOfDay(new Date())
}

/**
 * 把会话按「今天 / 昨天 / 本周 / 更早」分组。
 *
 * 列表本身已由后端按 updated_at 倒序排好，这里只做分组不改顺序，
 * 空分组直接丢弃（不会出现"昨天"下面空一片的情况）。
 */
export function groupConversations(items: Conversation[]): ConversationGroup[] {
  const today = todayStart()
  const yesterday = today - DAY_MS
  const weekAgo = today - 6 * DAY_MS

  const buckets: ConversationGroup[] = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '本周', items: [] },
    { label: '更早', items: [] },
  ]

  for (const item of items) {
    const time = startOfDay(new Date(item.updated_at))
    if (time >= today) buckets[0].items.push(item)
    else if (time >= yesterday) buckets[1].items.push(item)
    else if (time >= weekAgo) buckets[2].items.push(item)
    else buckets[3].items.push(item)
  }

  return buckets.filter((bucket) => bucket.items.length > 0)
}

/** 侧边栏右侧的时间角标：今天显示时分，其余显示日期 */
export function formatShortTime(iso: string): string {
  const date = new Date(iso)
  const time = startOfDay(date)

  if (time >= todayStart()) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  }
  if (time >= todayStart() - DAY_MS) {
    return '昨天'
  }
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

/**
 * 记忆面板用的时间：精确到分钟，且带"今天 / 昨天"前缀。
 *
 * 和 formatShortTime 的区别：侧边栏位置窄，只能放得下 3~5 个字；
 * 记忆面板一行能放更多，所以补上"今天 14:32"这种更有信息量的写法——
 * 用户判断"这条记忆是不是老掉牙了"靠的就是这个。
 */
export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  const day = startOfDay(date)
  const hm = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

  if (day >= todayStart()) return `今天 ${hm}`
  if (day >= todayStart() - DAY_MS) return `昨天 ${hm}`

  const md = date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
  return `${md} ${hm}`
}
