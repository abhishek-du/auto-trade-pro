import { useState, useEffect, useRef, useCallback } from 'react'
import { apiFetch } from '../api/client'

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

    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)
    setError(null)

    const history = messages
      .filter(m => !m.isWelcome && m.role !== 'system')
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }))

    try {
      const data = await apiFetch('/api/v1/chat/message', {
        method: 'POST',
        body:   JSON.stringify({ message: txt.trim(), history }),
      })

      if (data.source === 'rule_based') setNoAiBanner(true)

      const assistantMsg = {
        id:        (Date.now() + 1).toString(),
        role:      'assistant',
        content:   data.reply,
        timestamp: data.timestamp,
        contexts:  data.contexts,
        symbols:   data.symbols,
        intent:    data.intent,
        source:    data.source,
      }
      setMessages(prev => [...prev, assistantMsg])
      if (data.contexts && Object.keys(data.contexts).length) {
        setActiveContexts(data.contexts)
      }
    } catch (err) {
      setError('Failed to get response. Please try again.')
      setMessages(prev => [...prev, {
        id:        (Date.now() + 1).toString(),
        role:      'assistant',
        content:   'Sorry, I encountered an error. Please try again.',
        isError:   true,
        timestamp: new Date().toISOString(),
      }])
    } finally {
      setLoading(false)
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
