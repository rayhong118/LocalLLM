import { atom } from 'jotai';

export interface TaskOutput {
    id: number;
    content: string;
    created_at: string;
}

export type TaskFrequency = 'ONCE' | 'DAILY' | 'WEEKLY' | 'MONTHLY';

export interface Task {
    id: number;
    prompt: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'SUCCESS' | 'PARTIAL_SUCCESS' | 'NO_RESULT' | 'FAILED' | 'DAILY' | 'CANCELLED';
    frequency: TaskFrequency;
    hour_of_day: number | null;
    day_of_week?: number | null;
    day_of_month?: number | null;
    next_run_at: string | null;
    started_at: string | null;
    created_at: string;
    updated_at: string;
    outputs: TaskOutput[];
    contexts?: Context[];
}

export interface Context {
    id: number;
    name: string;
    content: string;
    created_at: string;
}

export const tasksAtom = atom<Task[]>([]);
export const contextsAtom = atom<Context[]>([]);
export const isLoadingTasksAtom = atom<boolean>(false);
export const isInitialLoadedAtom = atom<boolean>(false);
export const currentPageAtom = atom<'dashboard' | 'contexts'>('dashboard');
