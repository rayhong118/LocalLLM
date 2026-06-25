import { atom } from 'jotai';

export interface TaskOutput {
    id: number;
    content: string;
    created_at: string;
}

export interface Task {
    id: number;
    prompt: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'DAILY' | 'CANCELLED';
    frequency: 'ONCE' | 'DAILY';
    hour_of_day: number | null;
    next_run_at: string | null;
    started_at: string | null;
    created_at: string;
    updated_at: string;
    outputs: TaskOutput[];
}

export interface Context {
    id: number;
    name: string;
    content: string;
    created_at: string;
}

export interface SavedTask {
    id: number;
    prompt: string;
    frequency: 'ONCE' | 'DAILY';
    hour_of_day: number | null;
    created_at: string;
}

export const tasksAtom = atom<Task[]>([]);
export const contextsAtom = atom<Context[]>([]);
export const savedTasksAtom = atom<SavedTask[]>([]);
export const isLoadingTasksAtom = atom<boolean>(false);
export const isInitialLoadedAtom = atom<boolean>(false);
export const currentPageAtom = atom<'dashboard' | 'contexts' | 'commonly_used'>('dashboard');
