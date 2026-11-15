import { LandingHeader } from "./landing-header";
import { HeroSection } from "./hero-section";
import { DispatchLedgerShowcase } from "./dispatch-ledger-showcase";
import { ApprovalIntegritySection } from "./approval-integrity-section";
import { AgentPipelineSection } from "./agent-pipeline-section";
import { StaggerTimelineSection } from "./stagger-timeline-section";
import { McpGovernanceSection } from "./mcp-governance-section";
import { LandingFooter } from "./landing-footer";

export function LandingView() {
  return (
    <div className="min-h-screen bg-[#060709] text-white selection:bg-emerald-500/30 selection:text-white">
      <LandingHeader />
      <main>
        <HeroSection />
        <DispatchLedgerShowcase />
        <ApprovalIntegritySection />
        <AgentPipelineSection />
        <StaggerTimelineSection />
        <McpGovernanceSection />
      </main>
      <LandingFooter />
    </div>
  );
}
