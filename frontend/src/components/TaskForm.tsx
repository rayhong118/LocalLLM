import React, { useState } from 'react';
import { Calendar, Clock, Sparkles, BookOpen, Plus, X } from 'lucide-react';
import { TaskFrequency, Context } from '../store';
import { ContextSelectModal } from './ContextSelectModal';

interface TaskFormProps {
  onTaskCreated: () => void;
}

export const TaskForm: React.FC<TaskFormProps> = ({ onTaskCreated }) => {
  const [loading, setLoading] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [frequency, setFrequency] = useState<TaskFrequency>('ONCE');
  const [hourOfDay, setHourOfDay] = useState<number>(new Date().getHours());
  const [dayOfWeek, setDayOfWeek] = useState<number>(new Date().getDay() === 0 ? 6 : new Date().getDay() - 1); // 0=Mon..6=Sun
  const [dayOfMonth, setDayOfMonth] = useState<number>(new Date().getDate()); // 1..31
  const [weekdayOnly, setWeekdayOnly] = useState(false);
  const [selectedContexts, setSelectedContexts] = useState<Context[]>([]);
  const [showContextModal, setShowContextModal] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    setLoading(true);
    try {
      const res = await fetch('http://localhost:8000/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt,
          frequency,
          hour_of_day: frequency !== 'ONCE' ? Number(hourOfDay) : null,
          day_of_week: frequency === 'WEEKLY' ? Number(dayOfWeek) : null,
          day_of_month: frequency === 'MONTHLY' ? Number(dayOfMonth) : null,
          weekday_only: frequency === 'DAILY' ? weekdayOnly : false,
          context_ids: selectedContexts.map((c) => c.id)
        })
      });
      if (res.ok) {
        setPrompt('');
        setWeekdayOnly(false);
        setSelectedContexts([]);
        onTaskCreated();
      }
    } catch (err) {
      console.error('Failed to schedule task:', err);
    } finally {
      setLoading(false);
    }
  };

  const removeContext = (contextId: number) => {
    setSelectedContexts(selectedContexts.filter((c) => c.id !== contextId));
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="glass-card form-container animate-fade-in"
      style={{ padding: '1.75rem', marginBottom: '2rem' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1rem' }}>
        <Sparkles size={20} color="var(--color-primary)" />
        <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Schedule New Task</h2>
      </div>

      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="e.g., Search for Häagen-Dazs deals on Safeway and list flavors..."
        style={{ minHeight: '110px', marginBottom: '0.85rem', width: '100%', resize: 'vertical' }}
        disabled={loading}
        required
      />

      {/* Context Attachment Section */}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1.25rem' }}>
        <span
          style={{
            fontSize: '0.8rem',
            color: 'var(--color-text-muted)',
            display: 'flex',
            alignItems: 'center',
            gap: '0.35rem',
            fontWeight: 600
          }}
        >
          <BookOpen size={14} color="var(--color-primary)" /> Attached Contexts:
        </span>

        {selectedContexts.map((ctx) => (
          <span
            key={ctx.id}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
              padding: '0.25rem 0.65rem',
              borderRadius: '999px',
              fontSize: '0.75rem',
              fontWeight: 600,
              backgroundColor: 'var(--color-primary-soft)',
              color: 'var(--color-primary-active)',
              border: '1px solid var(--color-primary-border)'
            }}
          >
            {ctx.name}
            <button
              type="button"
              onClick={() => removeContext(ctx.id)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--color-primary)',
                cursor: 'pointer',
                padding: '1px',
                display: 'flex',
                alignItems: 'center',
                borderRadius: '50%'
              }}
              title="Remove context"
            >
              <X size={12} />
            </button>
          </span>
        ))}

        <button
          type="button"
          className="secondary"
          onClick={() => setShowContextModal(true)}
          style={{
            padding: '0.25rem 0.65rem',
            fontSize: '0.75rem',
            borderRadius: '999px',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.25rem'
          }}
        >
          <Plus size={12} /> Add Context
        </button>
      </div>

      <ContextSelectModal
        isOpen={showContextModal}
        onClose={() => setShowContextModal(false)}
        alreadySelectedIds={selectedContexts.map((c) => c.id)}
        onAdd={(newCtxs) => {
          setSelectedContexts([...selectedContexts, ...newCtxs]);
        }}
        title="Add Context to New Task"
      />

      <div
        style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', marginBottom: '1.5rem', alignItems: 'flex-end' }}
      >
        <div style={{ flex: 1, minWidth: '180px' }}>
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              fontSize: '0.8rem',
              fontWeight: 600,
              marginBottom: '0.5rem',
              color: 'var(--color-text-muted)'
            }}
          >
            <Calendar size={14} /> Frequency
          </label>
          <select
            value={frequency}
            onChange={(e) => setFrequency(e.target.value as TaskFrequency)}
            disabled={loading}
            style={{ width: '100%' }}
          >
            <option value="ONCE">One-time (Run Now)</option>
            <option value="DAILY">Daily Recurring</option>
            <option value="WEEKLY">Weekly Recurring</option>
            <option value="MONTHLY">Monthly Recurring</option>
          </select>
        </div>

        {frequency === 'DAILY' && (
          <div style={{ display: 'flex', alignItems: 'center', height: '44px' }}>
            <label
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.55rem',
                cursor: 'pointer',
                fontSize: '0.85rem',
                fontWeight: 600,
                color: weekdayOnly ? 'var(--color-primary-active)' : 'var(--color-text-body)',
                background: weekdayOnly ? 'var(--color-primary-soft-hover)' : 'var(--color-bg-alt)',
                border: `1px solid ${weekdayOnly ? 'var(--color-primary)' : 'var(--color-border)'}`,
                padding: '0.65rem 0.95rem',
                borderRadius: '10px',
                transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
                userSelect: 'none'
              }}
            >
              <input
                type="checkbox"
                checked={weekdayOnly}
                onChange={(e) => setWeekdayOnly(e.target.checked)}
                disabled={loading}
                style={{
                  width: '16px',
                  height: '16px',
                  accentColor: 'var(--color-primary)',
                  cursor: 'pointer',
                  margin: 0
                }}
              />
              <span>Weekday only (Mon – Fri)</span>
            </label>
          </div>
        )}

        {frequency === 'WEEKLY' && (
          <div style={{ flex: 1, minWidth: '150px' }}>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                fontSize: '0.8rem',
                fontWeight: 600,
                marginBottom: '0.5rem',
                color: 'var(--color-text-muted)'
              }}
            >
              <Calendar size={14} /> Day of Week
            </label>
            <select
              value={dayOfWeek}
              onChange={(e) => setDayOfWeek(Number(e.target.value))}
              disabled={loading}
              style={{ width: '100%' }}
            >
              <option value={0}>Every Monday</option>
              <option value={1}>Every Tuesday</option>
              <option value={2}>Every Wednesday</option>
              <option value={3}>Every Thursday</option>
              <option value={4}>Every Friday</option>
              <option value={5}>Every Saturday</option>
              <option value={6}>Every Sunday</option>
            </select>
          </div>
        )}

        {frequency === 'MONTHLY' && (
          <div style={{ width: '130px' }}>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                fontSize: '0.8rem',
                fontWeight: 600,
                marginBottom: '0.5rem',
                color: 'var(--color-text-muted)'
              }}
            >
              <Calendar size={14} /> Day of Month (1-31)
            </label>
            <input
              type="number"
              min="1"
              max="31"
              value={dayOfMonth}
              onChange={(e) => setDayOfMonth(Number(e.target.value))}
              disabled={loading}
              style={{ width: '100%' }}
              required
            />
          </div>
        )}

        {frequency !== 'ONCE' && (
          <div style={{ width: '130px' }}>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                fontSize: '0.8rem',
                fontWeight: 600,
                marginBottom: '0.5rem',
                color: 'var(--color-text-muted)'
              }}
            >
              <Clock size={14} /> Hour (0-23)
            </label>
            <input
              type="number"
              min="0"
              max="23"
              value={hourOfDay}
              onChange={(e) => setHourOfDay(Number(e.target.value))}
              disabled={loading}
              style={{ width: '100%' }}
              required
            />
          </div>
        )}
      </div>

      <button
        type="submit"
        className="primary"
        disabled={loading || !prompt.trim()}
        style={{ width: '100%', padding: '0.85rem' }}
      >
        {loading ? (
          <>
            <span className="spinner"></span> Scheduling...
          </>
        ) : (
          <>
            <Sparkles size={16} /> Schedule Task
          </>
        )}
      </button>
    </form>
  );
};
