import React, { useState, useEffect, useMemo } from 'react';
import { marked } from 'marked';
import {
  Trash2,
  Play,
  RotateCcw,
  XCircle,
  Copy,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  CalendarDays,
  BookOpen,
  Plus,
  X,
  Edit3,
  BellOff,
  History
} from 'lucide-react';
import { Task, Context, TaskFrequency } from '../store';
import { ContextSelectModal } from './ContextSelectModal';
import { EditScheduleModal } from './EditScheduleModal';
import { WsdotMap, WsdotMapAlertItem } from './WsdotMap';

// Configure marked to open all output markdown links in a new tab
marked.use({
  renderer: {
    link(this: any, arg: any, title?: string, text?: string) {
      let href = '';
      let titleStr = '';
      let linkText = '';

      if (typeof arg === 'object' && arg !== null) {
        href = arg.href || '';
        titleStr = arg.title || '';
        linkText = arg.tokens && this?.parser?.parseInline ? this.parser.parseInline(arg.tokens) : (arg.text || href);
      } else {
        href = arg || '';
        titleStr = title || '';
        linkText = text || href;
      }

      const titleAttr = titleStr ? ` title="${titleStr}"` : '';
      return `<a href="${href}" target="_blank" rel="noopener noreferrer"${titleAttr}>${linkText}</a>`;
    }
  }
});

interface TaskItemProps {
  task: Task;
  onDelete: (id: number, needCancel: boolean) => void;
  onRetry: (id: number) => void;
  onRunNow: (id: number) => void;
  onCancel: (id: number) => void;
  onTaskUpdated?: (updatedTask: Task) => void;
  isRecurringRunning?: boolean;
  runStage?: 'IDLE' | 'STARTING' | 'QUEUED' | 'RUNNING';
}

