// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataGallery, PrimitivesGallery, SignatureGallery } from "@/app/dev/components/gallery";
import { CharCounter } from "@/components/mera/char-counter";
import { DataTable, dataTableColumns } from "@/components/mera/data-table";
import { PostSetCard } from "@/components/mera/post-set-card";
import { VariantRow } from "@/components/mera/variant-row";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { StatusPill } from "@/components/ui/status-pill";
import { Switch } from "@/components/ui/switch";

afterEach(cleanup);

describe("SegmentedControl", () => {
  const options = [
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
    { value: "system", label: "System" },
  ];

  it("is a radio group with one tab stop", () => {
    render(<SegmentedControl aria-label="Theme" options={options} defaultValue="dark" />);
    const group = screen.getByRole("radiogroup", { name: "Theme" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
    expect(radios.map((r) => r.tabIndex)).toEqual([-1, 0, -1]);
  });

  it("moves selection and focus with arrow keys, wrapping, and Home / End", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(<SegmentedControl aria-label="Theme" options={options} onValueChange={onValueChange} />);
    const [light, dark, system] = screen.getAllByRole("radio");

    await user.tab();
    expect(light).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(dark).toHaveFocus();
    expect(dark).toHaveAttribute("aria-checked", "true");
    expect(onValueChange).toHaveBeenLastCalledWith("dark");

    await user.keyboard("{ArrowDown}");
    expect(system).toHaveAttribute("aria-checked", "true");

    await user.keyboard("{ArrowRight}"); // wraps
    expect(light).toHaveFocus();
    expect(light).toHaveAttribute("aria-checked", "true");

    await user.keyboard("{ArrowLeft}"); // wraps back
    expect(system).toHaveAttribute("aria-checked", "true");

    await user.keyboard("{Home}");
    expect(light).toHaveAttribute("aria-checked", "true");
    await user.keyboard("{End}");
    expect(system).toHaveFocus();
    expect(system!.tabIndex).toBe(0);
  });
});

describe("Switch", () => {
  it("toggles aria-checked on click and with the keyboard", async () => {
    const user = userEvent.setup();
    const onCheckedChange = vi.fn();
    render(<Switch aria-label="Approval requests" onCheckedChange={onCheckedChange} />);
    const sw = screen.getByRole("switch", { name: "Approval requests" });
    expect(sw).toHaveAttribute("aria-checked", "false");

    await user.click(sw);
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(onCheckedChange).toHaveBeenLastCalledWith(true);

    sw.focus();
    await user.keyboard(" ");
    expect(sw).toHaveAttribute("aria-checked", "false");
    await user.keyboard("{Enter}");
    expect(sw).toHaveAttribute("aria-checked", "true");
  });

  it("stays put when controlled", async () => {
    const user = userEvent.setup();
    render(<Switch aria-label="Locked" checked={false} />);
    const sw = screen.getByRole("switch");
    await user.click(sw);
    expect(sw).toHaveAttribute("aria-checked", "false");
  });
});

describe("PostSetCard", () => {
  it("is an accessible disclosure for its variants", async () => {
    const user = userEvent.setup();
    render(
      <PostSetCard
        title="Postgres 18 ships async I/O"
        group={{ key: 0, name: "Tech News" }}
        accountCount={4}
        source="postgresql.org"
        stagger={{ start: "2026-11-09T17:00:00Z", end: "2026-11-09T17:13:00Z", tz: "America/Los_Angeles" }}
        expiry={{ at: "2026-11-09T09:00:00Z", now: "2026-11-08T21:18:00Z" }}
      >
        <VariantRow
          account={{ platform: "linkedin", name: "Work LinkedIn" }}
          excerpt="Postgres 18 ships async I/O"
          confidence={0.84}
          state="needs_approval"
        />
      </PostSetCard>,
    );
    const button = screen.getByRole("button", { name: "Accounts in Postgres 18 ships async I/O" });
    expect(button).toHaveAttribute("aria-expanded", "true");
    const body = document.getElementById(button.getAttribute("aria-controls")!);
    expect(body).not.toBeNull();
    expect(body).toBeVisible();
    expect(screen.getByText("09:00–09:13")).toBeInTheDocument();
    expect(screen.getByText("Expires in 11h 42m")).toBeInTheDocument();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(body).not.toBeVisible();

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("marks danger urgency under two hours", () => {
    render(
      <PostSetCard
        title="We're hiring"
        group={{ key: 1, name: "Company Official" }}
        accountCount={3}
        expiry={{ at: "2026-11-08T23:12:00Z", now: "2026-11-08T21:18:00Z" }}
      />,
    );
    const pill = screen.getByText("Expires in 1h 54m");
    expect(pill).toHaveAttribute("data-urgency", "danger");
    expect(pill).toHaveAttribute("data-shape", "diamond");
  });
});

describe("VariantRow off state", () => {
  it("strikes the reason, says so to screen readers, and reads Skipped", () => {
    render(
      <VariantRow
        account={{ platform: "x", name: "X @tech" }}
        off={{ reason: "Disabled — too close to @main's take" }}
      />,
    );
    expect(screen.getByText("Off:")).toHaveClass("sr-only");
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });
});

describe("CharCounter", () => {
  it("counts graphemes and flags the over state beyond colour", () => {
    const { rerender, container } = render(<CharCounter text="👍🏽é" limit={3} />);
    expect(container.textContent).toBe("2 / 3");
    rerender(<CharCounter text={"👍🏽🇳🇿é👩‍💻"} limit={3} />);
    const el = container.querySelector("[data-slot=char-counter]")!;
    expect(el).toHaveAttribute("data-over", "true");
    expect(el.textContent).toContain("4 / 3");
    expect(el.textContent).toContain("1 over the limit");
  });
});

describe("StatusPill", () => {
  it("looks up kind + state", () => {
    render(<StatusPill kind="scheduled_post" state="dead_lettered" />);
    const pill = screen.getByText("Dead letter");
    expect(pill).toHaveAttribute("data-tone", "danger");
    expect(pill).toHaveAttribute("data-state", "scheduled_post:dead_lettered");
  });
});

describe("DataTable", () => {
  interface Row {
    name: string;
    published: number;
  }
  const h = dataTableColumns<Row>();
  const columns = h.columns([
    h.accessor("name", { header: "Account", enableSorting: false }),
    h.accessor("published", { header: "Published", meta: { align: "end" } }),
  ]);
  const data: Row[] = [
    { name: "X @main", published: 64 },
    { name: "Work LinkedIn", published: 38 },
    { name: "X @tech", published: 52 },
  ];

  it("sorts with header buttons and reports aria-sort", async () => {
    const user = userEvent.setup();
    render(<DataTable caption="Accounts" columns={columns} data={data} />);
    const headers = screen.getAllByRole("columnheader");
    expect(headers[0]).not.toHaveAttribute("aria-sort");
    expect(headers[1]).toHaveAttribute("aria-sort", "none");
    expect(headers[1]).toHaveClass("text-right");

    await user.click(within(headers[1]!).getByRole("button", { name: /Published/ }));
    const sorted = headers[1]!.getAttribute("aria-sort");
    expect(["ascending", "descending"]).toContain(sorted);
    const values = screen
      .getAllByRole("row")
      .slice(1)
      .map((r) => within(r).getAllByRole("cell")[1]!.textContent);
    expect(values).toEqual(sorted === "ascending" ? ["38", "52", "64"] : ["64", "52", "38"]);

    expect(screen.getByRole("region", { name: "Accounts" })).toHaveAttribute("tabindex", "0");
  });
});

describe("accessibility (axe) over the gallery", () => {
  const sections = [
    ["primitives", PrimitivesGallery],
    ["signature", SignatureGallery],
    ["data", DataGallery],
  ] as const;

  it.each(sections)("%s has no serious or critical violations", async (_name, Section) => {
    const { container } = render(
      <main>
        <h1>Gallery</h1>
        <Section />
      </main>,
    );
    const results = await axe.run(container, {
      // jsdom does no layout or painting; contrast is checked against mera.css ratios instead.
      rules: { "color-contrast": { enabled: false } },
    });
    const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(
      blocking.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`),
    ).toEqual([]);
  });
});
