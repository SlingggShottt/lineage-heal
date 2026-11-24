import { FAULT_CLASS_LABELS, type FaultClass } from "@/features/incidents/labels";

/** A runbook's fault class in words; the API sends a plain string, so an unknown one shows as is. */
export function faultClassLabel(value: string): string {
  return value in FAULT_CLASS_LABELS ? FAULT_CLASS_LABELS[value as FaultClass] : value;
}
