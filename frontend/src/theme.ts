import { createTheme } from '@mui/material/styles'
import { IconChevronDown } from '@tabler/icons-react'
import { createElement } from 'react'

/**
 * Токены дизайн-системы (`DESIGN.md`) — цвета UI-кита Портала поставщиков (Figma организаторов,
 * `data/figma/portal-kit.md`). Синий — ссылки, выбор, рамки действий; красный — одна заливная кнопка и ошибки.
 */
export const TOKENS = {
  /** Основной текст: Black портала. */
  ink: '#1a1a1a',
  /** Вторичный текст, коды. */
  ink2: '#333333',
  /** Подписи. Pale Black портала `#8c8c8c` даёт 3,4:1 — темнее в том же сером, 5,3:1 на белом и 4,5:1 на голубом. */
  mute: '#646c78',
  /** Разделители и рамки кнопок с обводкой: Gray Blue портала. */
  line: '#d4dbe6',
  /** Рамка поля ввода, кружок варианта, подсказка клавиши: Gray портала, 3,6:1 на белом (WCAG 1.4.11 — от 3:1). */
  field: '#7f8792',
  /** Main Blue портала: текущий шаг, выбранный вариант, ссылки, рамки кнопок, активные вкладки. */
  acc: '#264b82',
  /** Pale Blue портала: фон страницы, выбранного варианта и текущего шага. */
  accSoft: '#e7eef7',
  /** Red портала: единственная заливная кнопка экрана. */
  cta: '#db2b21',
  /** Заливная кнопка при наведении: красный плюс 10 % чёрного, как в UI-ките. */
  ctaHover: '#c5271e',
  /** Текущий шаг мастера: Orange портала темнее на тон — белая жирная цифра 3,3:1 (крупный текст, от 3:1). */
  now: '#e8650c',
  /** Полоса «Готово N из M»: Sea Dark портала, как у «Совпадение 100 %» в UI-ките. */
  done: '#167c85',
  /** Ошибка на поле: тот же красный портала, 4,8:1 на белом. */
  err: '#db2b21',
} as const

/** Коды КПГЗ, ОКПД2, КТРУ, СПГЗ и подсказки клавиш. */
/** Стрелка выпадающего списка кита — тонкий шеврон вместо треугольника MUI; className задаёт MUI (поворот). */
const SelectChevron = (props: { className?: string }) => createElement(IconChevronDown, { size: 20, stroke: 1.5, ...props })

/** Список кита (03_Controls, «Регион поставки»): прямо под полем, рамка Gray Blue, пункт без заливки, наведённый — синий. */
const LIST_PAPER = {
  border: '1px solid #c9d1df',
  boxShadow: '0 10px 30px rgba(38, 75, 130, 0.1)',
  marginTop: '-1px',
}
// Пункт того же кегля, что текст поля (15 px при корне 16 px): список не крупнее поля, из которого открылся.
const LIST_ITEM = {
  minHeight: 40,
  padding: '9px 16px',
  fontSize: '0.9375rem',
  lineHeight: '1.375rem',
  whiteSpace: 'normal',
  backgroundColor: 'transparent',
  '&:hover, &.Mui-focused': { backgroundColor: 'transparent', color: TOKENS.acc },
  '&.Mui-focusVisible': { backgroundColor: TOKENS.accSoft },
  '&.Mui-selected, &[aria-selected="true"]': { backgroundColor: 'transparent', color: TOKENS.acc, fontWeight: 600 },
  '&.Mui-selected:hover, &.Mui-selected.Mui-focusVisible, &[aria-selected="true"].Mui-focused': { backgroundColor: TOKENS.accSoft },
}

export const MONO = "'IBM Plex Mono', ui-monospace, 'Cascadia Mono', Consolas, monospace"
const SANS = "'Open Sans Variable', 'Open Sans', system-ui, 'Segoe UI', Roboto, sans-serif"

