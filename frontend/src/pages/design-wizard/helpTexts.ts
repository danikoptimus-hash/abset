// Тексты перенесены из app.py (_render_design_intro и соседние константы) —
// FRONTEND.md §5.2 шаг 1: "экспандеры-подсказки (перенести существующие тексты)".

export const DESIGN_EXAMPLE_ROWS = [
  { user_id: 'u_00001', platform: 'ios', country: 'RU', segment: 'premium', converted_pre_30d: 1, revenue_pre_30d: 1240, sessions_pre_30d: 12 },
  { user_id: 'u_00002', platform: 'android', country: 'UZ', segment: 'free', converted_pre_30d: 0, revenue_pre_30d: 0, sessions_pre_30d: 3 },
  { user_id: 'u_00003', platform: 'ios', country: 'KZ', segment: 'premium', converted_pre_30d: 1, revenue_pre_30d: 890, sessions_pre_30d: 8 },
  { user_id: 'u_00004', platform: 'android', country: 'RU', segment: 'free', converted_pre_30d: 0, revenue_pre_30d: 0, sessions_pre_30d: 1 },
  { user_id: 'u_00005', platform: 'web', country: 'UZ', segment: 'premium', converted_pre_30d: 1, revenue_pre_30d: 2100, sessions_pre_30d: 15 },
  { user_id: 'u_00006', platform: 'ios', country: 'RU', segment: 'free', converted_pre_30d: 0, revenue_pre_30d: 340, sessions_pre_30d: 5 },
]

export const DESIGN_SQL_EXAMPLE = `SELECT
    user_id,
    any(platform) as platform,
    any(country) as country,
    any(segment) as segment,
    -- бинарные pre-period метрики
    max(if(event = 'purchase', 1, 0)) as converted_pre_30d,
    -- continuous pre-period метрики
    sum(if(event = 'purchase', revenue, 0)) as revenue_pre_30d,
    count(distinct session_id) as sessions_pre_30d
FROM events
WHERE date >= today() - 30 AND date < today()
GROUP BY user_id`

export const WHAT_IS_THIS_DATA = `Это snapshot вашей базы пользователей **ПЕРЕД** тестом — те, кого вы потенциально включите в эксперимент.

**Формат:** одна строка = один пользователь.

**Что должно быть в файле:**
- Колонка с ID пользователя (обязательно, уникальная)
- Признаки для стратификации: платформа, страна, сегмент, тариф и т.д. (желательно — иначе группы не будут сбалансированы)
- Pre-period метрики: те же метрики, что будете мерить в тесте, но за период ДО теста (желательно — без них не работает CUPED и точный расчет MDE)`

export const EXAMPLE_EXPLANATION = `- **user_id** — уникальный идентификатор (обязательно)
- **platform, country, segment** — признаки для стратификации (можно любые категориальные, чем больше — тем лучше баланс)
- **converted_pre_30d** — бинарная pre-period метрика (0/1) для будущего анализа конверсии
- **revenue_pre_30d** — continuous pre-period метрика для выручки
- **sessions_pre_30d** — количество сессий, нужно для ratio-метрик вроде revenue/sessions`

export const SQL_EXPLANATION = `Замените \`event = 'purchase'\` на ваше событие конверсии. Период (30 дней) выбирайте так, чтобы он был осмысленным для вашего продукта — типичное окно принятия решения.`

export const NO_DATA_EXPLANATION = `Нажмите кнопку **«Демо-данные»**. Программа сгенерирует синтетический датасет на 5000 пользователей с реалистичной структурой (разные платформы, страны, сегменты, pre-period метрики) и проведет вас через весь воркфлоу — от дизайна до отчета анализа. Это лучший способ разобраться, как работает инструмент.`

export const SPLIT_METHOD_LABELS: Record<string, string> = {
  stratified: 'stratified — сплит внутри каждой страты отдельно (лучший баланс групп)',
  simple: 'simple — случайный сплит без учета страт (largest remainder)',
  hash: 'hash — детерминированный сплит по sha256(salt + unit_id), не гарантирует баланс страт',
}

export const ISOLATION_LABELS: Record<string, string> = {
  exclude: 'exclude — исключить участников всех активных тестов (рекомендуется)',
  warn: 'warn — показать пересечение и спросить подтверждение',
  off: 'off — не исключать никого (осознанный риск пересечения)',
  exclude_selected: 'exclude_selected — исключить участников только выбранных тестов',
}

export const NAN_STRATEGY_LABELS: Record<string, string> = {
  separate_stratum: "Выделить в отдельную страту 'unknown' (по умолчанию)",
  drop: 'Удалить юзеров с пропусками',
  error: 'Считать ошибкой дизайна',
}

export const SIZE_MODE_LABELS: Record<string, string> = {
  mde_rel: 'Задать целевой относительный MDE',
  mde_abs: 'Задать целевой абсолютный MDE',
  sample_size: 'Задать размер выборки',
  all: 'Использовать все доступные данные',
}

export const GROUP_PRESETS: Record<string, Record<string, number>> = {
  '50/50': { control: 0.5, treatment: 0.5 },
  '90/10': { control: 0.9, treatment: 0.1 },
  '33/33/33': { control: 0.34, treatment_a: 0.33, treatment_b: 0.33 },
}
