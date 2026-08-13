import { useState, type FormEvent } from 'react'
import { sendAgentMessage } from '../services/agentService'
import { EmptyState } from '../components/common/EmptyState'
import './SafetyAgent.css'

export const SafetyAgent = () => {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<Array<{ role: string; text: string }>>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!question.trim()) return

    setLoading(true)
    setError(null)
    setMessages((current) => [...current, { role: 'user', text: question.trim() }])

    try {
      await sendAgentMessage(question.trim())
      setMessages((current) => [...current, { role: 'assistant', text: 'AI integration pending. Connect to backend agent API when available.' }])
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
      setQuestion('')
    }
  }

  return (
    <main className="page page--agent">
      <div className="page__header">
        <div>
          <h2>Sentinel Safety Agent</h2>
          <p>Ask questions about current safety events and alerts.</p>
        </div>
      </div>

      <section className="agent-panel">
        <div className="agent-panel__intro">
          <p>Suggested questions:</p>
          <ul>
            <li>Are there any active safety alerts?</li>
            <li>What was the latest detection?</li>
            <li>Which camera generated the latest alert?</li>
            <li>How many danger-zone events occurred?</li>
            <li>What is the current factory safety status?</li>
          </ul>
        </div>

        <form className="agent-form" onSubmit={handleSubmit}>
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask the Sentinel Safety Agent a question"
            aria-label="Agent question"
          />
          <button type="submit" disabled={loading}>
            Ask
          </button>
        </form>

        {error ? <div className="agent-error">{error}</div> : null}

        {messages.length === 0 ? (
          <EmptyState message="AI agent integration pending. No chat responses yet." />
        ) : (
          <div className="chat-history">
            {messages.map((message, index) => (
              <div key={index} className={`chat-message chat-message--${message.role}`}>
                <strong>{message.role === 'user' ? 'Operator' : 'Agent'}</strong>
                <p>{message.text}</p>
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}
