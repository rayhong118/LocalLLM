import { LitElement } from 'lit';
import './task-form.ts';
import './task-list.ts';
export declare class AppRoot extends LitElement {
    static styles: import("lit").CSSResult;
    private _tasks;
    private _pollInterval?;
    connectedCallback(): void;
    disconnectedCallback(): void;
    private _fetchTasks;
    render(): import("lit-html").TemplateResult<1>;
}
declare global {
    interface HTMLElementTagNameMap {
        'app-root': AppRoot;
    }
}
