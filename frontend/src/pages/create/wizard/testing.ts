// Ответы модуля для тестов мастера: только поля, которые читают модели шагов.
import type { AutofillOut, Candidate, Chain, KtruState } from '../../../api/v1.ts'
import type { FieldState } from '../formModel.ts'

export const candidate = (code: string, name: string, over: Partial<Candidate> = {}): Candidate => ({
  code,
  name,
  score: 0,
  why: [],
  similar: null,
  examples: [],
  matched: [],
  missing: [],
  conflicts: [],
  ...over,
})

export const state = (over: Partial<KtruState> = {}): KtruState => ({
  candidates: [],
  remaining: 0,
  final_code: null,
  question: null,
  steps: [],
  trail: [],
  ...over,
})

export const chain = (over: Partial<Chain> = {}): Chain => ({
  category: '01.15.02.01',
  category_name: 'Бумага для офисной техники',
  kpgz: '01.15.02.01',
  okpd2: '17.12.14.129',
  spgz: [],
  ktru_linked: 2,
  ktru: null,
  path: [],
  ktru_merged: [],
  ...over,
})

export const out = (ktru: KtruState, over: Partial<AutofillOut> = {}): AutofillOut => ({
  session_id: 's1',
  ktru,
  characteristics: [],
  issues: [],
  degraded: [],
  chain: null,
  twins: [],
  extra: [],
  unread: [],
  suggestions: [],
  name: '',
  description: '',
  llm: 'on',
  ...over,
})

export const field = (over: Partial<FieldState>): FieldState => ({
  name: 'Формат',
  value: '',
  initial: '',
  unit: null,
  required: false,
  options: [],
  source: null,
  status: 'not_found',
  normalizedFrom: null,
  method: null,
  note: null,
  own: null,
  group: null,
  unitName: null,
  compact: null,
  origin: 'portal',
  extra: false,
  hint: null,
  ...over,
})
