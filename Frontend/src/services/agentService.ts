// Future AI agent integration point
// Backend route pending: POST /api/agent/chat

export interface AgentMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  text: string
}

export const sendAgentMessage = async (_message: string): Promise<AgentMessage[]> => {
  throw new Error('Agent integration pending. Connect to POST /api/agent/chat when available.')
}
