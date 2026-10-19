// Fields and paths come from the pending interrupt; values remain strings until
// the server validates them. In particular, never round currency through Number.
export function initialOrderValues(item) {
  return Object.fromEntries((item.fields || []).map(field => [field.path,
    field.value == null ? '' : typeof field.value === 'object' ? JSON.stringify(field.value) : String(field.value)]))
}

export function orderFormGroups(item) {
  const groups = new Map()
  for (const field of item.fields || []) {
    const oi = field.order_index
    if (!groups.has(oi)) groups.set(oi, { index: oi, fields: [], details: [] })
    const group = groups.get(oi)
    if (field.detail_index == null) group.fields.push(field)
    else {
      let detail = group.details.find(row => row.index === field.detail_index)
      if (!detail) {
        detail = { index: field.detail_index, fields: [] }
        group.details.push(detail)
      }
      detail.fields.push(field)
    }
  }
  return [...groups.values()]
}

function validDate(text) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || text.startsWith('0000')) return false
  const date = new Date(`${text}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === text
}

export function orderFormErrors(item, values) {
  const errors = {}
  for (const field of item.fields || []) {
    const text = String(values[field.path] ?? '').trim()
    let error = ''
    if (!text) {
      if (field.required) error = '请填写此字段'
    } else if (field.type === 'integer') {
      const number = Number(text)
      if (!/^\d+$/.test(text) || !Number.isSafeInteger(number) || number < (field.min ?? 1) || number > (field.max ?? Number.MAX_SAFE_INTEGER)) error = '请输入允许范围内的正整数'
    } else if (field.type === 'decimal') {
      if (!/^\d+(\.\d+)?$/.test(text)) error = '请输入大于或等于0的有效金额'
    } else if (field.type === 'date') {
      if (!validDate(text) || (field.min && text < field.min)) error = '请输入有效日期，且不能早于允许日期'
    } else if (field.type === 'datetime-local') {
      if (!validDate(text.slice(0, 10)) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(text) || !Number.isFinite(new Date(text).getTime()) || Number(text.slice(11, 13)) > 23 || Number(text.slice(14, 16)) > 59) error = '请输入有效日期和时间'
    }
    if (error) errors[field.path] = error
  }
  return errors
}

export function orderFormResponse(item, values, note = '', freeText = false) {
  const supplement = note.trim()
  if (freeText || !item.fields?.length) {
    if (!supplement) throw new Error('请填写补充说明')
    return { interrupt_id: item.interrupt_id, supplement }
  }
  if (Object.keys(orderFormErrors(item, values)).length) throw new Error('请检查表单中的字段')
  return { interrupt_id: item.interrupt_id, values: { ...values }, supplement }
}
