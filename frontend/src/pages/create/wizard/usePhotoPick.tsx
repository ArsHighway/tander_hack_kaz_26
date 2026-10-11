import { useRef, useState } from 'react'
import { checkPhoto, photoError } from './photo.ts'

/**
 * Выбор файла фото: тип и размер проверяются до загрузки, ошибка — строкой у кнопки. Общий для поля фото на шаге 4
 * и кнопки «Загрузить фото» в красной плашке шага 5. `input` рисуется рядом с кнопкой, `open` открывает выбор файла.
 */
export function usePhotoPick(onUpload: (file: File) => Promise<void>) {
  const ref = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  const choose = async (file: File | undefined) => {
    if (!file) return
    const problem = checkPhoto(file)
    setError(problem)
    if (problem) return
    try {
      await onUpload(file)
    } catch (e) {
      setError(photoError(e))
    }
  }

  const input = (describedBy: string) => (
    <input
      ref={ref}
      type="file"
      accept="image/jpeg,image/png,image/webp"
      hidden
      aria-describedby={describedBy}
      onChange={(e) => {
        const file = e.target.files?.[0]
        // Сразу, до проверок: тот же файл можно выбрать снова (после отказа, «Удалить» или ошибки).
        e.target.value = ''
        void choose(file)
      }}
    />
  )

  return { error, open: () => ref.current?.click(), input }
}
