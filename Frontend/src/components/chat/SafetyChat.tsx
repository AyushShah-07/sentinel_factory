import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import './SafetyChat.css'

type ChatRole = 'user' | 'assistant'

type ChatMessage = {
  id: number
  role: ChatRole
  text: string
}

type ConnectionStatus = 'checking' | 'online' | 'offline'

const quickQuestions = [
  'How many danger incidents?',
  'Who is in danger zone?',
  'Show all incidents',
  "What's the dwell time?"
]

const formatErrorMessage = (message: string) => {
  if (message.includes('Failed to fetch')) {
    return 'Unable to reach the Safety Assistant API. Please verify the RAG service is running on localhost:8001.'
  }

  return message
}

export const SafetyChat = () => {
  const [isOpen, setIsOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 1,
      role: 'assistant',
      text: 'Hi! I can help with current factory safety data. Ask about incidents, danger zones, or dwell time.'
    }
  ])
  const [loading, setLoading] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('checking')
  const [errorText, setErrorText] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  const statusLabel = useMemo(() => {
    if (connectionStatus === 'online') return 'Connected'
    if (connectionStatus === 'offline') return 'Offline'
    return 'Checking'
  }, [connectionStatus])

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const response = await fetch('http://localhost:8001/status', {
          method: 'GET',
          headers: { Accept: 'application/json' }
        })

        if (!response.ok) {
          setConnectionStatus('offline')
          return
        }

        setConnectionStatus('online')
      } catch {
        setConnectionStatus('offline')
      }
    }

    checkStatus()
    const intervalId = window.setInterval(checkStatus, 15000)

    return () => window.clearInterval(intervalId)
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const sendQuestion = async (value: string) => {
    const trimmed = value.trim()
    if (!trimmed || loading) return

    const userMessage: ChatMessage = {
      id: Date.now(),
      role: 'user',
      text: trimmed
    }

    setMessages((current) => [...current, userMessage])
    setQuestion('')
    setLoading(true)
    setErrorText(null)

    try {
      const response = await fetch('http://localhost:8001/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json'
        },
        body: JSON.stringify({ question: trimmed })
      })

      const data = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(data?.detail || 'The Safety Assistant request failed.')
      }

      const answer = typeof data?.answer === 'string' ? data.answer : 'No answer was returned by the Safety Assistant.'

      setMessages((current) => [
        ...current,
        {
          id: Date.now() + 1,
          role: 'assistant',
          text: answer
        }
      ])
    } catch (error) {
      const message = error instanceof Error ? formatErrorMessage(error.message) : 'Something went wrong while contacting the Safety Assistant.'
      setErrorText(message)
      setMessages((current) => [
        ...current,
        {
          id: Date.now() + 2,
          role: 'assistant',
          text: `I couldn’t answer that right now. ${message}`
        }
      ])
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await sendQuestion(question)
  }

  return (
    <div className="safety-chat">
      <button
        type="button"
        className="safety-chat__launch"
        aria-label="Open Safety Assistant"
        onClick={() => setIsOpen((current) => !current)}
      >
        <span className="safety-chat__launch-icon">💬</span>
      </button>

      {isOpen ? (
        <div className="safety-chat__panel" role="dialog" aria-live="polite" aria-label="Safety Assistant chat">
          <header className="safety-chat__header">
            <div>
              <p className="safety-chat__eyebrow">Safety Assistant</p>
              <h3>Factory AI support</h3>
            </div>
            <div className={`safety-chat__status safety-chat__status--${connectionStatus}`}>
              <span className="safety-chat__dot" />
              {statusLabel}
            </div>
          </header>

          <div className="safety-chat__quick-questions">
            {quickQuestions.map((item) => (
              <button key={item} type="button" className="safety-chat__quick-button" onClick={() => sendQuestion(item)}>
                {item}
              </button>
            ))}
          </div>

          <div className="safety-chat__messages">
            {messages.map((message) => (
              <div key={message.id} className={`safety-chat__message safety-chat__message--${message.role}`}>
                <span className="safety-chat__message-role">{message.role === 'user' ? 'You' : 'Assistant'}</span>
                <p>{message.text}</p>
              </div>
            ))}
            {loading ? (
              <div className="safety-chat__loading" aria-live="polite">
                <span />
                <span />
                <span />
              </div>
            ) : null}
            <div ref={messagesEndRef} />
          </div>

          {errorText ? <div className="safety-chat__error">{errorText}</div> : null}

          <form className="safety-chat__form" onSubmit={handleSubmit}>
            <input
              type="text"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask about danger incidents..."
              aria-label="Ask the safety assistant"
            />
            <button type="submit" disabled={loading || !question.trim()}>
              Send
            </button>
          </form>
        </div>
      ) : null}
    </div>
  )
}
