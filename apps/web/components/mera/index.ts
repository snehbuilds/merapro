/* Signature and data components (docs/12 §3 F2). Primitives live in components/ui. */
export { AccountChip, AccountMark, AccountStack, type AccountChipProps, type AccountRef } from "./account-chip";
export { ApprovalProof, type ApprovalProofProps } from "./approval-proof";
export { CapabilityTags, capabilityTags, type CapabilityTag, type PlatformCapabilities } from "./capability-tags";
export { CharCounter, type CharCounterProps } from "./char-counter";
export { DataTable, dataTableColumns, dataTableFeatures, matrixCell, type DataTableColumnMeta, type DataTableProps } from "./data-table";
export { GroupDot, GroupLabel, type GroupDotProps } from "./group-dot";
export { PlatformPreview, type PlatformPreviewProps } from "./platform-preview";
export { PostSetCard, type PostSetCardProps } from "./post-set-card";
export {
  ResolvedSettingRow,
  ResolvedSettingsTable,
  RESOLUTION_RULE_LABEL,
  SETTING_LEVEL_LABEL,
  type ResolutionRule,
  type ResolvedSettingRowProps,
  type SettingLevel,
} from "./resolved-setting-row";
export { StaggerTimeline, formatOffset, type StaggerItem, type StaggerTimelineProps } from "./stagger-timeline";
export { Timeline, type TimelineItem, type TimelineProps } from "./timeline";
export { TwoPaneList, type TwoPaneListProps } from "./two-pane-list";
export { VariantDecisionCard, type DecisionActions, type VariantDecisionCardProps } from "./variant-decision-card";
export { VariantList, VariantRow, type VariantRowProps } from "./variant-row";
