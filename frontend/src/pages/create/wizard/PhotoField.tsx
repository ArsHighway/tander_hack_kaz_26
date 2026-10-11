import { Box, Button, Typography } from '@mui/material'
import { useEffect, useId, useRef } from 'react'
import { PhotoThumb } from '../../../card/PhotoThumb'
import { TOKENS } from '../../../theme'
import { PHOTO, PHOTO_FIELD_ID } from './submit.ts'
import { usePhotoPick } from './usePhotoPick'

type Props = {
  photoId: number | null
  /** Загрузка идёт: её ведёт страница (ответ после «Начать заново» отбрасывается). */
  uploading: boolean
  /** Загрузить файл; ошибка — исключением, подсказка под полем. */
  onUpload: (file: File) => Promise<void>
  onRemove: () => void
}

/** Необязательное фото: файл уходит на сервер при выборе, заявка ссылается на него номером. */
export function PhotoField({ photoId, uploading, onUpload, onRemove }: Props) {
  const id = useId()
  const pick = useRef<HTMLButtonElement>(null)
  const removed = useRef(false)
  const { error, open, input } = usePhotoPick(onUpload)

  // После «Удалить» кнопка исчезает: фокус — на «Загрузить фото», а не в никуда.
  useEffect(() => {
    if (removed.current && photoId === null) {
      removed.current = false
      pick.current?.focus()
    }
  }, [photoId])

  return (
    <Box role="group" aria-labelledby={`${id}-label`}>
      <Typography id={`${id}-label`} sx={{ fontSize: '1.0625rem', fontWeight: 600 }}>
        {PHOTO}
        <Box component="span" sx={{ ml: 1, fontSize: '0.8125rem', fontWeight: 400, color: TOKENS.mute }}>
          необязательно
        </Box>
      </Typography>
      <Box className="mt-1.5 flex flex-wrap items-center gap-3">
        {photoId !== null && <PhotoThumb key={photoId} photoId={photoId} size={72} />}
        <Button
          ref={pick}
          id={PHOTO_FIELD_ID}
          variant="outlined"
          disabled={uploading}
          aria-describedby={`${id}-help`}
          onClick={open}
        >
          {uploading ? 'Загружаем…' : photoId !== null ? 'Заменить' : 'Загрузить фото'}
        </Button>
        {photoId !== null && (
          <Button
            color="inherit"
            disabled={uploading}
            onClick={() => {
              removed.current = true
              onRemove()
            }}
            sx={{ color: TOKENS.ink2 }}
          >
            Удалить
          </Button>
        )}
      </Box>
      <Typography id={`${id}-help`} role={error ? 'alert' : undefined} sx={{ mt: 0.75, fontSize: '0.8125rem', color: error ? TOKENS.err : TOKENS.mute }}>
        {error ?? 'Можно отправить без фото. JPEG, PNG или WebP до 5 МБ.'}
      </Typography>
      {input(`${id}-help`)}
    </Box>
  )
}
