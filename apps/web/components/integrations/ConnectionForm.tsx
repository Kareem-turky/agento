"use client";

// One generic form driven entirely by an integration definition's field metadata
// (text, URL, boolean, secret). Every value is rendered as plain text by React: no
// provider-supplied HTML is ever interpreted. Secret inputs are password inputs that
// always start EMPTY (never pre-filled) and are cleared after every submission.
import { useState, type FormEvent } from "react";
import type { ConfigFieldView, IntegrationConnectionView, IntegrationDefinitionView } from "../../lib/product-api/types";

export type ConnectionFormMode = "create" | "edit" | "credentials";

export type ConnectionFormSubmission = {
  displayName: string;
  config: Record<string, string | boolean>;
  credentials: Record<string, string>;
};

const MAX_DISPLAY_NAME = 120;
const MAX_VALUE = 2048;

function initialValues(fields: ConfigFieldView[], connection?: IntegrationConnectionView): Record<string, string | boolean> {
  const values: Record<string, string | boolean> = {};
  for (const field of fields) {
    if (field.kind === "secret") continue; // never pre-filled, never held here
    const existing = connection?.config[field.name];
    values[field.name] = field.kind === "boolean" ? existing === true : typeof existing === "string" ? existing : "";
  }
  return values;
}

export function ConnectionForm({
  definition,
  mode,
  connection,
  busy,
  onSubmit,
  onCancel,
}: {
  definition: IntegrationDefinitionView;
  mode: ConnectionFormMode;
  connection?: IntegrationConnectionView;
  busy: boolean;
  onSubmit: (submission: ConnectionFormSubmission) => Promise<boolean>;
  onCancel: () => void;
}) {
  const configFields = definition.fields.filter((f) => f.kind !== "secret");
  const secretFields = definition.fields.filter((f) => f.kind === "secret");
  const showConfig = mode !== "credentials";
  const showSecrets = mode !== "edit";
  const [displayName, setDisplayName] = useState(connection?.display_name ?? "");
  const [values, setValues] = useState(() => initialValues(configFields, connection));
  const [secrets, setSecrets] = useState<Record<string, string>>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    const config: Record<string, string | boolean> = {};
    if (showConfig) {
      for (const field of configFields) {
        const value = values[field.name];
        if (typeof value === "boolean") config[field.name] = value;
        else if (value.trim() !== "") config[field.name] = value.trim(); // blank optional: omitted
      }
    }
    const credentials: Record<string, string> = {};
    if (showSecrets) {
      for (const field of secretFields) {
        const value = secrets[field.name] ?? "";
        if (value !== "") credentials[field.name] = value;
      }
    }
    const submission = { displayName: displayName.trim(), config, credentials };
    setSecrets({}); // the values leave this form's memory with the one request
    await onSubmit(submission);
  }

  const title = mode === "create" ? `Connect ${definition.name}`
    : mode === "edit" ? "Edit name and settings" : "Replace credentials";

  return (
    <form className="form integration-form" onSubmit={submit} autoComplete="off" aria-label={title}>
      <p className="form__context"><strong>{title}</strong></p>
      {mode === "credentials" ? (
        <p className="form__context">
          Enter the complete new set. The current credentials stay in use until the replacement succeeds.
        </p>
      ) : null}
      {mode === "edit" && secretFields.length > 0 ? (
        <p className="form__context">Saving settings never changes or clears stored credentials.</p>
      ) : null}

      {showConfig ? (
        <label className="field">
          <span className="field__label">Connection name</span>
          <input
            name="display_name"
            value={displayName}
            maxLength={MAX_DISPLAY_NAME}
            required
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </label>
      ) : null}

      {showConfig ? configFields.map((field) => (
        field.kind === "boolean" ? (
          <label className="field field--checkbox" key={field.name}>
            <span>
              <input
                type="checkbox"
                name={field.name}
                checked={values[field.name] === true}
                onChange={(event) => setValues({ ...values, [field.name]: event.target.checked })}
              />{" "}
              <span className="field__label">{field.label}</span>
            </span>
            {field.help_text ? <span className="field__hint">{field.help_text}</span> : null}
          </label>
        ) : (
          <label className="field" key={field.name}>
            <span className="field__label">{field.label}{field.required ? " *" : ""}</span>
            <input
              type={field.kind === "url" ? "url" : "text"}
              name={field.name}
              spellCheck={false}
              maxLength={MAX_VALUE}
              required={field.required}
              value={String(values[field.name] ?? "")}
              onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
            />
            {field.help_text ? <span className="field__hint">{field.help_text}</span> : null}
          </label>
        )
      )) : null}

      {showSecrets ? secretFields.map((field) => (
        <label className="field" key={field.name}>
          <span className="field__label">{field.label}{field.required ? " *" : ""}</span>
          <input
            type="password"
            name={`secret-${field.name}`}
            autoComplete="new-password"
            spellCheck={false}
            maxLength={MAX_VALUE}
            required={field.required}
            value={secrets[field.name] ?? ""}
            onChange={(event) => setSecrets({ ...secrets, [field.name]: event.target.value })}
          />
          <span className="field__hint">
            {field.help_text ? `${field.help_text} ` : ""}Write-only: it is never shown again.
          </span>
        </label>
      )) : null}

      <div className="form__actions">
        <button type="submit" className="button" disabled={busy}>
          {mode === "create" ? "Create connection" : mode === "edit" ? "Save settings" : "Replace credentials"}
        </button>
        <button type="button" className="button button--ghost" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}
