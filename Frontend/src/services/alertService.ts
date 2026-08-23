// =========================================================
// alertService.ts - API calls for Alert data
// =========================================================

import api from './api'
import type { AlertRecord } from '../types'

// Fetches all alerts from backend
// GET /api/alerts
// Returns: List of AlertRecord (matches Alert.java)
export const fetchAlerts = async (): Promise<AlertRecord[]> => {
  const response = await api.get<AlertRecord[]>('/alerts')
  return response.data
}

// Fetches a single alert by ID
// GET /api/alerts/{id}
// Returns: Single AlertRecord
export const fetchAlertById = async (id: number): Promise<AlertRecord> => {
  const response = await api.get<AlertRecord>(`/alerts/${id}`)
  return response.data
}