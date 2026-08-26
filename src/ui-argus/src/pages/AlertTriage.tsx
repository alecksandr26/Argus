import { useParams } from 'react-router-dom'
import PageStub from './PageStub'

export default function AlertTriage() {
  const { alertId } = useParams()
  return (
    <PageStub
      title={`Triage de alerta ${alertId ? `#${alertId}` : ''}`}
      description="Torre de Control — detalle de una alerta: media capturada, confianza del modelo, revisión y notas del operador (operator_notes / reviwed_by_operator)."
    />
  )
}
