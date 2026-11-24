import { UserCheck, KeyRound, Server, PlugZap } from "./icons";
import { Reveal } from "./Reveal";

export const principles = [
  { icon: UserCheck, t: "Human-approved publishing", d: "Nothing ships without an explicit sign-off." },
  { icon: KeyRound, t: "Account-bound signatures", d: "Approvals tied to one version, one account." },
  { icon: Server, t: "Self-hosted control", d: "Runs on your infrastructure, under your keys." },
  { icon: PlugZap, t: "Official APIs", d: "Publishing through official platform adapters." },
];

export function Principles() {
  return (
    <section aria-label="Product principles" className="border-y border-border bg-card">
      <div className="mx-auto grid max-w-7xl grid-cols-1 divide-y divide-border px-5 sm:grid-cols-2 sm:divide-y-0 sm:px-8 lg:grid-cols-4 lg:divide-x">
        {principles.map((p, i) => (
          <Reveal key={p.t} delay={i * 80} className="flex items-start gap-3 py-6 lg:px-6 lg:first:pl-0">
            <p.icon className="mt-0.5 h-5 w-5 shrink-0 text-primary" strokeWidth={1.6} />
            <div><p className="text-sm font-semibold text-ink">{p.t}</p><p className="mt-0.5 text-sm text-muted-foreground">{p.d}</p></div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

