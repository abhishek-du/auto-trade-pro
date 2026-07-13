import { useState, useEffect, useRef, useCallback } from 'react'

const WELCOME_MSG = {
  id: 'welcome',
  role: 'assistant',
  content: "Namaste! I'm Avishk, your AI stock analyst. Ask me anything about NSE stocks — current price, technicals, buy/sell analysis, fundamentals, or recent news.\n\nTry: \"Should I buy Reliance now?\" or \"What's the RSI on TCS?\"",
  timestamp: new Date().toISOString(),
  isWelcome: true,
}

export const SUGGESTIONS = [
  "Should I buy HDFC Bank today?",
  "What's the technical analysis for Reliance?",
  "Is TCS overvalued at current price?",
  "What are the latest FII flows for NIFTY?",
  "Compare SBI vs ICICI Bank",
  "NIFTY 50 chart analysis",
  "Any news on Infosys?",
  "Best large cap stocks to buy now",
]

export function useStockChat() {
  const [messages, setMessages]           = useState([WELCOME_MSG])
  const [input, setInput]                 = useState('')
  const [loading, setLoading]             = useState(false)
  const [error, setError]                 = useState(null)
  const [activeContexts, setActiveContexts] = useState({})
  const [noAiBanner, setNoAiBanner]       = useState(false)
  const messagesEndRef                    = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = useCallback(async (text) => {
    const txt = typeof text === 'string' ? text : input
    if (!txt.trim() || loading) return

    const userMsg = {
      id:        Date.now().toString(),
      role:      'user',
      content:   txt.trim(),
      timestamp: new Date().toISOString(),
    }

    const assistantId = (Date.now() + 1).toString();

    setMessages(prev => [
      ...prev,
      userMsg,
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        reasoning: '',
        timestamp: new Date().toISOString(),
        isStreaming: true,
        streamingPhase: 'reasoning'
      }
    ])
    setInput('')
    setLoading(true)
    setError(null)

    const history = messages
      .filter(m => !m.isWelcome && m.role !== 'system')
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }))

    try {
      const token = localStorage.getItem('atp_admin_token')
      const headers = { 'Content-Type': 'application/json' }
      if (token) headers['Authorization'] = `Bearer ${token}`

      const res = await fetch((import.meta.env?.VITE_API_BASE || '') + '/api/v1/chat/stream', {
        method: 'POST',
        headers,
        body: JSON.stringify({ message: txt.trim(), history }),
      })

      if (!res.ok) throw new Error('Failed to start stream')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() || ''

        for (const block of lines) {
          if (!block.trim()) continue
          
          let eventType = 'message'
          let eventData = ''
          
          for (const line of block.split('\n')) {
            if (line.startsWith('event: ')) {
              eventType = line.substring(7).trim()
            } else if (line.startsWith('data: ')) {
              eventData = line.substring(6)
            }
          }

          if (!eventData) continue
          const parsedData = JSON.parse(eventData)

          setMessages(prev => prev.map(m => {
            if (m.id !== assistantId) return m

            if (eventType === 'reasoning') {
              return { ...m, reasoning: (m.reasoning || '') + parsedData.text, streamingPhase: 'reasoning' }
            } else if (eventType === 'content') {
              return { ...m, content: (m.content || '') + parsedData.text, streamingPhase: 'content' }
            } else if (eventType === 'meta') {
              if (parsedData.source === 'rule_based') setNoAiBanner(true)
              setActiveContexts(parsedData.contexts || {})
              return { ...m, contexts: parsedData.contexts, intent: parsedData.intent, symbols: parsedData.symbols, source: parsedData.source }
            } else if (eventType === 'done') {
              return { ...m, isStreaming: false, streamingPhase: 'done' }
            } else if (eventType === 'error') {
              return { ...m, content: parsedData.text, isError: true, isStreaming: false }
            }
            return m
          }))
        }
      }
    } catch (err) {
      setError('Failed to get response. Please try again.')
      setMessages(prev => prev.map(m =>
        m.id === assistantId ? { ...m, content: 'Sorry, I encountered an error. Please try again.', isError: true, isStreaming: false } : m
      ))
    } finally {
      setLoading(false)
      setMessages(prev => prev.map(m =>
        m.id === assistantId ? { ...m, isStreaming: false, streamingPhase: 'done' } : m
      ))
    }
  }, [input, loading, messages])

  const clearChat = useCallback(() => {
    setMessages([{ ...WELCOME_MSG, id: Date.now().toString(), content: 'Chat cleared. Ask me about any NSE stock!' }])
    setActiveContexts({})
    setError(null)
    setNoAiBanner(false)
  }, [])

  return {
    messages, input, setInput,
    loading, error, noAiBanner, setNoAiBanner,
    sendMessage, clearChat,
    activeContexts, messagesEndRef,
    suggestions: SUGGESTIONS,
  }
}
