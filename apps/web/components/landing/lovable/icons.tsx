import type { ComponentType, SVGProps } from "react";
import { Icon, type IconProps } from "@/components/icons";

export type LucideIcon = ComponentType<SVGProps<SVGSVGElement>>;

function icon(name: IconProps["name"]): LucideIcon {
  function LovableIcon(props: SVGProps<SVGSVGElement>) {
    return <Icon {...props} name={name} />;
  }
  LovableIcon.displayName = `LovableIcon(${name})`;
  return LovableIcon;
}

export const ArrowRight = icon("arrow");
export const BarChart3 = icon("analytics");
export const Box = icon("dashboard");
export const CalendarClock = icon("calendar");
export const Check = icon("check");
export const ClipboardCheck = icon("approvals");
export const Database = icon("sources");
export const FileSearch = icon("search");
export const Hash = icon("hash");
export const ImageIcon = icon("image");
export const KeyRound = icon("settings");
export const Menu = icon("menu");
export const MessageSquareCheck = icon("channels");
export const MessagesSquare = icon("inbox");
export const Newspaper = icon("discovery");
export const PenLine = icon("edit");
export const Plug = icon("mcp");
export const PlugZap = icon("bolt");
export const Power = icon("power");
export const RotateCcw = icon("refresh");
export const Search = icon("search");
export const Send = icon("arrow");
export const Server = icon("automation");
export const ShieldCheck = icon("check");
export const ShieldX = icon("x");
export const Shuffle = icon("stagger");
export const UserCheck = icon("team");
export const Workflow = icon("agents");
export const X = icon("x");
