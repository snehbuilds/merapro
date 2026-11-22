/*
 * Navigation model, ported from mera.js: NAV (rail groups, labels, icons,
 * counts) and SCREENS (command palette label + hint), plus the topbar title/sub
 * and primary action from each mockup's <header class="topbar">.
 */
import type { IconName } from "@/components/icons/paths";
import { features as defaultFeatures, type Feature } from "@/lib/demo";

export type ScreenId =
  | "dashboard"
  | "approvals"
  | "composer"
  | "calendar"
  | "inbox"
  | "analytics"
  | "sources"
  | "discovery"
  | "voice"
  | "library"
  | "accounts"
  | "groups"
  | "agents"
  | "automation"
  | "channels"
  | "mcp"
  | "team"
  | "audit"
  | "settings"
  | "docs";

export interface Screen {
  id: ScreenId;
  /** Rail label (mera.js NAV). */
  navLabel: string;
  icon: IconName;
  /** Topbar h1 (mockup data-title). */
  title: string;
  /** Topbar mono sub (mockup data-sub). Demo copy until real data exists. */
  sub?: string;
  /** Topbar primary action (mockup data-action). `target` links to a screen. */
  action?: { label: string; target?: ScreenId };
  /** Command palette label and hint (mera.js SCREENS). */
  paletteLabel: string;
  paletteHint: string;
  feature?: Feature;
}

export const SCREENS: Readonly<Record<ScreenId, Screen>> = {
  dashboard: {
    id: "dashboard",
    navLabel: "Dashboard",
    icon: "dashboard",
    title: "Dashboard",
    sub: "Mon 10 Nov",
    action: { label: "New post", target: "composer" },
    paletteLabel: "Dashboard",
    paletteHint: "Queue, upcoming, agent activity",
  },
  approvals: {
    id: "approvals",
    navLabel: "Approvals",
    icon: "approvals",
    title: "Approval Queue",
    sub: "7 posts across 3 sets · 1 tool call",
    paletteLabel: "Approval Queue",
    paletteHint: "Per-account decisions",
  },
  composer: {
    id: "composer",
    navLabel: "Composer",
    icon: "compose",
    title: "Composer",
    sub: "Draft · 4 accounts",
    paletteLabel: "Composer",
    paletteHint: "Target picker and per-account previews",
  },
  calendar: {
    id: "calendar",
    navLabel: "Calendar",
    icon: "calendar",
    title: "Content Calendar",
    sub: "10–16 Nov",
    action: { label: "New post", target: "composer" },
    paletteLabel: "Content Calendar",
    paletteHint: "Colour-coded by group",
  },
  inbox: {
    id: "inbox",
    navLabel: "Inbox",
    icon: "inbox",
    title: "Unified Inbox",
    paletteLabel: "Unified Inbox",
    paletteHint: "Comments across accounts",
    feature: "inbox",
  },
  analytics: {
    id: "analytics",
    navLabel: "Analytics",
    icon: "analytics",
    title: "Analytics",
    sub: "1–30 Nov",
    paletteLabel: "Analytics",
    paletteHint: "Account, group and workspace",
  },
  sources: {
    id: "sources",
    navLabel: "Sources",
    icon: "sources",
    title: "Sources",
    sub: "9 configured · 1 failing",
    action: { label: "Add source" },
    paletteLabel: "Sources",
    paletteHint: "Feeds and group links",
  },
  discovery: {
    id: "discovery",
    navLabel: "Discovery",
    icon: "discovery",
    title: "Content Discovery",
    sub: "Trending for Tech News",
    paletteLabel: "Content Discovery",
    paletteHint: "Trending, scored per group",
  },
  voice: {
    id: "voice",
    navLabel: "Skills & Profiles",
    icon: "voice",
    title: "Skills & Profiles",
    sub: "3 profiles",
    action: { label: "New profile" },
    paletteLabel: "Skills & Profiles",
    paletteHint: "Platform skills, who is posting",
  },
  library: {
    id: "library",
    navLabel: "Library",
    icon: "hash",
    title: "Library",
    sub: "Hashtags and templates",
    paletteLabel: "Library",
    paletteHint: "Hashtag registry, post templates",
  },
  accounts: {
    id: "accounts",
    navLabel: "Connected",
    icon: "accounts",
    title: "Connected Accounts",
    sub: "9 accounts · 5 platforms",
    action: { label: "Connect account" },
    paletteLabel: "Connected Accounts",
    paletteHint: "Health and reconnection",
  },
  groups: {
    id: "groups",
    navLabel: "Groups",
    icon: "groups",
    title: "Account Groups",
    sub: "3 groups · 9 accounts",
    action: { label: "New group" },
    paletteLabel: "Account Groups",
    paletteHint: "Membership and staggering",
  },
  agents: {
    id: "agents",
    navLabel: "Agents",
    icon: "agents",
    title: "Agents",
    sub: "11 agents · $1.84 today",
    paletteLabel: "Agents",
    paletteHint: "Configuration and run viewer",
  },
  automation: {
    id: "automation",
    navLabel: "Automation",
    icon: "automation",
    title: "Automation",
    sub: "Manual everywhere",
    paletteLabel: "Automation",
    paletteHint: "Autopilot and kill switch",
  },
  channels: {
    id: "channels",
    navLabel: "Channels",
    icon: "channels",
    title: "Approval Channels",
    sub: "Telegram connected",
    paletteLabel: "Approval Channels",
    paletteHint: "Telegram and WhatsApp",
  },
  mcp: {
    id: "mcp",
    navLabel: "MCP",
    icon: "mcp",
    title: "MCP Integrations",
    paletteLabel: "MCP Integrations",
    paletteHint: "External servers and allowlists",
    feature: "mcp",
  },
  team: {
    id: "team",
    navLabel: "Team",
    icon: "team",
    title: "Team & Roles",
    sub: "4 members",
    action: { label: "Invite" },
    paletteLabel: "Team & Roles",
    paletteHint: "Members and scopes",
  },
  audit: {
    id: "audit",
    navLabel: "Audit Log",
    icon: "audit",
    title: "Audit Log",
    sub: "Append-only · 2 year retention",
    paletteLabel: "Audit Log",
    paletteHint: "Every action, every actor",
  },
  settings: {
    id: "settings",
    navLabel: "Workspace settings",
    icon: "settings",
    title: "Settings",
    sub: "Acme workspace",
    paletteLabel: "Settings",
    paletteHint: "Workspace and usage",
  },
  docs: {
    id: "docs",
    navLabel: "Help & docs",
    icon: "hash",
    title: "Help & docs",
    paletteLabel: "Help & docs",
    paletteHint: "Guides for every workspace menu",
  },
};