export const TaskItem: React.FC<TaskItemProps> = ({
  task,
  onDelete,
  onRetry,
  onRunNow,
  onCancel,
  onTaskUpdated,
  isRecurringRunning = false,
  runStage
}) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [tzAbbreviation, setTzAbbreviation] = useState('');
  const [showContextModal, setShowContextModal] = useState(false);
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [isLocalStarting, setIsLocalStarting] = useState(false);
  const [isLocalRetrying, setIsLocalRetrying] = useState(false);

  useEffect(() => {
    if (runStage === 'RUNNING' || runStage === 'QUEUED' || runStage === 'IDLE') {
      setIsLocalStarting(false);
    }
  }, [runStage]);

  useEffect(() => {
    if (!isLocalStarting) return;
    const timer = setTimeout(() => setIsLocalStarting(false), 8000);
    return () => clearTimeout(timer);
  }, [isLocalStarting]);

  useEffect(() => {
    if (task.status === 'RUNNING' || task.status === 'SUCCESS' || task.status === 'FAILED') {
      setIsLocalRetrying(false);
    }
  }, [task.status]);

  useEffect(() => {
    if (!isLocalRetrying) return;
    const timer = setTimeout(() => setIsLocalRetrying(false), 8000);
    return () => clearTimeout(timer);
  }, [isLocalRetrying]);

  useEffect(() => {
    const tz =
      new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' })
        .formatToParts(new Date())
        .find((p) => p.type === 'timeZoneName')?.value || '';
    setTzAbbreviation(tz);
  }, []);

  const toLocalDate = (dateStr: string | null) => {
    if (!dateStr) return new Date();
    if (!dateStr.endsWith('Z') && !/[+-]\d{2}(?::?\d{2})?$/.test(dateStr)) {
      return new Date(dateStr + 'Z');
    }
    return new Date(dateStr);
  };

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(task.prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = task.prompt;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const handleRemoveContext = async (contextId: number) => {
    try {
      const res = await fetch(`http://localhost:8000/tasks/${task.id}/contexts/${contextId}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        const updated = await res.json();
        if (onTaskUpdated) onTaskUpdated(updated);
      }
    } catch (err) {
      console.error('Failed to remove context:', err);
    }
  };

  const handleAddContexts = async (newContexts: Context[]) => {
    try {
      const currentIds = (task.contexts || []).map((c) => c.id);
      const combinedIds = Array.from(new Set([...currentIds, ...newContexts.map((c) => c.id)]));
      const res = await fetch(`http://localhost:8000/tasks/${task.id}/contexts`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ context_ids: combinedIds })
      });
      if (res.ok) {
        const updated = await res.json();
        if (onTaskUpdated) onTaskUpdated(updated);
      }
    } catch (err) {
      console.error('Failed to add contexts to task:', err);
    }
  };

  const handleUpdateSchedule = async (updatedData: {
    prompt?: string;
    frequency: TaskFrequency;
    hour_of_day: number;
    weekday_only?: boolean;
    day_of_week?: number;
    day_of_month?: number;
    silent_if_no_result?: boolean;
    max_history_runs?: number | null;
  }) => {
    const res = await fetch(`http://localhost:8000/tasks/${task.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updatedData)
    });
    if (res.ok) {
      const updated = await res.json();
      if (onTaskUpdated) onTaskUpdated(updated);
      setShowScheduleModal(false);
    } else {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update schedule');
    }
  };

  const formatSchedule = (t: Task) => {
    const hh = String(t.hour_of_day ?? 0).padStart(2, '0') + ':00';
    if (t.frequency === 'DAILY') {
      return t.weekday_only ? `Every weekday (Mon-Fri) at ${hh}` : `Every day at ${hh}`;
    }
    if (t.frequency === 'WEEKLY') {
      const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
      const dayName = days[t.day_of_week ?? 0] || 'Monday';
      return `Every week on ${dayName} at ${hh}`;
    }
    if (t.frequency === 'MONTHLY') {
      const d = t.day_of_month ?? 1;
      const suffix =
        d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th';
      return `Day ${d}${suffix} of every month at ${hh}`;
    }
    return '';
  };

  const hasOutput = task.outputs && task.outputs.length > 0 && task.status !== 'RUNNING';
  const latestOutput = hasOutput ? task.outputs[task.outputs.length - 1].content : '';

  const { cleanMarkdown, mapAlerts } = useMemo(() => {
    if (!latestOutput) return { cleanMarkdown: '', mapAlerts: [] as WsdotMapAlertItem[] };
    const match = latestOutput.match(/```wsdot-map-data\s*([\s\S]*?)\s*```/);
    if (match) {
      try {
        const parsed = JSON.parse(match[1]);
        const cleaned = latestOutput.replace(/```wsdot-map-data\s*[\s\S]*?\s*```/, '').trim();
        return {
          cleanMarkdown: cleaned,
          mapAlerts: Array.isArray(parsed) ? (parsed as WsdotMapAlertItem[]) : []
        };
      } catch (e) {
        console.error('Failed to parse wsdot-map-data:', e);
      }
    }
    return { cleanMarkdown: latestOutput, mapAlerts: [] as WsdotMapAlertItem[] };
  }, [latestOutput]);

  const parsedHtml = useMemo(() => {
    if (!hasOutput || !cleanMarkdown) return '';
    return marked.parse(cleanMarkdown, { async: false, gfm: true, breaks: true }) as string;
  }, [hasOutput, cleanMarkdown]);

  const canEditContext = task.frequency !== 'ONCE' || task.status === 'PENDING' || task.status === 'FAILED';

  return (
    <div
      className={`glass-card task-card animate-fade-in ${task.status}`}
      style={{
        position: 'relative',
        padding: '1.25rem',
        border: '1px solid var(--color-border)',
        animation: task.status === 'RUNNING' ? 'pulseBorder 2.5s infinite ease-in-out' : undefined,
        borderWidth: task.status === 'RUNNING' ? '1.5px' : '1px'
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: '1.5rem',
          marginBottom: '0.85rem'
        }}
      >
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flex: 1, minWidth: 0 }}>
          <span
            className="status-badge"
            style={{
              padding: '0.25rem 0.75rem',
              borderRadius: '999px',
              fontSize: '0.7rem',
              fontWeight: 700,
              textTransform: 'uppercase',
              whiteSpace: 'nowrap',
              display: 'inline-block',
              backgroundColor:
                task.frequency === 'DAILY'
                  ? 'var(--color-primary-soft)'
                  : task.frequency === 'WEEKLY'
                    ? 'var(--color-accent-purple-bg)'
                    : task.frequency === 'MONTHLY'
                      ? 'rgba(16, 185, 129, 0.12)'
                      : task.status === 'SUCCESS' || task.status === 'COMPLETED'
                        ? 'var(--color-success-bg)'
                        : task.status === 'PARTIAL_SUCCESS'
                          ? 'var(--color-warning-bg)'
                          : task.status === 'NO_RESULT'
                            ? 'var(--color-neutral-bg)'
                            : task.status === 'RUNNING'
                              ? 'var(--color-warning-bg)'
                              : task.status === 'PENDING'
                                ? 'var(--color-primary-soft)'
                                : task.status === 'FAILED'
                                  ? 'var(--color-error-bg)'
                                  : 'var(--color-neutral-bg)',
              color:
                task.frequency === 'DAILY'
                  ? 'var(--color-primary-active)'
                  : task.frequency === 'WEEKLY'
                    ? 'var(--color-accent-purple)'
                    : task.frequency === 'MONTHLY'
                      ? '#047857'
                      : task.status === 'SUCCESS' || task.status === 'COMPLETED'
                        ? 'var(--color-success)'
                        : task.status === 'PARTIAL_SUCCESS'
                          ? '#b45309'
                          : task.status === 'NO_RESULT'
                            ? 'var(--color-text-muted)'
                            : task.status === 'RUNNING'
                              ? 'var(--color-warning)'
                              : task.status === 'PENDING'
                                ? 'var(--color-primary-active)'
                                : task.status === 'FAILED'
                                  ? 'var(--color-error)'
                                  : 'var(--color-neutral)'
            }}
          >
            {task.frequency !== 'ONCE'
              ? task.frequency === 'DAILY' && task.weekday_only
                ? 'WEEKDAYS'
                : task.frequency
              : task.status === 'PARTIAL_SUCCESS'
                ? 'PARTIAL SUCCESS'
                : task.status === 'NO_RESULT'
                  ? 'NO RESULT'
                  : task.status === 'PENDING'
                    ? runStage === 'STARTING' || isLocalStarting
                      ? 'STARTING'
                      : 'QUEUED'
                    : task.status}
          </span>
          {task.silent_if_no_result && (
            <span
              style={{
                padding: '0.2rem 0.55rem',
                borderRadius: '999px',
                fontSize: '0.68rem',
                fontWeight: 600,
                backgroundColor: 'var(--color-bg-alt)',
                color: 'var(--color-text-muted)',
                border: '1px solid var(--color-border)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.25rem',
                whiteSpace: 'nowrap'
              }}
              title="Silent on NO_RESULT: Telegram notification is skipped if 0 matching results are found"
            >
              <BellOff size={11} /> Silent on No Result
            </span>
          )}
          {Boolean(task.max_history_runs) && (
            <span
              style={{
                padding: '0.2rem 0.55rem',
                borderRadius: '999px',
                fontSize: '0.68rem',
                fontWeight: 600,
                backgroundColor: 'var(--color-bg-alt)',
                color: 'var(--color-text-muted)',
                border: '1px solid var(--color-border)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.25rem',
                whiteSpace: 'nowrap'
              }}
              title={`Keep latest ${task.max_history_runs} runs: older runs and log files are purged automatically`}
            >
              <History size={11} /> Keep {task.max_history_runs} runs
            </span>
          )}
          <span
            style={{
              fontWeight: 600,
              color: 'var(--color-text-body)',
              lineHeight: 1.4,
              fontSize: '0.95rem',
              wordBreak: 'break-word'
            }}
          >
            {task.prompt}
          </span>
        </div>

        {/* Task Actions */}
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexShrink: 0 }}>
          {task.frequency !== 'ONCE' && (
            <>
              <button
                className="secondary"
                style={{ fontSize: '0.75rem', padding: '0.35rem 0.75rem', borderRadius: '6px' }}
                onClick={(e) => {
                  e.stopPropagation();
                  setShowScheduleModal(true);
                }}
                title="Edit task prompt and schedule"
              >
                <Edit3 size={12} /> Edit Task
              </button>
              {(() => {
                const isStarting = runStage === 'STARTING' || isLocalStarting;
                const isQueued = !isStarting && runStage === 'QUEUED';
                const isRunning = !isStarting && !isQueued && (runStage === 'RUNNING' || isRecurringRunning);
                const isBusy = isStarting || isQueued || isRunning;

                return (
                  <button
                    className="secondary"
                    disabled={isBusy}
                    style={{
                      fontSize: '0.75rem',
                      padding: '0.35rem 0.75rem',
                      borderRadius: '6px',
                      opacity: isBusy ? 0.7 : 1,
                      cursor: isBusy ? 'not-allowed' : 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '0.35rem'
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!isBusy) {
                        setIsLocalStarting(true);
                        onRunNow(task.id);
                      }
                    }}
                    title={
                      isStarting
                        ? 'Starting task...'
                        : isQueued
                          ? 'Task is queued in database'
                          : isRunning
                            ? 'Task is currently running'
                            : 'Run task immediately'
                    }
                  >
                    {isStarting ? (
                      <>
                        <span className="spinner" style={{ width: 11, height: 11, borderWidth: '2px' }}></span>
                        <span>Starting task...</span>
                      </>
                    ) : isQueued ? (
                      <>
                        <Clock size={12} color="var(--color-primary-active)" />
                        <span>Queued...</span>
                      </>
                    ) : isRunning ? (
                      <>
                        <span className="spinner" style={{ width: 11, height: 11, borderWidth: '2px' }}></span>
                        <span>Task running...</span>
                      </>
                    ) : (
                      <>
                        <Play size={12} />
                        <span>Run Now</span>
                      </>
                    )}
                  </button>
                );
              })()}
            </>
          )}
          {(task.status === 'FAILED' || task.status === 'CANCELLED' || task.status === 'NO_RESULT') &&
            task.frequency === 'ONCE' && (
              <button
                className="primary"
                disabled={isLocalRetrying}
                style={{
                  fontSize: '0.75rem',
                  padding: '0.35rem 0.75rem',
                  borderRadius: '6px',
                  opacity: isLocalRetrying ? 0.7 : 1,
                  cursor: isLocalRetrying ? 'not-allowed' : 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.35rem'
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  if (!isLocalRetrying) {
                    setIsLocalRetrying(true);
                    onRetry(task.id);
                  }
                }}
                title={isLocalRetrying ? 'Starting task...' : 'Retry this task'}
              >
                {isLocalRetrying ? (
                  <>
                    <span className="spinner" style={{ width: 11, height: 11, borderWidth: '2px' }}></span>
                    <span>Starting task...</span>
                  </>
                ) : (
                  <>
                    <RotateCcw size={12} />
                    <span>Retry</span>
                  </>
                )}
              </button>
            )}
          {(task.status === 'COMPLETED' ||
            task.status === 'SUCCESS' ||
            task.status === 'PARTIAL_SUCCESS' ||
            task.status === 'NO_RESULT') && (
            <button
              className="secondary"
              style={{
                fontSize: '0.75rem',
                padding: '0.35rem 0.75rem',
                borderRadius: '6px',
                borderColor: copied ? 'var(--color-success)' : undefined,
                color: copied ? 'var(--color-success)' : undefined,
                background: copied ? 'var(--color-success-bg)' : undefined
              }}
              onClick={handleCopy}
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          )}
          {task.status === 'RUNNING' && (
            <button
              className="danger"
              style={{ fontSize: '0.75rem', padding: '0.35rem 0.75rem', borderRadius: '6px' }}
              onClick={(e) => {
                e.stopPropagation();
                onCancel(task.id);
              }}
            >
              <XCircle size={12} /> Cancel
            </button>
          )}
          <button
            className="secondary"
            style={{
              fontSize: '0.75rem',
              padding: '0.35rem 0.6rem',
              borderRadius: '6px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
            onClick={(e) => {
              e.stopPropagation();
              onDelete(task.id, task.status !== 'RUNNING');
            }}
            title="Delete Task"
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--color-error)';
              e.currentTarget.style.borderColor = 'rgba(220, 38, 38, 0.4)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = '';
              e.currentTarget.style.borderColor = '';
            }}
          >
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {/* Context Badges & Add Context Button */}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.75rem' }}>
        <span
          style={{
            fontSize: '0.75rem',
            color: 'var(--color-text-muted)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.3rem',
            fontWeight: 600
          }}
        >
          <BookOpen size={12} color="var(--color-primary)" /> Context:
        </span>
        {task.contexts && task.contexts.length > 0 ? (
          task.contexts.map((ctx) => (
            <span
              key={ctx.id}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.3rem',
                padding: '0.15rem 0.55rem',
                borderRadius: '999px',
                fontSize: '0.72rem',
                fontWeight: 600,
                backgroundColor: 'var(--color-primary-soft)',
                color: 'var(--color-primary-active)',
                border: '1px solid var(--color-primary-border)'
              }}
            >
              {ctx.name}
              {canEditContext && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleRemoveContext(ctx.id);
                  }}
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
                  title="Remove context from task"
                >
                  <X size={11} />
                </button>
              )}
            </span>
          ))
        ) : (
          <span style={{ fontSize: '0.72rem', color: 'var(--color-text-light)', fontStyle: 'italic' }}>None</span>
        )}

        {canEditContext && (
          <button
            className="secondary"
            onClick={(e) => {
              e.stopPropagation();
              setShowContextModal(true);
            }}
            style={{
              padding: '0.15rem 0.55rem',
              fontSize: '0.72rem',
              borderRadius: '999px',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.2rem',
              cursor: 'pointer',
              marginLeft: '0.2rem'
            }}
            title="Add context to this task"
          >
            <Plus size={11} /> Add Context
          </button>
        )}

        {canEditContext && (
          <ContextSelectModal
            isOpen={showContextModal}
            onClose={() => setShowContextModal(false)}
            alreadySelectedIds={(task.contexts || []).map((c) => c.id)}
            onAdd={handleAddContexts}
            title={`Add Context to Task #${task.id}`}
          />
        )}
      </div>

      {/* Time Stamp and Expandable Toggle */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.5rem'
        }}
      >
        <span
          style={{
            fontSize: '0.75rem',
            color: 'var(--color-text-muted)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.4rem',
            flexWrap: 'wrap'
          }}
        >
          {task.frequency !== 'ONCE' ? <Clock size={12} /> : <CalendarDays size={12} />}
          {task.frequency !== 'ONCE'
            ? `${formatSchedule(task)} ${tzAbbreviation}`
            : task.status === 'RUNNING'
              ? `Started: ${toLocalDate(task.started_at).toLocaleTimeString()} ${tzAbbreviation}`
              : task.status === 'PENDING'
                ? `${runStage === 'STARTING' || isLocalStarting ? 'Starting' : 'Queued at'}: ${toLocalDate(task.created_at).toLocaleTimeString()} ${tzAbbreviation}`
                : task.status === 'CANCELLED'
                  ? `Cancelled at: ${toLocalDate(task.updated_at).toLocaleTimeString()} ${tzAbbreviation}`
                  : `Last Run: ${toLocalDate(task.updated_at).toLocaleString()} ${tzAbbreviation}`}
        </span>

        {hasOutput && (
          <button
            style={{
              background: 'none',
              border: 'none',
              padding: '4px 8px',
              fontSize: '0.75rem',
              color: 'var(--color-info)',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.2rem',
              borderRadius: '4px',
              transition: 'color 0.2s'
            }}
            onClick={() => setIsExpanded(!isExpanded)}
            onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-primary)')}
            onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-info)')}
          >
            {isExpanded ? (
              <>
                Hide Result <ChevronUp size={12} />
              </>
            ) : (
              <>
                View Result <ChevronDown size={12} />
              </>
            )}
          </button>
        )}
      </div>

      {/* Collapsible output display with custom markdown renderer */}
      {isExpanded && hasOutput && (
        <div
          className="output-container"
          onClick={(e) => e.stopPropagation()}
          style={{
            marginTop: '1rem',
            padding: '1.25rem',
            background: 'var(--color-bg-alt)',
            borderRadius: '8px',
            border: '1px solid var(--color-border)',
            borderLeft: '4px solid var(--color-brand)',
            animation: 'slideDown 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards'
          }}
        >
          {mapAlerts && mapAlerts.length > 0 && (
            <WsdotMap alerts={mapAlerts} />
          )}

          <div
            className="markdown-body"
            onClick={(e) => {
              const target = (e.target as HTMLElement).closest('a');
              if (target && target.getAttribute('href')) {
                target.setAttribute('target', '_blank');
                target.setAttribute('rel', 'noopener noreferrer');
              }
            }}
            style={{
              fontSize: '0.95rem',
              lineHeight: 1.6,
              color: 'var(--color-text-body)',
              wordBreak: 'break-word',
              overflowWrap: 'break-word'
            }}
            dangerouslySetInnerHTML={{ __html: parsedHtml }}
          />
        </div>
      )}

      {task.frequency !== 'ONCE' && (
        <EditScheduleModal
          isOpen={showScheduleModal}
          onClose={() => setShowScheduleModal(false)}
          task={task}
          onSave={handleUpdateSchedule}
        />
      )}
    </div>
  );
};
