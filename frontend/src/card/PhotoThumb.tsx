import { Box } from '@mui/material'
import { IconPhoto } from '@tabler/icons-react'
import { useState } from 'react'
import { photoUrl } from '../api/v1.ts'
import { TOKENS } from '../theme'

/** Картинка меньше этого по любой стороне — не фото товара (пиксель-заглушка из тестов): показываем «нет фото». */
const MIN_SIDE = 16

/** Место фото без фото: спокойный голубой квадрат со значком, без красного — фото у товара необязательно. */
export function NoPhoto({ size }: { size: number }) {
  return (
    <Box
      component="span"
      aria-label="Нет фото"
      role="img"
      sx={{
        width: size,
        height: size,
        flex: 'none',
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '2px',
        bgcolor: TOKENS.accSoft,
        color: TOKENS.mute,
        borderRadius: '6px',
        fontSize: '0.6875rem',
        lineHeight: 1.1,
        textAlign: 'center',
      }}
    >
      <IconPhoto size={Math.round(size * 0.42)} stroke={1.25} aria-hidden />
      {size >= 56 && <span aria-hidden>нет фото</span>}
    </Box>
  )
}

/** Фото товара из заявки; не загрузилось или это не фото (пиксель) — место «нет фото», без битой иконки. */
export function PhotoThumb({ photoId, size }: { photoId: number; size: number }) {
  const [none, setNone] = useState(false)
  if (none) return <NoPhoto size={size} />
  return (
    <Box
      component="img"
      src={photoUrl(photoId)}
      alt="Фото товара"
      loading="lazy"
      decoding="async"
      width={size}
      height={size}
      onError={() => setNone(true)}
      onLoad={(e) => {
        const img = e.currentTarget
        if (img.naturalWidth < MIN_SIDE || img.naturalHeight < MIN_SIDE) setNone(true)
      }}
      sx={{ width: size, height: size, flex: 'none', objectFit: 'contain', bgcolor: '#ffffff', border: `1px solid ${TOKENS.line}`, borderRadius: '6px' }}
    />
  )
}
