import assert from 'node:assert/strict'
import { test } from 'node:test'
import { NOTHING_FITS, OTHER } from '../ktruView.ts'
import { categoryNote, categoryQuestion, ktruEffect, likelyOption, optionsOf, questionText, shownTwins, twinNote } from './options.ts'
import { candidate, state } from './testing.ts'

const CONFIRM = 'Позиция КТРУ'

test('тип товара: имя и объяснения; недоступные метаданные не подменяются кодом категории', () => {
  const paper = candidate('01.15.02.01', 'Бумага для офисной техники', {
    similar: 0.6996,
    similar_count: 14,
    similar_total: 30,
    examples: [{ id: 47453345, name: 'Бумага офисная Снегурочка А4' }],
    matched: ['бумага'],
    missing: ['плотность'],
    conflicts: ['цвет'],
  })
  const cards = candidate('01.15.02.02', 'Бумага для заметок')
  const s = state({
    candidates: [paper, cards],
    remaining: 2,
    question: {
      characteristic: CONFIRM,
      options: [
        { value: paper.code, remaining: 1, cuts: 1, codes: [paper.code] },
        { value: cards.code, remaining: 1, cuts: 1, codes: [cards.code] },
        { value: NOTHING_FITS, remaining: 0, cuts: 2, codes: [] },
      ],
    },
  })
  const [first, second, ...rest] = optionsOf(s, 'category')
  assert.equal(rest.length, 0)
  assert.deepEqual([first.title, first.codes], ['Бумага для офисной техники', ['ОКПД2: сведения не загружены']])
  assert.deepEqual(first.reasons, [{ label: 'Совпало', values: ['бумага'] }, { label: 'Нет в тексте', values: ['плотность'] }, { label: 'Противоречит', values: ['цвет'] }])
  assert.deepEqual(second.reasons, [])
})

test('КТРУ: значение, коды и что останется', () => {
  const s = state({
    remaining: 6,
    question: {
      characteristic: 'Цветность',
      options: [
        { value: 'Белая', remaining: 1, cuts: 5, codes: ['17.12.14.129-00000031'] },
        { value: 'Цветная', remaining: 3, cuts: 3, codes: ['K1', 'K2', 'K3'] },
        { value: 'Серая', remaining: 5, cuts: 1, codes: ['K4', 'K5', 'K6'] },
        { value: OTHER, remaining: 6, cuts: 0, codes: [] },
        { value: NOTHING_FITS, remaining: 0, cuts: 6, codes: [] },
      ],
    },
  })
  const v = optionsOf(s, 'ktru')
  assert.deepEqual(v.map((x) => x.title), ['Белая', 'Цветная', 'Серая', 'Другое значение'])
  assert.deepEqual(v.map((x) => x.effect), ['останется 1 позиция', 'останется 3 позиции', 'останется 5 позиций', ''])
  assert.deepEqual(v[0].codes, ['17.12.14.129-00000031'])
  assert.equal(v[3].hint, 'выбор не сузит, модуль спросит другое')
  assert.equal(v[0].hint, null)
})

test('без вопроса вариантов нет', () => {
  assert.deepEqual(optionsOf(state(), 'category'), [])
})

const opt = (value: string) => ({ value, remaining: 1, cuts: 1, codes: [value] })

test('шаг 2: длинный перечень характеристики заменяется выбором категории', () => {
  const a = candidate('01.15.02.08', 'Дипломы, грамоты, благодарности')
  const b = candidate('01.15.01.05', 'Фотобумага')
  const values = ['Грамота', 'Диплом', 'Сертификат', 'Буклет', 'Листовка', 'Программка', 'Абонемент']
  const long = state({
    candidates: [a, b],
    remaining: 2,
    question: { characteristic: 'Вид товара', options: [...values.map(opt), opt(''), opt(NOTHING_FITS)] },
  })
  const q = categoryQuestion(long).question!
  assert.equal(q.characteristic, CONFIRM)
  assert.deepEqual(q.options.map((o) => o.value), [a.code, b.code, NOTHING_FITS])
  assert.deepEqual(q.options[0].codes, [a.code])
})

test('шаг 2: короткий вопрос о характеристике и выбор позиции не меняются', () => {
  const a = candidate('K1', 'Бумага белая')
  const b = candidate('K2', 'Бумага цветная')
  const short = state({ candidates: [a, b], remaining: 2, question: { characteristic: 'Цветность', options: [opt('Белая'), opt('Цветная'), opt(NOTHING_FITS)] } })
  assert.equal(categoryQuestion(short), short)
  const choose = state({ candidates: [a, b], remaining: 2, question: { characteristic: CONFIRM, options: [opt('K1'), opt('K2')] } })
  assert.equal(categoryQuestion(choose), choose)
})

