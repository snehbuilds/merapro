// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StepUpPrompt } from "@/components/mera/step-up";

afterEach(cleanup);

describe("StepUpPrompt", () => {
  it("offers both providers until a proof arrives", () => {
    const { rerender } = render(<StepUpPrompt state={{ proof: null, failed: false }} purpose="delete your account" />);
    expect(screen.getByRole("link", { name: "Confirm with Google" }).getAttribute("href")).toMatch(/step_up=1/);
    expect(screen.getByRole("link", { name: "Confirm with Microsoft" })).toBeTruthy();
    rerender(<StepUpPrompt state={{ proof: "mera_ra_x", failed: false }} purpose="delete your account" />);
    expect(screen.getByText(/You have a few minutes to delete your account/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Confirm with Google" })).toBeNull();
  });

  it("says when the confirmation failed", () => {
    render(<StepUpPrompt state={{ proof: null, failed: true }} purpose="change your PIN" />);
    expect(screen.getByRole("alert").textContent).toMatch(/did not work/);
  });
});
