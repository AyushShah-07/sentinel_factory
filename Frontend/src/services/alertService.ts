import api from './api'
import type { AlertRecord } from '../types'

export const fetchAlerts = async (): Promise<AlertRecord[]> => {
  const response = await api.get<AlertRecord[]>('/alerts')
  return response.data
}

export const fetchAlertById = async (id: number): Promise<AlertRecord> => {
  const response = await api.get<AlertRecord>(`/alerts/${id}`)
  return response.data
}
