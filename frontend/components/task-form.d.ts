import { LitElement } from 'lit';
export declare class TaskForm extends LitElement {
    static styles: import("lit").CSSResult;
    private loading;
    private textarea;
    private _handleSubmit;
    render(): import("lit-html").TemplateResult<1>;
}
declare global {
    interface HTMLElementTagNameMap {
        'task-form': TaskForm;
    }
}
