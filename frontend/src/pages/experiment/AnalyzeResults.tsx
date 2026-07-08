import { Typography, Tag, Space, Card, Alert, Row, Col } from 'antd'
import { ForestPlotChart } from '../../charts/ForestPlotChart'
import { DistributionChart } from '../../charts/DistributionChart'
import { CumulativeLiftChart } from '../../charts/CumulativeLiftChart'
import { HelpCollapse } from './HelpCollapse'
import { DetailedResultsTable } from './DetailedResultsTable'
import type { AnalysisResultsOut, TestResultOut } from './analyzeTypes'
import { resultsByMetric, verdict } from './analyzeTypes'

const VERDICT_LABELS: Record<string, string> = {
  significant_positive: 'значимо позитивный',
  significant_negative: 'значимо негативный',
  no_effect_detected: 'эффект не обнаружен',
}
const VERDICT_COLORS: Record<string, string> = {
  significant_positive: 'success',
  significant_negative: 'error',
  no_effect_detected: 'default',
}

function VerdictCards({ results }: { results: TestResultOut[] }) {
  const designed = results.filter((r) => r.is_designed_method)
  const byMetric = resultsByMetric(designed)
  return (
    <Row gutter={16} style={{ marginBottom: 24 }}>
      {Object.entries(byMetric).map(([metric, rows]) =>
        rows.map((r) => {
          const v = verdict(r)
          return (
            <Col key={`${metric}_${r.treatment_group}`}>
              <Card size="small" style={{ minWidth: 220 }}>
                <Typography.Text strong>{metric}</Typography.Text>
                <br />
                <Typography.Text type="secondary">{r.treatment_group} vs control</Typography.Text>
                <br />
                <Tag color={VERDICT_COLORS[v]} style={{ marginTop: 8 }}>
                  {VERDICT_LABELS[v]}
                </Tag>
                <Typography.Paragraph style={{ marginTop: 4, marginBottom: 0 }}>
                  {(r.effect_rel * 100).toFixed(1)}% [{(r.ci_rel[0] * 100).toFixed(1)}%, {(r.ci_rel[1] * 100).toFixed(1)}%]
                </Typography.Paragraph>
              </Card>
            </Col>
          )
        }),
      )}
    </Row>
  )
}

export function AnalyzeResults({ data, experimentName }: { data: AnalysisResultsOut; experimentName: string }) {
  const byMetric = resultsByMetric(data.results)
  const { checks } = data.chart_data

  return (
    <div>
      {data.global_warnings.length > 0 && (
        <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
          {data.global_warnings.map((w, i) => (
            <Alert key={i} type="warning" showIcon message={w} />
          ))}
        </Space>
      )}

      <VerdictCards results={data.results} />

      <Typography.Title level={5}>Проверки честности (на пост-данных)</Typography.Title>
      <Space wrap style={{ marginBottom: 8 }}>
        {checks.srm && (
          <Tag color={checks.srm.passed ? 'success' : 'error'}>
            SRM: {checks.srm.passed ? 'OK' : 'провалена'} (p={checks.srm.p_value.toExponential(2)})
          </Tag>
        )}
        {checks.loss && (
          <Tag color={checks.loss.symmetric ? 'success' : 'error'}>
            Потери данных: {checks.loss.symmetric ? 'симметричны' : 'асимметричны'}
          </Tag>
        )}
      </Space>
      <HelpCollapse chartType="srm_table" table />

      {Object.entries(byMetric).map(([metricName, metricResults]) => {
        const metricChart = data.chart_data.metrics[metricName]
        return (
          <div key={metricName} style={{ marginTop: 32 }}>
            <Typography.Title level={4}>{metricName}</Typography.Title>

            <Typography.Title level={5}>Forest plot</Typography.Title>
            <ForestPlotChart
              rows={metricResults.map((r) => ({
                label: `${r.method} (${r.treatment_group})`,
                effectRelPct: r.effect_rel * 100,
                ciLoPct: r.ci_rel[0] * 100,
                ciHiPct: r.ci_rel[1] * 100,
                highlighted: r.is_designed_method,
              }))}
            />
            <HelpCollapse chartType="forest" />

            {metricChart &&
              Object.entries(metricChart.distributions).map(([treatName, dist]) => (
                <div key={treatName}>
                  <Typography.Title level={5}>
                    Распределение: {metricChart.control_name} vs {treatName}
                  </Typography.Title>
                  <DistributionChart distribution={dist} controlName={metricChart.control_name} treatName={treatName} />
                  <HelpCollapse chartType={dist.kind === 'binary' ? 'distribution_binary' : 'distribution_continuous'} />
                </div>
              ))}

            {metricChart &&
              Object.entries(metricChart.daily).map(([treatName, points]) => (
                <div key={treatName}>
                  <Typography.Title level={5}>
                    Кумулятивный лифт: {metricChart.control_name} vs {treatName}
                  </Typography.Title>
                  <CumulativeLiftChart points={points} />
                  <HelpCollapse chartType="cumulative_lift" />
                </div>
              ))}

            {metricChart &&
              Object.entries(metricChart.segments).map(([treatName, segs]) => (
                <div key={treatName}>
                  <Typography.Title level={5}>
                    По сегментам: {metricChart.control_name} vs {treatName}{' '}
                    <Tag>exploratory</Tag>
                  </Typography.Title>
                  <ForestPlotChart
                    rows={segs.map((s) => ({
                      label: s.stratum,
                      effectRelPct: s.effect_rel * 100,
                      ciLoPct: s.ci_rel[0] * 100,
                      ciHiPct: s.ci_rel[1] * 100,
                      highlighted: false,
                    }))}
                  />
                  <HelpCollapse chartType="segment_forest" />
                </div>
              ))}
          </div>
        )
      })}

      <Typography.Title level={4} style={{ marginTop: 32 }}>
        Детальная таблица результатов
      </Typography.Title>
      <DetailedResultsTable
        results={data.results}
        controlName={Object.values(data.chart_data.metrics)[0]?.control_name ?? 'control'}
        correction={data.correction ?? 'none'}
        experimentName={experimentName}
      />
      <HelpCollapse chartType="verdicts_table" table />
    </div>
  )
}
