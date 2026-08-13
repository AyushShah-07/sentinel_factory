import api from './api'
import type { EventRecord } from '../types'

export const fetchEvents = async (): Promise<EventRecord[]> => {
  const response = await api.get<EventRecord[]>('/events')
  return response.data
}

export const fetchEventById = async (
  id: number
): Promise<EventRecord> => {
  const response = await api.get<EventRecord>(`/events/${id}`)
  return response.data
}