import type { Metadata } from "next";
import { LandingView } from "@/components/landing";

export const metadata: Metadata = {
  title: { absolute: "Talonic — One story. Every account. A voice of its own." },
  description:
    "Self-hosted multi-agent social publishing: research stories, write distinct posts per account, and publish only after signed human approval.",
};

export default function LandingRoutePage() {
  return <LandingView />;
}
