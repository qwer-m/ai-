/**
 * Standard API Testing page scoped styles.
 * Keep visual rules local to this workspace to avoid leaking global side effects.
 */
export function StandardApiTestingPageStyles() {
  return (
    <style>{`
      .standard-api-shell {
        --sat-bg: var(--ui-bg, #f6f8fc);
        --sat-surface: var(--ui-surface, #ffffff);
        --sat-surface-muted: var(--ui-surface-muted, #f8fafc);
        --sat-border: var(--ui-border, #dbe4f0);
        --sat-border-strong: var(--ui-border-strong, #cbd5e1);
        --sat-text: var(--ui-text, #0f172a);
        --sat-text-soft: var(--ui-text-soft, #667085);
        --sat-primary: var(--ui-primary, #4f46e5);
        --sat-primary-soft: rgba(79, 70, 229, 0.12);
        --sat-shadow: var(--ui-shadow-sm, 0 4px 16px rgba(15, 23, 42, 0.08));
        background: var(--sat-bg) !important;
        color: var(--sat-text);
      }

      body.theme-dark .standard-api-shell {
        --sat-bg: var(--ui-bg, #0b1220);
        --sat-surface: var(--ui-surface, #111827);
        --sat-surface-muted: var(--ui-surface-muted, #0f172a);
        --sat-border: var(--ui-border, #314055);
        --sat-border-strong: var(--ui-border-strong, #3f5168);
        --sat-text: var(--ui-text, #e2e8f0);
        --sat-text-soft: var(--ui-text-soft, #94a3b8);
        --sat-primary: var(--ui-primary, #818cf8);
        --sat-primary-soft: rgba(129, 140, 248, 0.18);
        --sat-shadow: 0 6px 20px rgba(2, 6, 23, 0.52);
      }

      .standard-api-shell .bg-white {
        background: var(--sat-surface) !important;
      }

      .standard-api-shell .bg-light {
        background: var(--sat-surface-muted) !important;
      }

      .standard-api-shell .text-dark {
        color: var(--sat-text) !important;
      }

      .standard-api-shell .no-caret::after { display: none !important; }
      .standard-api-shell .dropdown-menu {
        border-radius: 10px !important;
        border: 1px solid var(--sat-border);
        background: var(--sat-surface);
        box-shadow: var(--sat-shadow);
      }

      .standard-api-shell .standard-api-sidebar,
      .standard-api-shell .standard-api-workspace,
      .standard-api-shell .standard-api-response-panel {
        background: var(--sat-surface) !important;
        border-color: var(--sat-border) !important;
      }

      .standard-api-shell .standard-api-response-shell {
        flex: 1;
        min-height: 0;
      }

      .standard-api-shell .standard-api-sidebar-head,
      .standard-api-shell .standard-api-sidebar-body,
      .standard-api-shell .standard-api-toolbar,
      .standard-api-shell .standard-api-tabbar,
      .standard-api-shell .standard-api-response-head {
        background: var(--sat-surface-muted) !important;
        border-color: var(--sat-border) !important;
      }

      .standard-api-shell .standard-api-tabbar-row {
        min-height: 45px;
      }

      .standard-api-shell .standard-api-sidebar h6,
      .standard-api-shell .standard-api-sidebar .text-secondary,
      .standard-api-shell .standard-api-cookie-btn,
      .standard-api-shell .custom-nav-tabs .nav-link {
        color: var(--sat-text-soft) !important;
      }

      .standard-api-shell .api-tree-item { position: relative; min-width: 0; }
      .standard-api-shell .api-tree-drop-wrap {
        transition: all 0.2s;
        border-top: 1px solid transparent;
        border-bottom: 1px solid transparent;
        border-left: 1px solid transparent;
        border-right: 1px solid transparent;
      }

      .standard-api-shell .api-tree-drop-top {
        border-top: 2px solid #dc3545;
      }

      .standard-api-shell .api-tree-drop-bottom {
        border-bottom: 2px solid #dc3545;
      }

      .standard-api-shell .api-tree-drop-middle {
        border: 1px solid var(--sat-primary);
        background: color-mix(in srgb, var(--sat-primary-soft) 70%, transparent);
      }

      .standard-api-shell .api-tree-item .d-flex,
      .standard-api-shell .api-tree-item .text-truncate,
      .standard-api-shell .list-group,
      .standard-api-shell .list-group-item {
        min-width: 0;
        overflow-x: hidden;
      }

      .standard-api-shell .api-tree-item.api-tree-item-selected::after {
        content: '';
        position: absolute;
        inset: 2px;
        background: var(--sat-primary-soft);
        border: 1px solid color-mix(in srgb, var(--sat-primary) 35%, var(--sat-border));
        border-radius: 8px;
        pointer-events: none;
      }

      .standard-api-shell .api-tree-method-col { width: 56px; flex: 0 0 56px; text-align: center; margin-right: 14px; }
      .standard-api-shell .api-tree-method-text {
        font-size: 0.75rem;
        font-weight: 700;
        display: block;
        text-align: left;
        white-space: nowrap;
        color: var(--api-method-color, var(--sat-text-soft));
      }

      .standard-api-shell .api-tree-indent {
        width: calc(var(--api-tree-depth, 0) * 6px);
        flex-shrink: 0;
      }

      .standard-api-shell .api-tree-bulk-check {
        width: 18px;
        height: 18px;
      }

      .standard-api-shell .api-tree-folder-toggle {
        width: 19px;
        height: 18px;
        cursor: pointer;
        margin-left: -7px;
      }

      .standard-api-shell .api-tree-rename-input {
        padding: 0 0.25rem;
        height: auto;
      }

      .standard-api-shell .api-tree-actions {
        opacity: 0;
        transition: opacity 0.2s;
      }

      .standard-api-shell .api-tree-actions.is-visible {
        opacity: 1;
      }

      .standard-api-shell .api-tree-menu {
        z-index: 1050;
      }

      .standard-api-shell .api-tree-children {
        padding-left: 0;
        margin-left: calc(var(--api-tree-depth, 0) * 6px + 12px);
      }
      .standard-api-shell .api-tree-icon-slot { width: 14px; flex: 0 0 14px; margin-right: 4px; display: inline-flex; align-items: center; justify-content: center; }

      .standard-api-shell .api-method-get { color: #198754 !important; }
      .standard-api-shell .api-method-post { color: #8b5a2b !important; }
      .standard-api-shell .api-method-put { color: #6f42c1 !important; }
      .standard-api-shell .api-method-delete { color: #a61e2b !important; }
      .standard-api-shell .api-method-other { color: #6c757d !important; }

      .standard-api-shell .api-sidebar-resizer {
        width: 10px;
        cursor: col-resize;
        background: color-mix(in srgb, var(--sat-surface-muted) 85%, transparent);
        border-left: 1px solid var(--sat-border);
      }
      .standard-api-shell .api-sidebar-resizer:hover {
        background: var(--sat-primary-soft);
      }

      .standard-api-shell .standard-api-url-bar,
      .standard-api-shell .standard-api-request-config,
      .standard-api-shell .standard-api-response-content {
        background: var(--sat-surface) !important;
        border-color: var(--sat-border) !important;
      }

      .standard-api-shell .standard-api-method-select {
        color: var(--sat-text);
        border-right: 1px solid var(--sat-border) !important;
      }

      .standard-api-shell .standard-api-path-input::placeholder,
      .standard-api-shell .custom-api-input::placeholder {
        color: var(--sat-text-soft) !important;
        font-size: 12px;
      }

      .standard-api-shell .standard-api-path-input {
        caret-color: var(--sat-text);
        position: relative;
        z-index: 1;
        font-family: inherit;
      }

      .standard-api-shell .standard-api-path-input--filled {
        color: transparent;
      }

      .standard-api-shell .standard-api-path-input:focus,
      .standard-api-shell .standard-api-method-select:focus,
      .standard-api-shell .form-control:focus,
      .standard-api-shell .form-select:focus {
        box-shadow: 0 0 0 3px var(--sat-primary-soft) !important;
        border-color: var(--sat-primary) !important;
      }

      .standard-api-shell .standard-api-env-popup {
        border-color: var(--sat-border) !important;
        background: var(--sat-surface) !important;
      }

      .standard-api-shell .standard-api-send-btn {
        background: linear-gradient(135deg, var(--sat-primary), color-mix(in srgb, var(--sat-primary) 82%, #111827)) !important;
        border-color: var(--sat-primary) !important;
        border-radius: 10px !important;
      }

      .standard-api-shell .standard-api-save-btn,
      .standard-api-shell .standard-api-cookie-btn {
        border-radius: 10px !important;
      }

      .standard-api-shell .custom-nav-tabs .nav-link {
        border: none;
        background: transparent !important;
        display: inline-flex;
        align-items: center;
        padding-bottom: 8px;
        border-bottom: 2px solid transparent;
        border-radius: 0;
        font-weight: 500;
      }

      .standard-api-shell .custom-nav-tabs .nav-link:focus,
      .standard-api-shell .custom-nav-tabs .nav-link:focus-visible {
        outline: none;
        box-shadow: none;
      }

      .standard-api-shell .custom-nav-tabs .nav-link:hover {
        color: color-mix(in srgb, var(--sat-text) 72%, var(--sat-text-soft)) !important;
      }

      .standard-api-shell .custom-nav-tabs .nav-link.active {
        color: var(--sat-primary) !important;
        background: transparent !important;
        border-bottom: 2px solid var(--sat-primary);
      }

      .standard-api-shell .standard-api-splitter {
        border-top-color: var(--sat-border) !important;
        background: var(--sat-surface-muted);
      }

      .standard-api-shell .standard-api-splitter-handle {
        height: 6px;
        cursor: row-resize;
        user-select: none;
      }

      .standard-api-shell .standard-api-request-config-resizable {
        height: var(--sat-request-height, 320px);
        min-height: 100px;
        overflow: hidden;
      }

      .standard-api-shell .standard-api-sidebar-resizable {
        width: var(--sat-sidebar-width, 260px);
        min-width: var(--sat-sidebar-width, 260px);
        overflow: hidden;
        transition: width 0.2s ease, min-width 0.2s ease, opacity 0.2s ease;
        opacity: 1;
      }

      .standard-api-shell .standard-api-sidebar-resizable.is-closed {
        width: 0;
        min-width: 0;
        opacity: 0;
      }

      .standard-api-shell .standard-api-sidebar-resizable.is-resizing {
        transition: none;
      }

      .standard-api-shell .standard-api-splitter.is-dragging {
        background: color-mix(in srgb, var(--sat-primary-soft) 70%, var(--sat-surface-muted));
      }

      .standard-api-shell .standard-api-response-loading {
        background: color-mix(in srgb, var(--sat-surface) 75%, transparent) !important;
      }

      .standard-api-shell .standard-api-toolbar {
        min-height: 50px;
      }

      .standard-api-shell .standard-api-method-select {
        width: 110px;
        background-color: color-mix(in srgb, var(--sat-surface-muted) 88%, transparent);
        border-right: 1px solid var(--sat-border) !important;
        font-weight: 600;
      }

      .standard-api-shell .standard-api-path-highlight {
        white-space: pre;
        overflow: hidden;
        pointer-events: none;
        font: inherit;
        color: var(--sat-text);
        padding-left: 0;
        padding-right: 0;
      }

      .standard-api-shell .standard-api-path-text {
        color: var(--sat-text);
      }

      .standard-api-shell .standard-api-env-chip {
        border-radius: 4px;
        padding: 0 2px;
        margin: 0 1px;
        font-size: 1em;
        line-height: 1.6;
        border: 1px solid var(--sat-border);
      }

      .standard-api-shell .standard-api-env-chip--empty {
        border-color: color-mix(in srgb, #f59e0b 45%, var(--sat-border));
        color: color-mix(in srgb, #b45309 82%, var(--sat-text));
      }

      .standard-api-shell .standard-api-env-chip--missing {
        background: color-mix(in srgb, #ef4444 14%, var(--sat-surface));
        border-color: color-mix(in srgb, #ef4444 38%, var(--sat-border));
        color: #dc2626;
        font-weight: 600;
      }

      .standard-api-shell .standard-api-env-chip--ok {
        color: var(--sat-primary);
      }

      .standard-api-shell .standard-api-env-popup {
        top: 100%;
        z-index: 1050;
        margin-top: 4px;
      }

      .standard-api-shell .standard-api-env-popup-input-wrap {
        background: var(--sat-surface);
        border-color: var(--sat-border) !important;
      }

      .standard-api-shell .standard-api-env-label {
        font-weight: 500;
      }

      .standard-api-shell .standard-api-send-btn,
      .standard-api-shell .standard-api-save-btn,
      .standard-api-shell .standard-api-env-manage-btn {
        font-weight: 500;
      }

      .standard-api-shell .standard-api-tab-ai {
        font-weight: 500;
      }

      .standard-api-shell .standard-api-format-toggle,
      .standard-api-shell .standard-api-preview-btn {
        font-weight: 600;
      }

      .standard-api-shell .standard-api-format-glyph {
        width: 34px;
      }

      .standard-api-shell .standard-api-format-menu {
        min-width: 200px;
      }

      .standard-api-shell .standard-api-body-tab-wrap,
      .standard-api-shell .standard-api-body-content,
      .standard-api-shell .standard-api-report-tab-wrap {
        min-height: 0;
      }

      .standard-api-shell .standard-api-body-toolbar-divider {
        height: 16px;
      }

      .standard-api-shell .standard-api-response-iframe {
        width: 100%;
        height: 100%;
        border: none;
      }

      .standard-api-shell .standard-api-json-view {
        white-space: pre-wrap;
        word-wrap: break-word;
        overflow: auto;
        user-select: text;
        background: var(--sat-surface);
      }

      .standard-api-shell .standard-api-response-textarea {
        resize: none;
        outline: none;
        color: var(--sat-text);
        opacity: 1;
        background: var(--sat-surface);
      }

      .standard-api-shell .standard-api-report-card {
        background: var(--sat-surface);
        border-color: var(--sat-border) !important;
      }

      .standard-api-shell .standard-api-report-pre {
        white-space: pre-wrap;
        word-break: break-word;
        font-family: inherit;
      }

      .standard-api-shell .standard-api-report-pre--raw {
        word-break: break-all;
      }

      .standard-api-shell .standard-api-report-tip {
        max-width: 400px;
      }

      .standard-api-shell .standard-api-panel-scroll {
        min-height: 0;
      }

      .standard-api-shell .standard-api-fw-600 {
        font-weight: 600;
      }

      .standard-api-shell .standard-api-fw-500 {
        font-weight: 500;
      }

      .standard-api-shell .standard-api-report-failure-name {
        max-width: 300px;
      }

      .standard-api-shell .standard-api-raw-textarea {
        resize: none;
        outline: none;
      }

      .standard-api-shell .standard-api-kv-table {
        table-layout: fixed;
      }

      .standard-api-shell .standard-api-kv-table th {
        font-weight: 500;
      }

      .standard-api-shell .standard-api-kv-col-key,
      .standard-api-shell .standard-api-kv-col-value {
        width: 30%;
      }

      .standard-api-shell .standard-api-kv-col-desc {
        width: 40%;
      }

      .standard-api-shell .standard-api-fd-col-key {
        width: 25%;
      }

      .standard-api-shell .standard-api-fd-col-type {
        width: 10%;
      }

      .standard-api-shell .standard-api-fd-col-value {
        width: 30%;
      }

      .standard-api-shell .standard-api-fd-col-desc {
        width: 35%;
      }

      .standard-api-shell .standard-api-fd-type-select {
        font-size: 0.875rem;
      }

      .standard-api-shell .standard-api-file-name {
        max-width: 100px;
      }

      .standard-api-shell .standard-api-kv-desc-input {
        padding-right: 24px;
      }

      .standard-api-shell .standard-api-kv-remove-btn {
        z-index: 5;
      }

      .standard-api-shell .standard-api-table-key {
        font-weight: 600;
      }

      .standard-api-shell .standard-api-cookie-value {
        max-width: 200px;
      }

      .standard-api-shell .standard-api-error-trace {
        white-space: pre-wrap;
        background: var(--sat-surface);
      }

      .standard-api-shell .standard-api-env-modal-body {
        min-height: 400px;
      }

      .standard-api-shell .standard-api-env-var-list {
        max-height: 300px;
        background: var(--sat-surface-muted);
      }

      .standard-api-shell .standard-api-cookie-modal-body {
        min-height: 300px;
        max-height: 600px;
        overflow-y: auto;
      }

      .standard-api-shell .standard-api-cookie-op-col {
        width: 60px;
      }

      .standard-api-shell .standard-api-response-metrics .standard-api-metric-value {
        font-weight: 600;
      }

      .standard-api-shell .standard-api-hidden-input {
        display: none;
      }

      .standard-api-shell .standard-api-import-overlay {
        z-index: 100;
        pointer-events: none;
        border: 2px dashed var(--sat-primary);
      }

      .standard-api-shell .standard-api-import-overlay-card {
        background: var(--sat-surface);
        font-weight: 600;
      }

      .standard-api-shell .standard-api-sidebar-title {
        font-weight: 600;
      }

      .standard-api-shell .standard-api-sidebar-scroll {
        min-height: 100px;
        overflow-y: auto;
        overflow-x: hidden;
      }

      .standard-api-shell .standard-api-sidebar-empty {
        top: 100px;
        left: 0;
        pointer-events: none;
      }

      .standard-api-shell .standard-api-scroll-pane {
        visibility: visible;
        z-index: 10;
        overflow-x: hidden;
        overflow-y: scroll;
        background: var(--sat-surface);
      }

      .standard-api-shell .standard-api-setting-inner {
        max-width: 800px;
      }

      .standard-api-shell .standard-api-setting-desc {
        font-size: 0.75rem;
      }

      .standard-api-shell .standard-api-setting-select-wide {
        width: 150px;
      }

      .standard-api-shell .standard-api-setting-input-narrow {
        width: 100px;
      }

      .standard-api-shell .standard-api-side-selector {
        width: 200px;
        min-width: 200px;
        background: var(--sat-surface-muted);
      }

      .standard-api-shell .standard-api-side-selector-item {
        cursor: pointer;
        color: var(--sat-text-soft);
      }

      .standard-api-shell .standard-api-side-selector-item.is-active {
        background: var(--sat-primary);
        color: #fff;
      }

      .standard-api-shell .standard-api-auth-form-wrap {
        max-width: 500px;
      }

      .standard-api-shell .standard-api-pane-gen-inner {
        overflow-x: hidden;
        overflow-y: scroll;
      }

      .standard-api-shell .standard-api-gen-textarea {
        background: var(--sat-surface-muted) !important;
        border: 1px solid var(--sat-border) !important;
      }

      .standard-api-shell .standard-api-body-root {
        min-width: 0;
      }

      .standard-api-shell .standard-api-body-pane {
        z-index: 10;
      }

      .standard-api-shell .standard-api-body-pane.is-hidden {
        visibility: hidden;
        z-index: 0;
      }

      .standard-api-shell .standard-api-body-modebar {
        min-width: 0;
      }

      .standard-api-shell .standard-api-body-highlighter {
        white-space: pre-wrap;
        word-wrap: break-word;
        overflow: hidden;
        color: transparent;
        pointer-events: none;
        background-color: transparent;
        z-index: 0;
      }

      .standard-api-shell .standard-api-body-raw-input {
        resize: vertical;
        outline: none;
        caret-color: var(--sat-text);
        z-index: 1;
        position: relative;
        min-height: 300px;
        overflow: hidden;
      }

      .standard-api-shell .standard-api-body-raw-input.is-json {
        color: transparent;
      }

      .standard-api-shell .standard-api-binary-pane {
        background: var(--sat-surface-muted);
      }

      .standard-api-shell .standard-api-body-empty {
        background: var(--sat-surface-muted);
      }

      .standard-api-shell .api-main-content { min-width: 0; }
      .standard-api-shell .api-request-config-content { overflow-x: hidden; }

      .standard-api-shell *::-webkit-scrollbar {
        width: 8px;
        height: 8px;
      }

      .standard-api-shell *::-webkit-scrollbar-track {
        background: color-mix(in srgb, var(--sat-surface-muted) 82%, transparent);
      }

      .standard-api-shell *::-webkit-scrollbar-thumb {
        background: color-mix(in srgb, var(--sat-text-soft) 35%, transparent);
        border-radius: 4px;
      }

      .standard-api-shell *::-webkit-scrollbar-thumb:hover {
        background: color-mix(in srgb, var(--sat-text-soft) 55%, transparent);
      }

      body.theme-dark .standard-api-shell .api-method-get { color: #34d399 !important; }
      body.theme-dark .standard-api-shell .api-method-post { color: #fbbf24 !important; }
      body.theme-dark .standard-api-shell .api-method-put { color: #a78bfa !important; }
      body.theme-dark .standard-api-shell .api-method-delete { color: #fb7185 !important; }

      @media (max-width: 1024px) {
        .standard-api-shell .standard-api-toolbar {
          height: auto !important;
          min-height: 50px;
          flex-wrap: wrap;
        }
      }
    `}</style>
  );
}
