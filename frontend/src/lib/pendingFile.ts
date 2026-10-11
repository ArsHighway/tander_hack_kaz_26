// Файл, который мастер передаёт массовой загрузке при переходе: живёт в памяти вкладки
// (переход без перезагрузки страницы) и отдаётся один раз — повторный заход не загрузит его снова.

let pending: File | null = null

export const pendingFile = {
  put(file: File): void {
    pending = file
  },
  /** Посмотреть, не забирая: начальное состояние страницы (в StrictMode его считают дважды). */
  peek(): File | null {
    return pending
  },
  take(): File | null {
    const file = pending
    pending = null
    return file
  },
}
