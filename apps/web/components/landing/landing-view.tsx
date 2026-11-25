import { Agents } from "./lovable/Agents";
import { FinalCta, Footer } from "./lovable/Closing";
import { Distribution } from "./lovable/Distribution";
import { Hero } from "./lovable/Hero";
import { Integrity } from "./lovable/Integrity";
import { Nav } from "./lovable/Nav";
import { Principles } from "./lovable/Principles";
import { Security } from "./lovable/Security";
import { Voices } from "./lovable/Voices";

export function LandingView() {
  return (
    <div className="talonic-landing min-h-screen bg-background text-foreground">
      <a href="#product" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-60 focus:rounded-md focus:bg-card focus:px-3 focus:py-2">
        Skip to content
      </a>
      <Nav />
      <main>
        <Hero />
        <Principles />
        <Voices />
        <Integrity />
        <Agents />
        <Distribution />
        <Security />
        <FinalCta />
      </main>
      <Footer />
    </div>
  );
}
