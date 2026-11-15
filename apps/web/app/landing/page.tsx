import type { Metadata } from "next";
import { LandingView } from "@/components/landing";

export const metadata: Metadata = {
  title: "Mera — Next-Gen Multi-Agent Social Dispatch Ledger",
  description:
    "Speak as several identities at once. Approved in ten seconds from your phone via Telegram or WhatsApp.",
};

export default function LandingRoutePage() {
  return <LandingView />;
}
