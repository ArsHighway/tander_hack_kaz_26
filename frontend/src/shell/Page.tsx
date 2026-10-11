import { Box, Typography } from '@mui/material'
import { useEffect, type ReactNode } from 'react'
import { TOKENS } from '../theme'
import { PRODUCT_NAME } from './brand'
import { PAGE_TITLE_SX } from './pageTitle.ts'

type Props = {
  title?: string
  description?: ReactNode
  actions?: ReactNode
  /** Содержимое одним белым блоком (мастер). Без него экран сам кладёт блоки — `Paper variant="outlined"`. */
  surface?: boolean
  children: ReactNode
}

/** Заголовок в два цвета, как разделы портала («Категории товаров»): последнее слово Main Blue. */
export function TwoTone({ text }: { text: string }) {
  const cut = text.lastIndexOf(' ')
  if (cut < 0) return text
  return (
    <>
      {text.slice(0, cut + 1)}
      <Box component="span" sx={{ color: TOKENS.acc }}>
        {text.slice(cut + 1)}
      </Box>
    </>
  )
}

/**
 * Тело экрана: заголовок с описанием и действиями — белым блоком, как у мастера, ниже содержимое белыми блоками на
 * голубом фоне. Контейнер 1440 px или 74 % ширины экрана, что больше. id — цель ссылки «Перейти к содержимому».
 */
export function Page({ title, description, actions, surface, children }: Props) {
  useEffect(() => {
    document.title = title ? `${title}: ${PRODUCT_NAME}` : PRODUCT_NAME
  }, [title])
  return (
    <Box component="main" id="content" tabIndex={-1} className="mx-auto w-full max-w-[max(1440px,74vw)] px-4 pt-6 pb-12 outline-none md:px-6">
      {title && (
        <Box className="mb-4 flex flex-wrap items-end justify-between gap-3 p-4 md:px-6 md:py-5" sx={{ bgcolor: 'background.paper' }}>
          <Box className="min-w-0">
            <Typography variant="h4" component="h1" sx={PAGE_TITLE_SX}>
              <TwoTone text={title} />
            </Typography>
            {description && (
              <Typography color="text.secondary" className="mt-1 max-w-[70ch]">
                {description}
              </Typography>
            )}
          </Box>
          {actions && <Box className="flex shrink-0 flex-wrap gap-2">{actions}</Box>}
        </Box>
      )}
      {surface ? (
        <Box className="p-4 md:p-6" sx={{ bgcolor: 'background.paper' }}>
          {children}
        </Box>
      ) : (
        children
      )}
    </Box>
  )
}
