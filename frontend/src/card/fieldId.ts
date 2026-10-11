/** id поля характеристики: по нему фрагмент текста переводит фокус на поле. */
export const fieldId = (name: string) => `field-${name.trim().replace(/\s+/g, '_')}`