test('заголовок шага 2: выбрать категорию или подтвердить одну', () => {
  const many = state({ remaining: 2, question: { characteristic: CONFIRM, options: [opt('A'), opt('B'), opt(NOTHING_FITS)] } })
  assert.equal(questionText(many, 'category').title, 'Какой это товар по КТРУ?')
  const one = state({ remaining: 1, steps: ['Проверьте: в наименовании есть слова, которых нет у категории.'], question: { characteristic: CONFIRM, options: [opt('A'), opt(NOTHING_FITS)] } })
  assert.deepEqual(questionText(one, 'category'), {
    title: 'Подтвердите тип товара',
    why: 'Проверьте: в наименовании есть слова, которых нет у категории.',
  })
})

test('заголовок шага 3: уточнить характеристику и сколько позиций КТРУ', () => {
  const s = state({ remaining: 2, question: { characteristic: 'Цветность', options: [opt('Белая'), opt('Цветная')] } })
  assert.deepEqual(questionText(s, 'ktru'), {
    title: 'Цветность: что подходит вашему товару?',
    why: 'От ответа зависит позиция КТРУ: сейчас подходят 2 позиции.',
  })
  const yes = state({ remaining: 5, question: { characteristic: 'Регулировка по высоте', options: [opt('Да'), opt('Нет'), opt('')] } })
  assert.equal(questionText(yes, 'ktru').title, 'Регулировка по высоте: да или нет?')
  const choose = state({ remaining: 3, question: { characteristic: CONFIRM, options: [opt('A'), opt('B'), opt('C')] } })
  assert.equal(questionText(choose, 'ktru').title, 'Выберите позицию КТРУ')
})

test('категория по такой же СТЕ портала: её номер; модель или чужая категория — не повод', () => {
  assert.equal(twinNote([{ id: 5001, by: 'name', same_category: true }]), 'Такая же СТЕ есть на портале: №5001')
  assert.equal(twinNote([{ id: 5002, by: 'model', same_category: true }]), null)
  assert.equal(twinNote([{ id: 5003, by: 'name', same_category: false }]), null)
})

test('блок «такая СТЕ уже есть»: только по наименованию и пока проверка видит дубль', () => {
  const twin = (id: number, by: 'name' | 'model', same = true) => ({ id, name: `СТЕ ${id}`, url: `u${id}`, by, same_category: same })
  const twins = [twin(1, 'name'), twin(2, 'model'), twin(3, 'name', false)]
  assert.deepEqual(shownTwins(twins, [{ kind: 'duplicate' }]).map((t) => t.id), [1])
  assert.deepEqual(shownTwins(twins, [{ kind: 'moderation_rule' }]), [])  // наименование поправили
})

test('совпадение не округляется вверх до порога: 69,96 % — ещё не 70 %', () => {
  assert.equal(categoryNote(candidate('A', 'Бумага', { similar: 0.7 })), 'Совпадение 70 % с похожими СТЕ портала')
  assert.equal(categoryNote(candidate('A', 'Бумага', { similar: 0.6996 })), 'Совпадение 69 % с похожими СТЕ портала')
  assert.equal(categoryNote(candidate('A', 'Бумага', { why: ['бумага'] })), 'Определена по словам: бумага')
  assert.equal(categoryNote(candidate('A', 'Бумага')), null)
})

test('одинаковые позиции КТРУ объединены уже в подписи типа', () => {
  assert.equal(ktruEffect({ ktru_total: 2, ktru_distinct: 1 }, 2), '1 позиция КТРУ: из 2 одинаковые объединены')
  assert.equal(ktruEffect({ ktru_total: 3, ktru_distinct: 3 }, 3), '3 позиции КТРУ')
  assert.equal(ktruEffect(undefined, null), 'Число позиций не загружено')
  assert.equal(ktruEffect(undefined, 0), 'Нет в КТРУ: карточка пойдёт с категорией портала')
})

test('самый вероятный тип по модели отмечен заранее', () => {
  const base = state({ question: { characteristic: 'Позиция КТРУ', options: [{ value: 'A', remaining: 1, cuts: 0, codes: ['A'] }, { value: NOTHING_FITS, remaining: 0, cuts: 1, codes: [] }] } })
  assert.equal(likelyOption(base), null)
  assert.equal(likelyOption({ ...base, steps: ['Вероятнее всего это «A» — поставлен первым.'] }), 'A')
})