export const theme = createTheme({
  palette: {
    primary: { main: TOKENS.acc, contrastText: '#ffffff' },
    info: { main: TOKENS.acc },
    error: { main: TOKENS.err },
    // Green портала `#0d9b68` — для значков (3,6:1, графике хватает 3:1); текст статусов — темнее.
    success: { main: '#0d9b68', dark: '#0b7f55' },
    // Orange портала `#f67319` даёт 2,8:1 на белом: мелкий текст статусов не проходит AA, темнее — 5,8:1.
    warning: { main: '#a14f00', dark: '#7a3b00' },
    text: { primary: TOKENS.ink, secondary: TOKENS.mute },
    divider: TOKENS.line,
    background: { default: TOKENS.accSoft, paper: '#ffffff' },
  },
  typography: {
    fontFamily: SANS,
    // Шрифт крупнее штатного MUI на 7 %: основной текст и поля 17 px, вторичный 15 px — ближе к шкале кита на
    // широком экране (p4 16–18 px), просьба пользователя 11.10.2026.
    fontSize: 15,
    fontWeightBold: 700,
    button: { textTransform: 'none', fontWeight: 600 },
    // Заголовок блока портала (h4 кита): Bold 20/24.
    h6: { fontSize: '1.25rem', lineHeight: '1.5rem', fontWeight: 700 },
    overline: { textTransform: 'none', letterSpacing: 0, fontWeight: 600, fontSize: '0.8125rem', lineHeight: 1.5 },
  },
  // Кнопки, поля, карточки и окна портала — без скругления. Явные '8px' в sx остались у мелких списков.
  shape: { borderRadius: 0 },
  components: {
    // На пальце цель не меньше 44 px (антислоп R-03): кнопки, иконки и вкладки выше только на сенсорных экранах.
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { '@media (pointer: coarse)': { minHeight: 44 } },
        sizeMedium: { minHeight: 40, paddingLeft: 20, paddingRight: 20 },
        sizeLarge: { minHeight: 48, paddingLeft: 20, paddingRight: 20, fontSize: '1.0625rem' },
        // Заливная кнопка портала — красная; синий остаётся у выбора, ссылок и рамок. В MUI 9 слотов
        // `containedPrimary` нет: цвет выбирается вариантом внутри слота.
        contained: {
          variants: [
            {
              props: { color: 'primary' },
              style: {
                backgroundColor: TOKENS.cta,
                '&:hover': { backgroundColor: TOKENS.ctaHover },
                // Неактивная заливная портала: Pale Blue и текст 50 % чёрного.
                '&.Mui-disabled': { backgroundColor: TOKENS.accSoft, color: 'rgba(0, 0, 0, 0.5)' },
              },
            },
          ],
        },
        // Кнопка с обводкой портала: белая, рамка Gray Blue, текст Main Blue; при наведении рамка синяя.
        outlined: {
          variants: [
            {
              props: { color: 'primary' },
              style: {
                backgroundColor: '#ffffff',
                borderColor: TOKENS.line,
                '&:hover': { borderColor: TOKENS.acc, backgroundColor: '#ffffff' },
              },
            },
          ],
        },
      },
    },
    MuiIconButton: { styleOverrides: { root: { borderRadius: '4px', '@media (pointer: coarse)': { minWidth: 44, minHeight: 44 } } } },
    MuiTab: { styleOverrides: { root: { '@media (pointer: coarse)': { minHeight: 44 } } } },
    MuiTabs: { styleOverrides: { root: { '@media (pointer: coarse)': { minHeight: 44 } } } },
    // Поверхности портала — белые блоки на голубом фоне, без теней и скруглений; меню и окна — с тенью портала.
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: { root: { backgroundImage: 'none' } },
    },
    MuiPopover: { styleOverrides: { paper: { boxShadow: '0 11px 70px rgba(38, 75, 130, 0.2)' } } },
    MuiMenu: { styleOverrides: { paper: { boxShadow: '0 11px 70px rgba(38, 75, 130, 0.2)' } } },
    MuiDialog: { styleOverrides: { paper: { boxShadow: '0 11px 70px rgba(38, 75, 130, 0.2)' } } },
    MuiAlert: { styleOverrides: { root: { borderRadius: 0 } } },
    MuiTableCell: { styleOverrides: { root: { borderBottomColor: TOKENS.line } } },
    MuiInputLabel: {
      defaultProps: { shrink: true },
      styleOverrides: {
        root: {
          position: 'static',
          transform: 'none',
          transition: 'none',
          maxWidth: '100%',
          whiteSpace: 'normal',
          overflow: 'visible',
          // Подпись над полем портала: SemiBold 16 Black (UI-кит, 03_Controls).
          fontSize: '1.0625rem',
          fontWeight: 600,
          lineHeight: 1.5,
          color: TOKENS.ink,
          marginBottom: 8,
          pointerEvents: 'auto',
          '&.MuiInputLabel-shrink': { transform: 'none' },
        },
      },
    },
    // Подсказка под полем портала — от края поля, без отступа MUI в 14 px.
    MuiFormHelperText: { styleOverrides: { contained: { marginLeft: 0, marginRight: 0 } } },
    MuiSelect: {
      defaultProps: {
        IconComponent: SelectChevron,
        MenuProps: {
          // Список открывается под полем, а не поверх него с выбранным пунктом на месте поля.
          anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
          transformOrigin: { vertical: 'top', horizontal: 'left' },
          slotProps: { paper: { sx: LIST_PAPER } },
        },
      },
      styleOverrides: { icon: { top: 'calc(50% - 10px)', right: 14, color: TOKENS.acc } },
    },
    MuiMenuItem: { styleOverrides: { root: LIST_ITEM } },
    // Подсказка портала: Main Blue, белый текст, без скругления.
    MuiTooltip: {
      styleOverrides: {
        tooltip: { backgroundColor: TOKENS.acc, color: '#ffffff', fontSize: '0.875rem', lineHeight: '1.125rem', padding: '10px 12px', borderRadius: 0, maxWidth: 320 },
        arrow: { color: TOKENS.acc },
      },
    },
    MuiAutocomplete: {
      styleOverrides: {
        paper: LIST_PAPER,
        // Пункты подсказок — как пункты списка кита; селектор длиннее штатного «listbox option», иначе он сильнее.
        listbox: { padding: 0, '& .MuiAutocomplete-option': LIST_ITEM },
      },
    },
    // Подсказка в пустом поле — серым кита, без полупрозрачности MUI: 4,5:1 на заливке поля.
    MuiInputBase: { styleOverrides: { input: { '&::placeholder': { color: TOKENS.mute, opacity: 1 } } } },
    MuiOutlinedInput: {
      defaultProps: { notched: false },
      styleOverrides: {
        // Поле кита (03_Controls, «Каталожный номер», «Регион поставки»): заливка Pale Blue без рамки, при наведении
        // темнее; рамка — только на фокусе (синяя) и ошибке (красная). Так и у текстовых полей, и у списков (решение
        // пользователя 11.10.2026). Заливка на белом — 1,2:1, ниже WCAG 1.4.11: поле узнаётся по подписи и заливке.
        root: {
          backgroundColor: TOKENS.accSoft,
          '&:hover:not(.Mui-disabled)': { backgroundColor: '#e1e8f1' },
          '&:not(.Mui-focused):not(.Mui-error) .MuiOutlinedInput-notchedOutline': { borderColor: 'transparent' },
        },
        notchedOutline: { '& legend': { display: 'none' } },
      },
    },
  },
})