/** Rail groups in mera.js NAV order. */
export const NAV_GROUPS: readonly { label: string; items: readonly ScreenId[] }[] = [
  { label: "Work", items: ["dashboard", "approvals", "composer", "calendar", "inbox", "analytics"] },
  { label: "Content", items: ["sources", "discovery", "voice", "library"] },
  { label: "Accounts", items: ["accounts", "groups"] },
  { label: "System", items: ["agents", "automation", "channels", "mcp", "team", "audit", "settings", "docs"] },
];

/** Command palette order, from mera.js SCREENS. */
export const PALETTE_ORDER: readonly ScreenId[] = [
  "dashboard",
  "approvals",
  "composer",
  "calendar",
  "inbox",
  "analytics",
  "accounts",
  "groups",
  "sources",
  "discovery",
  "agents",
  "voice",
  "library",
  "automation",
  "channels",
  "mcp",
  "team",
  "audit",
  "settings",
  "docs",
];

export type FeatureFlags = Readonly<Record<Feature, boolean>>;

export function isScreenEnabled(id: ScreenId, flags: FeatureFlags = defaultFeatures): boolean {
  const feature = SCREENS[id].feature;
  return feature ? flags[feature] : true;
}

export function visibleNav(flags: FeatureFlags = defaultFeatures) {
  return NAV_GROUPS.map((g) => ({
    label: g.label,
    items: g.items.filter((id) => isScreenEnabled(id, flags)).map((id) => SCREENS[id]),
  })).filter((g) => g.items.length > 0);
}

export function paletteScreens(flags: FeatureFlags = defaultFeatures): Screen[] {
  return PALETTE_ORDER.filter((id) => isScreenEnabled(id, flags)).map((id) => SCREENS[id]);
}

export function screenHref(workspace: string, id: ScreenId): string {
  return `/w/${workspace}/${id}`;
}

export function isScreenId(value: string): value is ScreenId {
  return Object.prototype.hasOwnProperty.call(SCREENS, value);
}

/** The screen a pathname like /w/acme/approvals/… belongs to, if any. */
export function screenFromPath(pathname: string): ScreenId | undefined {
  const seg = pathname.split("/")[3];
  return seg && isScreenId(seg) ? seg : undefined;
}
