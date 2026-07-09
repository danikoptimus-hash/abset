import type { ThemeConfig } from 'antd'

// Единый источник цветов/шрифтов (FRONTEND.md §5.1) — копия темы Apache
// Superset с зеленым акцентом вместо синего/оранжевого. НИ ОДНОГО оранжевого
// цвета нигде в интерфейсе (бейджи/прогрессы/спиннеры/палитры ECharts) — см.
// src/charts/theme.ts для графиков.
export const colors = {
  primary: '#2E8B6D',
  primaryHover: '#256F57',
  primaryActive: '#1F5C46',
  success: '#2E8B6D',
  warning: '#C9A227',
  error: '#D64545',
  text: '#484848',
  border: '#E0E0E0',
  bgLayout: '#F7F7F7',
  tableHeaderText: '#666666',
  tableRowHover: '#FAFAFA',
} as const

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: colors.primary,
    colorSuccess: colors.success,
    colorWarning: colors.warning,
    colorError: colors.error,
    colorText: colors.text,
    colorBorder: colors.border,
    colorBgLayout: colors.bgLayout,
    // Дефолт AntD для ссылок — синий, которого нет в нашей палитре (UX-пакет,
    // п.1) — переопределяем на тот же зеленый, что и остальной primary-акцент.
    colorLink: colors.primary,
    colorLinkHover: colors.primaryHover,
    colorLinkActive: colors.primaryActive,
    fontFamily: 'Inter, -apple-system, Helvetica, Arial, sans-serif',
    fontSize: 14,
    borderRadius: 4,
  },
  components: {
    Table: {
      fontSize: 13,
      headerBg: colors.bgLayout,
      headerColor: colors.tableHeaderText,
      rowHoverBg: colors.tableRowHover,
    },
    Button: {
      colorPrimaryHover: colors.primaryHover,
      colorPrimaryActive: colors.primaryActive,
    },
  },
}
