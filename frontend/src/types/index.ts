/**
 * 前后端共享的数据结构定义。
 *
 * 集中放一处的好处：后端字段一改，这里编译就会报错，不用靠人肉搜。
 */

/** 当前登录用户（后端 /auth/login 和 /auth/me 返回的 user 字段） */
export interface User {
  id: string
  username: string
  display_name: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

/** 侧边栏列表项。不含消息正文，列表接口只返回轻量数据 */
export interface Conversation {
  id: string
  title: string
  message_count: number
  created_at: string
  updated_at: string
}

/** 后端返回的消息行（含 tool 消息） */
export interface MessageRow {
  id: number
  role: 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: unknown[] | null
  tool_name?: string | null
  created_at: string
}

export interface ConversationDetail extends Conversation {
  summary: string | null
  messages: MessageRow[]
}

/**
 * 渲染用消息。
 *
 * 和 MessageRow 的差别：数据库里一次工具调用会拆成两条记录
 * （assistant 带 tool_calls + tool 带结果），但界面上它们是同一个气泡。
 * 所以这里把工具结果折叠成 toolLines 数组，挂在 assistant 气泡上。
 */
export interface UiMessage {
  role: 'user' | 'assistant'
  content: string
  /** 工具调用状态行，按发生顺序展示 */
  toolLines: string[]
  /** 是否正在流式输出（控制光标闪烁） */
  streaming?: boolean
  /** 本条消息是否出错（红色展示） */
  failed?: boolean
}

/** 左侧会话列表按时间分组后的结构 */
export interface ConversationGroup {
  label: string
  items: Conversation[]
}

/** 记忆类型。后端注释：profile=身份，preference=稳定偏好，fact=长期事实 */
export type MemoryKind = 'profile' | 'preference' | 'fact'

/**
 * 一条长期记忆。
 *
 * 和消息的区别：消息属于某个会话，换个会话就看不到；
 * 记忆属于用户，跨会话生效，会注入到每一轮的系统提示里。
 */
export interface Memory {
  id: number
  kind: MemoryKind
  content: string
  /** 0~1，越高越重要。面板排序和注入时的阈值过滤都用它 */
  importance: number
  created_at: string
  /** 最近一次被注入到对话里的时间。null 表示还没用过 */
  last_used_at: string | null
}
