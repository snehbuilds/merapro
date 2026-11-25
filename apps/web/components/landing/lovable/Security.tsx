import { Database, Plug, Power, Box } from "./icons";
import { Reveal, SectionHeading } from "./Reveal";

const items = [
  { icon: Database, t: "PostgreSQL Row-Level Security", d: "Workspace isolation is enforced in the database itself, so one tenant's queries cannot read another's rows — even if application code has a bug." },
  { icon: Plug, t: "MCP integrations", d: "Agents reach tools and data through Model Context Protocol servers with explicit, scoped access instead of ad-hoc credentials." },
  { icon: Power, t: "Three-tier kill switch", d: "Halt publishing for a whole workspace, a single account, or an account group instantly. Pending jobs stop; nothing drains through." },
  { icon: Box, t: "Deterministic sandbox", d: "The publisher runs with zero LLM involvement. Same verified input, same API call — no generation at the moment of publishing." },
];

export function Security() {
  return (
    <section id="security" className="mx-auto max-w-7xl px-5 py-24 sm:px-8 lg:py-32">
      <SectionHeading eyebrow="Security & governance" title={<>Self-hosted, and <span className="text-primary">yours to stop.</span></>}
        body="Talonic runs on infrastructure you control. Governance is built into the data layer and the publishing path, not bolted on." />
      <div className="mt-14 grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2">
        {items.map((it, i) => (
          <Reveal key={it.t} delay={i * 90} className="group bg-card p-7 transition-colors hover:bg-surface sm:p-9">
            <it.icon className="h-6 w-6 text-primary transition-transform group-hover:-translate-y-0.5" strokeWidth={1.5} />
            <h3 className="mt-6 text-lg font-semibold text-ink">{it.t}</h3>
            <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">{it.d}</p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

