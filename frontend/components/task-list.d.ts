import { LitElement } from 'lit';
interface TaskOutput {
    id: number;
    content: string;
    created_at: string;
}
interface Task {
    id: number;
    prompt: string;
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
    created_at: string;
    updated_at: string;
    outputs: TaskOutput[];
}
export declare class TaskList extends LitElement {
    static styles: import("lit").CSSResult;
    tasks: Task[];
    render(): import("lit-html").TemplateResult<1>;
}
declare global {
    interface HTMLElementTagNameMap {
        'task-list': TaskList;
    }
}
export {};
