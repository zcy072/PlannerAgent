const DEFAULT_WARNING = '修改未保存，刷新后会丢失'

export interface PersistenceStatus {
  persistence_saved?: boolean | null
  persistence_warning?: string | null
}

/** 修改已生效但没能写盘时返回要展示的警告；已保存则返回 null。 */
export function persistenceWarning(res: PersistenceStatus): string | null {
  if (res.persistence_saved) return null
  return res.persistence_warning || DEFAULT_WARNING
}
