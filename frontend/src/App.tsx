import React, { useEffect } from 'react';
import { useAtom } from 'jotai';
import { 
    tasksAtom, 
    isLoadingTasksAtom, 
    isInitialLoadedAtom, 
    currentPageAtom 
} from './store';
import { TaskForm } from './components/TaskForm';
import { TaskList } from './components/TaskList';
import { ContextManager } from './components/ContextManager';
import { Cpu, LayoutDashboard, Settings, RefreshCw } from 'lucide-react';

export const App: React.FC = () => {
    const [tasks, setTasks] = useAtom(tasksAtom);
    const [isLoading, setIsLoading] = useAtom(isLoadingTasksAtom);
    const [isInitialLoaded, setIsInitialLoaded] = useAtom(isInitialLoadedAtom);
    const [currentPage, setCurrentPage] = useAtom(currentPageAtom);

    // Setup SSE Task Stream
    useEffect(() => {
        setIsLoading(true);
        const eventSource = new EventSource('http://localhost:8000/tasks/stream');
        
        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                setTasks(data);
                setIsLoading(false);
                setIsInitialLoaded(true);
            } catch (err) {
                console.error('Failed to parse SSE task data:', err);
                setIsLoading(false);
            }
        };

        eventSource.onerror = (err) => {
            console.error('Task stream SSE connection error:', err);
            setIsLoading(false);
        };

        return () => {
            eventSource.close();
        };
    }, [setTasks, setIsLoading, setIsInitialLoaded]);

    const fetchTasks = async () => {
        setIsLoading(true);
        try {
            const res = await fetch('http://localhost:8000/tasks');
            if (res.ok) {
                const data = await res.json();
                setTasks(data);
            }
        } catch (err) {
            console.error('Failed to manually fetch tasks:', err);
        } finally {
            setIsLoading(false);
            setIsInitialLoaded(true);
        }
    };

    return (
        <div className="container animate-fade-in">
            {/* Navigation and Title Header */}
            <header 
                style={{ 
                    display: 'flex', 
                    justifyContent: 'space-between', 
                    alignItems: 'center', 
                    marginBottom: '3rem', 
                    paddingBottom: '1.5rem', 
                    borderBottom: '1.5px solid var(--color-border)',
                    flexWrap: 'wrap',
                    gap: '1.5rem'
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
                    <div 
                        style={{
                            background: 'linear-gradient(135deg, var(--color-primary), #6366f1)',
                            padding: '0.6rem',
                            borderRadius: '12px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            boxShadow: '0 4px 12px rgba(59, 130, 246, 0.3)'
                        }}
                    >
                        <Cpu size={24} color="#ffffff" />
                    </div>
                    <h1 style={{ fontSize: '1.75rem', fontWeight: 800 }}>
                        LocalLLM <span style={{ background: 'linear-gradient(135deg, var(--color-primary), #818cf8)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>Agent</span>
                    </h1>
                </div>

                <nav style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
                    <span 
                        onClick={() => setCurrentPage('dashboard')}
                        style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            gap: '0.4rem',
                            cursor: 'pointer',
                            padding: '0.6rem 1rem',
                            borderRadius: '8px',
                            fontSize: '0.9rem',
                            fontWeight: 600,
                            transition: 'all 0.2s',
                            backgroundColor: currentPage === 'dashboard' ? 'var(--color-primary-soft)' : 'transparent',
                            color: currentPage === 'dashboard' ? 'var(--color-primary)' : 'var(--color-text-muted)',
                            border: currentPage === 'dashboard' ? '1px solid rgba(59, 130, 246, 0.15)' : '1px solid transparent'
                        }}
                        onMouseEnter={(e) => {
                            if (currentPage !== 'dashboard') {
                                e.currentTarget.style.color = 'var(--color-text-main)';
                                e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.03)';
                            }
                        }}
                        onMouseLeave={(e) => {
                            if (currentPage !== 'dashboard') {
                                e.currentTarget.style.color = 'var(--color-text-muted)';
                                e.currentTarget.style.backgroundColor = 'transparent';
                            }
                        }}
                    >
                        <LayoutDashboard size={16} /> Dashboard
                    </span>
                    <span 
                        onClick={() => setCurrentPage('contexts')}
                        style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            gap: '0.4rem',
                            cursor: 'pointer',
                            padding: '0.6rem 1rem',
                            borderRadius: '8px',
                            fontSize: '0.9rem',
                            fontWeight: 600,
                            transition: 'all 0.2s',
                            backgroundColor: currentPage === 'contexts' ? 'var(--color-primary-soft)' : 'transparent',
                            color: currentPage === 'contexts' ? 'var(--color-primary)' : 'var(--color-text-muted)',
                            border: currentPage === 'contexts' ? '1px solid rgba(59, 130, 246, 0.15)' : '1px solid transparent'
                        }}
                        onMouseEnter={(e) => {
                            if (currentPage !== 'contexts') {
                                e.currentTarget.style.color = 'var(--color-text-main)';
                                e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.03)';
                            }
                        }}
                        onMouseLeave={(e) => {
                            if (currentPage !== 'contexts') {
                                e.currentTarget.style.color = 'var(--color-text-muted)';
                                e.currentTarget.style.backgroundColor = 'transparent';
                            }
                        }}
                    >
                        <Settings size={16} /> Context Manager
                    </span>

                    <button 
                        className="secondary" 
                        onClick={fetchTasks} 
                        disabled={isLoading}
                        style={{ padding: '0.6rem 1rem', borderRadius: '8px' }}
                    >
                        <RefreshCw 
                            size={14} 
                            style={{ 
                                animation: isLoading ? 'spin 1.2s linear infinite' : undefined 
                            }} 
                        />
                        {isLoading ? 'Refreshing...' : 'Refresh Status'}
                    </button>
                </nav>
            </header>

            {/* Main dashboard body */}
            <main>
                {currentPage === 'dashboard' ? (
                    <div>
                        <TaskForm onTaskCreated={fetchTasks} />
                        
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '2rem 0 1rem 0' }}>
                            <h2 style={{ fontSize: '1.35rem', fontWeight: 700 }}>Task Dashboard Queue</h2>
                        </div>

                        {!isInitialLoaded ? (
                            <div 
                                className="glass-card"
                                style={{ 
                                    display: 'flex', 
                                    flexDirection: 'column',
                                    justifyContent: 'center', 
                                    alignItems: 'center', 
                                    padding: '5rem 2rem', 
                                    color: 'var(--color-text-muted)', 
                                    fontSize: '1rem', 
                                    gap: '1rem', 
                                    border: '1.5px dashed var(--color-border)', 
                                    borderRadius: '16px' 
                                }}
                            >
                                <span className="spinner" style={{ width: '1.75rem', height: '1.75rem', borderWidth: '3px' }}></span>
                                <span>Loading tasks history...</span>
                            </div>
                        ) : (
                            <TaskList tasks={tasks} onTasksUpdated={setTasks} />
                        )}
                    </div>
                ) : (
                    <ContextManager />
                )}
            </main>
        </div>
    );
};
export default App;
