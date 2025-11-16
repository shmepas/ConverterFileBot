import React, { useState } from 'react';

export default function App() {
  const [form, setForm] = useState({ name: '', email: '', message: '' });
  const [response, setResponse] = useState({ message: '', type: '' });
  const [loading, setLoading] = useState(false);

  const handleChange = (e) => {
    setForm(prev => ({ ...prev, [e.target.name]: e.target.value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setResponse({ message: '', type: '' });

    if (!form.name || !form.email || !form.message) {
      setResponse({ message: 'Пожалуйста, заполните все поля.', type: 'error' });
      return;
    }

    setLoading(true);
    try {
      const res = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await res.json();

      if (res.ok) {
        setResponse({ message: data.message, type: 'success' });
        setForm({ name: '', email: '', message: '' });
      } else {
        setResponse({ message: data.error || 'Ошибка отправки.', type: 'error' });
      }
    } catch {
      setResponse({ message: 'Ошибка сети. Попробуйте позже.', type: 'error' });
    }
    setLoading(false);
  };

  return (
    <main style={{ maxWidth: 600, margin: '40px auto', padding: 20, color: '#eee', background: '#121212', borderRadius: 10 }}>
      <h1 style={{ color: '#2a9df4' }}>Добро пожаловать в бота-конвертер файлов</h1>
      <p>Этот бот умеет конвертировать аудио, видео, тексты и архивы в разные форматы прямо в Telegram.</p>
      <a
        href="https://t.me/ConverterFormatsBot"
        target="_blank"
        rel="noopener noreferrer"
        style={{
          display: 'inline-block',
          background: 'linear-gradient(45deg, #2a9df4, #1a7cd6)',
          color: 'white',
          padding: '18px 40px',
          borderRadius: 50,
          fontWeight: '700',
          fontSize: '1.2rem',
          textDecoration: 'none',
          boxShadow: '0 4px 15px rgba(42,157,244,0.6)',
          marginBottom: 50,
        }}
      >
        Запустить бота
      </a>

      <section aria-label="Форма обратной связи" style={{ background: '#222', padding: 30, borderRadius: 15, boxShadow: '0 0 30px rgba(42,157,244,0.3)' }}>
        <h2 style={{ color: '#2a9df4', marginBottom: 20 }}>Связаться с нами</h2>
        <form onSubmit={handleSubmit} noValidate>
          <label htmlFor="name">Ваше имя</label>
          <input
            type="text"
            id="name"
            name="name"
            value={form.name}
            onChange={handleChange}
            required
            style={{
              width: '100%',
              padding: '14px 18px',
              borderRadius: 10,
              border: 'none',
              marginBottom: 20,
              fontSize: '1rem',
              background: '#333',
              color: '#eee',
              boxShadow: 'inset 0 0 8px #1a7cd6',
            }}
          />

          <label htmlFor="email">Email</label>
          <input
            type="email"
            id="email"
            name="email"
            value={form.email}
            onChange={handleChange}
            required
            style={{
              width: '100%',
              padding: '14px 18px',
              borderRadius: 10,
              border: 'none',
              marginBottom: 20,
              fontSize: '1rem',
              background: '#333',
              color: '#eee',
              boxShadow: 'inset 0 0 8px #1a7cd6',
            }}
          />

          <label htmlFor="message">Сообщение</label>
          <textarea
            id="message"
            name="message"
            rows="4"
            value={form.message}
            onChange={handleChange}
            required
            style={{
              width: '100%',
              padding: '14px 18px',
              borderRadius: 10,
              border: 'none',
              marginBottom: 20,
              fontSize: '1rem',
              background: '#333',
              color: '#eee',
              boxShadow: 'inset 0 0 8px #1a7cd6',
              resize: 'vertical',
              minHeight: 44,
            }}
          />

          <button
            type="submit"
            disabled={loading}
            style={{
              background: 'linear-gradient(45deg, #2a9df4, #1a7cd6)',
              color: 'white',
              fontWeight: '700',
              padding: '16px 0',
              border: 'none',
              borderRadius: 50,
              cursor: 'pointer',
              fontSize: '1.2rem',
              width: '100%',
              boxShadow: '0 6px 20px rgba(42,157,244,0.7)',
              userSelect: 'none',
              minHeight: 44,
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? 'Отправка...' : 'Отправить'}
          </button>
        </form>
        {response.message && (
          <p
            role="alert"
            aria-live="polite"
            style={{
              marginTop: 20,
              fontWeight: '700',
              textAlign: 'center',
              color: response.type === 'success' ? '#4caf50' : '#f44336',
            }}
          >
            {response.message}
          </p>
        )}
      </section>
    </main>
  );
}
