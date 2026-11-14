"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";

interface LlmSettingsState {
  sandbox_llm: boolean;
  has_openai_key: boolean;
  openai_key_preview: string | null;
  has_anthropic_key: boolean;
  anthropic_key_preview: string | null;
  has_gemini_key: boolean;
  gemini_key_preview: string | null;
  has_openrouter_key: boolean;
  openrouter_key_preview: string | null;
  has_groq_key: boolean;
  groq_key_preview: string | null;
  custom_api_base: string | null;
  has_custom_key: boolean;
  custom_key_preview: string | null;
}

export function LLMSettingsPanel({
  workspaceId,
  canEdit,
}: {
  workspaceId: string;
  canEdit: boolean;
}) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [settings, setSettings] = useState<LlmSettingsState>({
    sandbox_llm: true,
    has_openai_key: false,
    openai_key_preview: null,
    has_anthropic_key: false,
    anthropic_key_preview: null,
    has_gemini_key: false,
    gemini_key_preview: null,
    has_openrouter_key: false,
    openrouter_key_preview: null,
    has_groq_key: false,
    groq_key_preview: null,
    custom_api_base: null,
    has_custom_key: false,
    custom_key_preview: null,
  });

  const [openaiKey, setOpenaiKey] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [openrouterKey, setOpenrouterKey] = useState("");
  const [customBase, setCustomBase] = useState("");
  const [customKey, setCustomKey] = useState("");
  const [sandboxLlm, setSandboxLlm] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`/api/mera/workspaces/${workspaceId}/llm-settings`, {
          credentials: "include",
          headers: {
            "X-Mera-Client": "web",
          },
        });
        if (res.ok) {
          const data: LlmSettingsState = await res.json();
          setSettings(data);
          setSandboxLlm(data.sandbox_llm);
          setCustomBase(data.custom_api_base ?? "");
        }
      } catch (e) {
        // Fallback silently if fetch fails
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [workspaceId]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!canEdit) return;

    setSaving(true);
    setError(null);
    setSuccess(false);

    try {
      const payload: Record<string, unknown> = {
        sandbox_llm: sandboxLlm,
      };
      if (openaiKey.trim()) payload.openai_api_key = openaiKey.trim();
      if (anthropicKey.trim()) payload.anthropic_api_key = anthropicKey.trim();
      if (geminiKey.trim()) payload.gemini_api_key = geminiKey.trim();
      if (openrouterKey.trim()) payload.openrouter_api_key = openrouterKey.trim();
      if (customBase.trim() !== (settings.custom_api_base ?? "")) {
        payload.custom_api_base = customBase.trim();
      }
      if (customKey.trim()) payload.custom_api_key = customKey.trim();

      const res = await fetch(`/api/mera/workspaces/${workspaceId}/llm-settings`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-Mera-Client": "web",
        },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => null);
        throw new Error(errJson?.detail || "Failed to update LLM settings");
      }

      const updated: LlmSettingsState = await res.json();
      setSettings(updated);
      setSandboxLlm(updated.sandbox_llm);
      setOpenaiKey("");
      setAnthropicKey("");
      setGeminiKey("");
      setOpenrouterKey("");
      setCustomKey("");
      setSuccess(true);
      setTimeout(() => setSuccess(false), 4000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel>
      <PanelHead
        title="AI & LLM Model Providers"
        actions={
          <StatusPill
            tone={settings.sandbox_llm ? "warn" : "ok"}
            label={settings.sandbox_llm ? "Sandbox Mode (Mock LLM)" : "Live LLMs Enabled"}
          />
        }
      />
      <PanelBody className="grid gap-5">
        <p className="text-sm text-fg-muted">
          Configure API keys for real AI providers (OpenAI, Anthropic Claude, Google Gemini, OpenRouter, or local/custom
          OpenAI-compatible models like Ollama). Individual models for each agent (Writer, Editor, Research, etc.) can be
          selected in the <strong>Agents</strong> screen.
        </p>

        {error ? <Notice tone="danger">{error}</Notice> : null}
        {success ? <Notice tone="info">LLM settings saved successfully! Changes are active immediately.</Notice> : null}

        <form onSubmit={handleSave} className="grid gap-4">
          <div className="flex items-center justify-between rounded-md border border-border-subtle bg-surface-muted p-3.5">
            <div>
              <span className="block text-sm font-semibold text-fg">LLM Sandbox Mode</span>
              <span className="block text-xs text-fg-subtle">
                When enabled, drafting uses offline simulated templates without calling real API keys. Turn off to generate
                posts with real models.
              </span>
            </div>
            <label className="relative inline-flex cursor-pointer items-center">
              <input
                type="checkbox"
                checked={!sandboxLlm}
                disabled={!canEdit || saving}
                onChange={(e) => setSandboxLlm(!e.target.checked)}
                className="peer sr-only"
              />
              <div className="peer h-6 w-11 rounded-full bg-border-strong peer-checked:bg-success peer-focus:outline-none after:absolute after:left-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:border after:border-border after:bg-surface after:transition-all after:content-[''] peer-checked:after:translate-x-full peer-checked:after:border-surface"></div>
              <span className="ml-2.5 text-xs font-medium text-fg">
                {!sandboxLlm ? "Live Providers" : "Offline Sandbox"}
              </span>
            </label>
          </div>

          <div className="grid grid-cols-2 gap-4 max-wide:grid-cols-1">
            {/* Google Gemini */}
            <div className="rounded-md border border-border-subtle p-3.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-fg">Google Gemini</span>
                {settings.has_gemini_key ? (
                  <StatusPill tone="ok" label={`Active (${settings.gemini_key_preview})`} />
                ) : (
                  <StatusPill tone="neutral" label="Not configured" />
                )}
              </div>
              <Field label="Gemini API Key" hint="Supports gemini/gemini-2.0-flash, gemini-1.5-pro, etc.">
                <Input
                  type="password"
                  placeholder={settings.has_gemini_key ? "Enter new key to replace..." : "AIzaSy..."}
                  value={geminiKey}
                  onChange={(e) => setGeminiKey(e.target.value)}
                  disabled={!canEdit || saving}
                  className="font-mono text-xs"
                />
              </Field>
            </div>

            {/* OpenRouter / Any OpenAI-Compatible */}
            <div className="rounded-md border border-border-subtle p-3.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-fg">OpenRouter</span>
                {settings.has_openrouter_key ? (
                  <StatusPill tone="ok" label={`Active (${settings.openrouter_key_preview})`} />
                ) : (
                  <StatusPill tone="neutral" label="Not configured" />
                )}
              </div>
              <Field label="OpenRouter API Key" hint="Access Claude 3.5, DeepSeek R1/V3, Llama 3.3 with 1 key">
                <Input
                  type="password"
                  placeholder={settings.has_openrouter_key ? "Enter new key to replace..." : "sk-or-v1-..."}
                  value={openrouterKey}
                  onChange={(e) => setOpenrouterKey(e.target.value)}
                  disabled={!canEdit || saving}
                  className="font-mono text-xs"
                />
              </Field>
            </div>

            {/* OpenAI */}
            <div className="rounded-md border border-border-subtle p-3.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-fg">OpenAI</span>
                {settings.has_openai_key ? (
                  <StatusPill tone="ok" label={`Active (${settings.openai_key_preview})`} />
                ) : (
                  <StatusPill tone="neutral" label="Not configured" />
                )}
              </div>
              <Field label="OpenAI API Key" hint="For GPT-4o, GPT-4o-mini, o3-mini, and text embeddings">
                <Input
                  type="password"
                  placeholder={settings.has_openai_key ? "Enter new key to replace..." : "sk-proj-..."}
                  value={openaiKey}
                  onChange={(e) => setOpenaiKey(e.target.value)}
                  disabled={!canEdit || saving}
                  className="font-mono text-xs"
                />
              </Field>
            </div>

            {/* Anthropic */}
            <div className="rounded-md border border-border-subtle p-3.5">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-semibold text-fg">Anthropic (Claude)</span>
                {settings.has_anthropic_key ? (
                  <StatusPill tone="ok" label={`Active (${settings.anthropic_key_preview})`} />
                ) : (
                  <StatusPill tone="neutral" label="Not configured" />
                )}
              </div>
              <Field label="Anthropic API Key" hint="For Claude 3.5 Sonnet, Claude 3.5 Haiku, etc.">
                <Input
                  type="password"
                  placeholder={settings.has_anthropic_key ? "Enter new key to replace..." : "sk-ant-..."}
                  value={anthropicKey}
                  onChange={(e) => setAnthropicKey(e.target.value)}
                  disabled={!canEdit || saving}
                  className="font-mono text-xs"
                />
              </Field>
            </div>
          </div>

          {/* Custom OpenAI-Compatible / Local Endpoint */}
          <div className="rounded-md border border-border-subtle p-3.5">
            <span className="mb-1 block text-sm font-semibold text-fg">Custom OpenAI-Compatible Endpoint</span>
            <p className="mb-3 text-xs text-fg-subtle">
              Point to a self-hosted Ollama (`http://localhost:11434/v1`), vLLM, LocalAI, or custom OpenAI proxy.
            </p>
            <div className="grid grid-cols-2 gap-4 max-wide:grid-cols-1">
              <Field label="Custom Base URL" hint="e.g. http://localhost:11434/v1 or https://openrouter.ai/api/v1">
                <Input
                  type="text"
                  placeholder="https://api.openai.com/v1"
                  value={customBase}
                  onChange={(e) => setCustomBase(e.target.value)}
                  disabled={!canEdit || saving}
                  className="font-mono text-xs"
                />
              </Field>
              <Field label="Custom API Key (Optional)" hint="Optional if your local proxy requires no authentication">
                <Input
                  type="password"
                  placeholder={settings.has_custom_key ? `Configured (${settings.custom_key_preview})` : "Optional key..."}
                  value={customKey}
                  onChange={(e) => setCustomKey(e.target.value)}
                  disabled={!canEdit || saving}
                  className="font-mono text-xs"
                />
              </Field>
            </div>
          </div>

          {canEdit ? (
            <div className="flex justify-end pt-2">
              <Button type="submit" variant="primary" disabled={saving || loading}>
                {saving ? "Saving Settings…" : "Save LLM Settings"}
              </Button>
            </div>
          ) : null}
        </form>
      </PanelBody>
      <PanelFoot>
        API keys are stored safely and routed via LiteLLM. Models configured on the <strong>Agents</strong> screen will
        automatically use these keys.
      </PanelFoot>
    </Panel>
  );
}
