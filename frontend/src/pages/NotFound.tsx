import { Button, Paper, Typography } from '@mui/material'
import { go } from '../lib/router'
import { Page } from '../shell/Page'

export function NotFound() {
  return (
    <Page title="Страница не найдена">
      <Paper variant="outlined" className="p-6">
        <Typography color="text.secondary">Такого адреса в модуле нет. Начните с формы создания СТЕ.</Typography>
        <Button variant="contained" className="mt-4" onClick={() => go('/')}>
          Создать СТЕ
        </Button>
      </Paper>
    </Page>
  )
}
