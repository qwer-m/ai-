import { ResponsePanelShell } from "./ResponsePanelShell";
import type { ResponsePanelProps } from "./ResponsePanel.types";

export type { ResponsePanelProps } from "./ResponsePanel.types";

export function ResponsePanel(props: ResponsePanelProps) {
  return <ResponsePanelShell {...props} />;
}
